"""Estimating a node's thermal resistance from passive observation.

The quantity worth tracking on a fanless unit is not temperature but thermal
resistance:

    T_die = T_ambient + P · θ            θ in °C/W

θ is what a drying thermal pad changes. Temperature is not: a node reading
78 °C in July and 62 °C in November may have identical θ, and two nodes in the
same condition read differently purely because one stands in the sun.

Measuring θ the obvious way — (T − T_ambient)/P at one instant — fails twice
over. There is no trustworthy T_ambient (a dark enclosure in direct sun sits
tens of degrees above any weather station), and a 25×25 cm heatsink has a time
constant of tens of minutes, so a load that changed ten minutes ago has not
settled and the ratio is biased.

Fitting the *dynamics* solves both. For a first-order lumped system

    dT/dt = ( P·θ − (T − T_amb) ) / τ

the exact solution over a step Δt of constant power is linear in its
parameters — no Euler approximation, so no discretisation bias:

    T[k+1]  =  A·T[k]  +  B·P[k]  +  C  +  D·(k − k̄)

    with A = exp(−Δt/τ),  B = (1−A)·θ,  C = (1−A)·T_amb

The drift term absorbs an ambient that ramps slowly across the window. The
coefficients give back all three physical quantities:

    τ      = −Δt / ln A
    θ      = B / (1 − A)
    T_amb  = C / (1 − A)

θ is a *ratio of fitted coefficients*, so it needs no external ambient reading
at all — the fit infers the ambient the observations imply. That is the whole
point of doing it this way.

Two traps, both found the hard way and both handled here.

**Errors in variables.** T[k] is a regressor *and* carries sensor noise, and
that same noise sits in the target T[k+1]. Ordinary least squares reads the
induced correlation as fast decay: A is pulled towards zero and θ down with
it. The effect is not subtle. At a 60 s step the die moves a couple of tenths
of a degree while the sensor quantises to 0.1 °C and wanders more than that,
and OLS lands about 37 % low. The fix is instrumental variables — T[k−1]
instruments for T[k], since its measurement noise is independent of both the
regressor's and the target's. Same data, same cost, bias gone: on synthetic
systems at realistic noise, OLS returns 0.95 against a true 1.5 while IV
returns 1.52.

**Averaging before fitting does not help — it hurts.** Block-averaging samples
into coarser bins looks like an obvious way to beat the noise down, and it
biases θ badly (1.5 → 1.0 at five-sample bins, worse with more data). The
input and the output are not attenuated equally: T is already a low-passed
version of P, so averaging costs it more, the apparent gain collapses, and θ
collapses with it. `decimate` remains available for storage rollups; it must
not be used ahead of a fit.

Pure arithmetic, no dependencies, no I/O — a four-parameter solve over a couple
of hundred points does not justify pulling in a numeric stack.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Sequence

#: Below this the CPU power is too flat to separate θ from the intercept.
#: Expressed as interquartile range over median, so it is scale-free.
MIN_EXCITATION = 0.15

#: A window shorter than this cannot pin down a time constant of tens of
#: minutes, whatever the arithmetic says. At the collector's 60 s step this is
#: one hour; three to six hours is the intended window.
MIN_SAMPLES = 60

#: Physically plausible bounds. A fit outside them is a numerical artefact, not
#: a discovery — reject rather than publish.
TAU_BOUNDS_S = (60.0, 4 * 3600.0)
THETA_BOUNDS_C_PER_W = (0.05, 20.0)

#: Natural-convection exponent: the heat transfer coefficient of a free
#: vertical surface scales roughly as ΔT^0.25, so θ scales as ΔT^-0.25. Used to
#: normalise θ back to reference conditions. Should be re-fitted per node from
#: its own record; this is only the textbook starting value.
CONVECTION_EXPONENT = 0.25

#: The ΔT (sink above ambient) that normalised θ is quoted at.
REFERENCE_DELTA_T = 20.0

#: Huber tuning constant, in robust standard deviations.
_HUBER_K = 1.345


class Rejection(str, Enum):
    """Why a window produced no usable θ."""

    OK = "OK"
    TOO_FEW_SAMPLES = "TOO_FEW_SAMPLES"
    #: CPU power was effectively constant — θ is not identifiable.
    NO_EXCITATION = "NO_EXCITATION"
    #: Thermal throttling occurred; the system left its linear regime.
    THROTTLED = "THROTTLED"
    #: The instrument matrix was singular or near-singular.
    SINGULAR = "SINGULAR"
    #: A outside (0, 1): the fit describes a system that does not settle.
    UNSTABLE = "UNSTABLE"
    IMPLAUSIBLE_TAU = "IMPLAUSIBLE_TAU"
    IMPLAUSIBLE_THETA = "IMPLAUSIBLE_THETA"


@dataclass(frozen=True)
class Sample:
    """One observation. `power_w` is package power, `temp_c` die temperature."""

    timestamp: float
    power_w: float
    temp_c: float
    throttled: bool = False


@dataclass(frozen=True)
class ThermalFit:
    rejection: Rejection
    n_samples: int = 0
    excitation: float = 0.0

    theta_c_per_w: Optional[float] = None
    tau_seconds: Optional[float] = None
    t_ambient_c: Optional[float] = None
    #: Share of the step-to-step temperature *change* the model explains.
    r_squared: Optional[float] = None
    #: Mean die temperature over the window, for the normalisation step.
    mean_temp_c: Optional[float] = None
    coefficients: tuple = field(default=())

    @property
    def ok(self) -> bool:
        return self.rejection is Rejection.OK


def power_watts(
    previous_uj: int,
    current_uj: int,
    dt_seconds: float,
    max_range_uj: Optional[int] = None,
) -> Optional[float]:
    """Package power from two RAPL energy counter readings.

    The counter is cumulative microjoules and wraps at `max_range_uj`. A
    reading that went backwards without a known wrap point is unusable — the
    machine most likely rebooted, and guessing would invent a power spike.
    """
    if dt_seconds <= 0:
        return None

    delta = current_uj - previous_uj
    if delta < 0:
        if not max_range_uj:
            return None
        delta += max_range_uj
        if delta < 0:
            return None

    watts = delta / (dt_seconds * 1_000_000.0)
    # A 15 W embedded part cannot draw kilowatts; that is a counter glitch.
    return watts if watts < 1000.0 else None


def excitation(powers: Sequence[float]) -> float:
    """How much the load varied, as interquartile range over median.

    Scale-free on purpose, so the same threshold works for a 6 W idle node and
    a 28 W one. Zero means perfectly flat, which means θ is unidentifiable.
    """
    clean = sorted(p for p in powers if p is not None)
    if len(clean) < 4:
        return 0.0
    median = _quantile(clean, 0.5)
    if median <= 0:
        return 0.0
    return (_quantile(clean, 0.75) - _quantile(clean, 0.25)) / median


def decimate(samples: Sequence[Sample], factor: int) -> list:
    """Fold consecutive samples into means. For storage rollups only.

    Never feed the result to `fit`. Block-averaging attenuates the already
    low-passed temperature more than it attenuates power, which collapses the
    apparent gain and biases θ downwards by tens of percent — see the module
    docstring. Sensor noise is dealt with by instrumental variables instead.
    """
    if factor <= 1:
        return list(samples)

    out = []
    for start in range(0, len(samples) - factor + 1, factor):
        chunk = samples[start:start + factor]
        out.append(Sample(
            timestamp=sum(s.timestamp for s in chunk) / factor,
            power_w=sum(s.power_w for s in chunk) / factor,
            temp_c=sum(s.temp_c for s in chunk) / factor,
            throttled=any(s.throttled for s in chunk),
        ))
    return out


def fit(
    samples: Sequence[Sample],
    min_excitation: float = MIN_EXCITATION,
    min_samples: int = MIN_SAMPLES,
    robust: bool = True,
) -> ThermalFit:
    """Identify θ, τ and the effective ambient from one window of samples.

    Samples must be evenly spaced and ordered; the step is taken from the
    median gap so a single missed reading does not skew it. Returns a rejection
    rather than a number whenever the window cannot support one.
    """
    count = len(samples)

    if any(s.throttled for s in samples):
        # While throttling, power and temperature stop being independent — the
        # controller holds T at a ceiling by cutting P, and the fit would read
        # that correlation as a very low thermal resistance.
        return ThermalFit(Rejection.THROTTLED, n_samples=count)

    if count < min_samples:
        return ThermalFit(Rejection.TOO_FEW_SAMPLES, n_samples=count)

    exc = excitation([s.power_w for s in samples])
    if exc < min_excitation:
        return ThermalFit(Rejection.NO_EXCITATION, n_samples=count, excitation=exc)

    gaps = sorted(
        samples[i + 1].timestamp - samples[i].timestamp for i in range(count - 1)
    )
    dt = _quantile(gaps, 0.5)
    if dt <= 0:
        return ThermalFit(Rejection.TOO_FEW_SAMPLES, n_samples=count, excitation=exc)

    # k runs over the interior: k-1 supplies the instrument, k+1 the target.
    first, last = 1, count - 1
    span = last - first
    if span < min_samples // 2:
        return ThermalFit(Rejection.TOO_FEW_SAMPLES, n_samples=count, excitation=exc)

    # Centring keeps the constant from fighting the other regressors for the
    # same information, which matters when A sits close to 1.
    t_mean = sum(samples[k].temp_c for k in range(first, last)) / span
    p_mean = sum(samples[k].power_w for k in range(first, last)) / span
    k_mean = (first + last - 1) / 2.0

    regressors = []
    instruments = []
    targets = []
    for k in range(first, last):
        power = samples[k].power_w - p_mean
        drift = k - k_mean
        # Power comes from an energy counter and the last two columns are
        # deterministic, so only the temperature column needs instrumenting.
        regressors.append((samples[k].temp_c - t_mean, power, 1.0, drift))
        instruments.append((samples[k - 1].temp_c - t_mean, power, 1.0, drift))
        targets.append(samples[k + 1].temp_c - t_mean)

    solution = _instrumental_fit(regressors, instruments, targets, robust=robust)
    if solution is None:
        return ThermalFit(Rejection.SINGULAR, n_samples=count, excitation=exc)

    a_coef, b_coef, c_coef, _drift = solution

    # A = exp(-dt/tau) must sit strictly inside (0, 1): at or above 1 the window
    # describes a system that never settles, at or below 0 an oscillation.
    if not (0.0 < a_coef < 1.0):
        return ThermalFit(Rejection.UNSTABLE, n_samples=count, excitation=exc,
                          coefficients=solution)

    decay = 1.0 - a_coef
    tau = -dt / math.log(a_coef)
    theta = b_coef / decay
    # Undo the centring: at steady state around the window mean, the ambient
    # sits one θ·P̄ below the mean temperature.
    t_ambient = t_mean - theta * p_mean + c_coef / decay

    if not (TAU_BOUNDS_S[0] <= tau <= TAU_BOUNDS_S[1]):
        return ThermalFit(Rejection.IMPLAUSIBLE_TAU, n_samples=count,
                          excitation=exc, tau_seconds=tau, coefficients=solution)

    if not (THETA_BOUNDS_C_PER_W[0] <= theta <= THETA_BOUNDS_C_PER_W[1]):
        return ThermalFit(Rejection.IMPLAUSIBLE_THETA, n_samples=count,
                          excitation=exc, theta_c_per_w=theta, coefficients=solution)

    return ThermalFit(
        rejection=Rejection.OK,
        n_samples=count,
        excitation=exc,
        theta_c_per_w=theta,
        tau_seconds=tau,
        t_ambient_c=t_ambient,
        r_squared=_explained_change(samples, first, last, regressors, targets, solution),
        mean_temp_c=sum(s.temp_c for s in samples) / count,
        coefficients=solution,
    )


def normalise_theta(
    theta: Optional[float],
    delta_t: Optional[float],
    reference_delta_t: float = REFERENCE_DELTA_T,
    exponent: float = CONVECTION_EXPONENT,
) -> Optional[float]:
    """Correct θ back to reference conditions before comparing across time.

    θ is not the ambient-invariant constant a first reading of the physics
    suggests. Natural convection gets *more* effective as the surface runs
    hotter above the air around it, and radiation more so again, so the same
    hardware genuinely measures a lower θ on a hot afternoon than on a cold
    night — by 15-20 % across a seasonal swing, which is the same size as the
    degradation signal being hunted.

    Comparing a node against its own past therefore requires this correction.
    Comparing nodes against each other *within the same window* does not, since
    they share the weather — which is why the cohort detector is the more
    robust of the two.

    `exponent` should be re-fitted from each node's own record; the default is
    only the textbook laminar value.
    """
    if theta is None or not delta_t or delta_t <= 0:
        return None
    return theta * (delta_t / reference_delta_t) ** exponent


def _instrumental_fit(
    regressors: Sequence[Sequence[float]],
    instruments: Sequence[Sequence[float]],
    targets: Sequence[float],
    robust: bool = True,
    iterations: int = 5,
) -> Optional[tuple]:
    """Two-stage-free IV estimate, optionally Huber-reweighted.

    β = (Z'WX)⁻¹ Z'Wy. With Z = X this degenerates to weighted least squares;
    the whole reason Z differs is to break the correlation between the noisy
    temperature regressor and the noise in the target.
    """
    weights = [1.0] * len(regressors)
    solution = _weighted_solve(regressors, instruments, targets, weights)
    if solution is None or not robust:
        return solution

    # Huber: outliers get down-weighted rather than discarded, so one bogus
    # reading does not drag the window.
    for _ in range(iterations):
        residuals = [
            targets[i] - sum(c * x for c, x in zip(solution, regressors[i]))
            for i in range(len(regressors))
        ]
        scale = _mad(residuals)
        if scale <= 0:
            break
        cutoff = _HUBER_K * scale
        weights = [1.0 if abs(r) <= cutoff else cutoff / abs(r) for r in residuals]
        updated = _weighted_solve(regressors, instruments, targets, weights)
        if updated is None:
            break
        converged = max(abs(u - s) for u, s in zip(updated, solution)) < 1e-12
        solution = updated
        if converged:
            break

    return solution


def _weighted_solve(
    regressors: Sequence[Sequence[float]],
    instruments: Sequence[Sequence[float]],
    targets: Sequence[float],
    weights: Sequence[float],
) -> Optional[tuple]:
    width = len(regressors[0])
    # Augmented [Z'WX | Z'Wy].
    matrix = [[0.0] * (width + 1) for _ in range(width)]
    for row, instrument, target, weight in zip(regressors, instruments, targets, weights):
        for i in range(width):
            wi = weight * instrument[i]
            for j in range(width):
                matrix[i][j] += wi * row[j]
            matrix[i][width] += wi * target
    return _gauss(matrix, width)


def _gauss(matrix, width: int) -> Optional[tuple]:
    """Gaussian elimination with partial pivoting, refusing near-singularity."""
    largest = max(abs(matrix[i][j]) for i in range(width) for j in range(width))
    if largest == 0:
        return None
    tolerance = largest * 1e-12

    for col in range(width):
        pivot_row = max(range(col, width), key=lambda r: abs(matrix[r][col]))
        if abs(matrix[pivot_row][col]) <= tolerance:
            # Collinear regressors — most often a window where power never
            # moved, which the excitation gate should already have caught.
            return None
        matrix[col], matrix[pivot_row] = matrix[pivot_row], matrix[col]

        pivot = matrix[col][col]
        for row in range(col + 1, width):
            factor = matrix[row][col] / pivot
            if factor == 0:
                continue
            for j in range(col, width + 1):
                matrix[row][j] -= factor * matrix[col][j]

    solution = [0.0] * width
    for row in range(width - 1, -1, -1):
        total = matrix[row][width] - sum(
            matrix[row][j] * solution[j] for j in range(row + 1, width)
        )
        solution[row] = total / matrix[row][row]
    return tuple(solution)


def _quantile(sorted_values: Sequence[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = (len(sorted_values) - 1) * q
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return float(sorted_values[int(rank)])
    return float(
        sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (rank - low)
    )


def _mad(values: Sequence[float]) -> float:
    """Median absolute deviation, scaled to estimate a standard deviation."""
    if not values:
        return 0.0
    median = _quantile(sorted(values), 0.5)
    deviations = sorted(abs(v - median) for v in values)
    return 1.4826 * _quantile(deviations, 0.5)


def _explained_change(samples, first, last, regressors, targets, solution) -> Optional[float]:
    """Share of the temperature *change* the model accounts for.

    Scoring against T[k+1] itself would flatter the fit into meaninglessness —
    the next reading is mostly the last one, so any model that passes T[k]
    through scores near 1. Measuring against the step-to-step change asks the
    honest question: does the model explain the movement?
    """
    changes = [samples[k + 1].temp_c - samples[k].temp_c for k in range(first, last)]
    mean_change = sum(changes) / len(changes)
    ss_total = sum((c - mean_change) ** 2 for c in changes)
    if ss_total <= 0:
        return None
    ss_residual = sum(
        (targets[i] - sum(c * x for c, x in zip(solution, regressors[i]))) ** 2
        for i in range(len(regressors))
    )
    return 1.0 - ss_residual / ss_total


def simulate(
    theta: float,
    tau: float,
    t_ambient: float,
    powers: Sequence[float],
    dt: float,
    t_initial: Optional[float] = None,
    exact: bool = True,
) -> list:
    """Generate samples from a known first-order system. For tests.

    Defaults to the analytic exponential solution, which is what real hardware
    does and what the fit is derived from. `exact=False` integrates a crude
    Euler step instead, deliberately mismatching the model so the fit's
    tolerance of model error can be measured rather than assumed.
    """
    temperature = t_ambient if t_initial is None else t_initial
    out = []
    for k, power in enumerate(powers):
        out.append(Sample(timestamp=k * dt, power_w=power, temp_c=temperature))
        steady = t_ambient + power * theta
        if exact:
            temperature = steady + (temperature - steady) * math.exp(-dt / tau)
        else:
            temperature += (dt / tau) * (power * theta - (temperature - t_ambient))
    return out
