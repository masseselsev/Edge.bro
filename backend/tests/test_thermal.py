import math
import random

import pytest

from core import thermal
from core.thermal import Rejection, Sample


# A Vecow EMBC-5000 carries a 15 W Tiger Lake part on a large passive sink.
# These are the numbers the tests exercise against.
THETA = 1.5      # °C/W
TAU = 2100.0     # s, ~35 min for a 25x25 cm aluminium heatsink
T_AMB = 25.0     # °C
DT = 60.0        # s, the collector's sampling step


def varying_load(n, low=3.0, high=14.0, seed=1):
    """A load that swings the way traffic-driven inference plausibly does."""
    rng = random.Random(seed)
    return [low + (high - low) * (0.5 + 0.5 * math.sin(k / 9.0)) * rng.uniform(0.8, 1.2)
            for k in range(n)]


# --- RAPL -------------------------------------------------------------------

def test_power_from_two_energy_readings():
    # 900 J over 60 s = 15 W
    assert thermal.power_watts(1_000_000, 901_000_000, 60.0) == pytest.approx(15.0)


def test_counter_wrap_is_unwrapped_when_the_range_is_known():
    max_range = 1_000_000_000
    # Wrapped: 999.7 MJ -> 0.3 MJ is +600000 uj, not a huge negative.
    watts = thermal.power_watts(999_700_000, 300_000, 60.0, max_range_uj=max_range)
    assert watts == pytest.approx(600_000 / 60_000_000)


def test_a_backwards_counter_without_a_known_range_is_refused():
    """Most likely a reboot. Guessing would invent a power spike."""
    assert thermal.power_watts(900_000_000, 1_000, 60.0) is None


def test_absurd_power_is_refused():
    assert thermal.power_watts(0, 10**15, 1.0) is None


def test_a_zero_interval_yields_nothing():
    assert thermal.power_watts(0, 1000, 0) is None


# --- excitation -------------------------------------------------------------

def test_a_flat_load_has_no_excitation():
    assert thermal.excitation([8.0] * 100) == 0.0


def test_a_swinging_load_is_well_excited():
    assert thermal.excitation(varying_load(200)) > thermal.MIN_EXCITATION


def test_excitation_is_scale_free():
    """The same relative swing scores the same at 6 W and at 28 W."""
    small = [3.0, 4.0, 5.0, 6.0] * 25
    large = [15.0, 20.0, 25.0, 30.0] * 25
    assert thermal.excitation(small) == pytest.approx(thermal.excitation(large), rel=1e-9)


def test_excitation_of_almost_nothing_is_zero():
    assert thermal.excitation([]) == 0.0
    assert thermal.excitation([5.0]) == 0.0


# --- recovering a known system ----------------------------------------------

def test_the_fit_recovers_a_known_system():
    samples = thermal.simulate(THETA, TAU, T_AMB, varying_load(240), DT)
    result = thermal.fit(samples)

    assert result.ok
    assert result.theta_c_per_w == pytest.approx(THETA, rel=0.02)
    assert result.tau_seconds == pytest.approx(TAU, rel=0.02)
    assert result.t_ambient_c == pytest.approx(T_AMB, abs=0.5)


def test_ambient_is_recovered_without_being_measured():
    """The whole reason this approach works: no thermometer sees the air, yet
    the fit infers what ambient the observations imply."""
    for ambient in (-20.0, 5.0, 25.0, 45.0):
        samples = thermal.simulate(THETA, TAU, ambient, varying_load(240), DT)
        result = thermal.fit(samples)
        assert result.ok
        assert result.t_ambient_c == pytest.approx(ambient, abs=0.5)


def test_theta_is_independent_of_ambient():
    """A node in the sun and one in the shade must report the same theta."""
    cold = thermal.fit(thermal.simulate(THETA, TAU, -10.0, varying_load(240), DT))
    hot = thermal.fit(thermal.simulate(THETA, TAU, 50.0, varying_load(240), DT))
    assert cold.theta_c_per_w == pytest.approx(hot.theta_c_per_w, rel=0.01)


def test_a_degraded_interface_shows_up_as_a_higher_theta():
    """The signal the whole feature exists to detect."""
    healthy = thermal.fit(thermal.simulate(1.5, TAU, T_AMB, varying_load(240), DT))
    degraded = thermal.fit(thermal.simulate(1.9, TAU, T_AMB, varying_load(240), DT))

    assert healthy.ok and degraded.ok
    assert degraded.theta_c_per_w > healthy.theta_c_per_w * 1.2


def test_discretisation_bias_against_the_exact_solution_is_small():
    """The fit assumes an Euler step; reality integrates an exponential. At
    dt/tau ~ 0.03 the resulting bias should stay within a few percent."""
    samples = thermal.simulate(THETA, TAU, T_AMB, varying_load(240), DT, exact=True)
    result = thermal.fit(samples)

    assert result.ok
    assert result.theta_c_per_w == pytest.approx(THETA, rel=0.05)
    assert result.tau_seconds == pytest.approx(TAU, rel=0.05)


def noisy(samples, sigma=0.25, seed=7):
    """Quantised, wandering die temperature — what the sensor actually gives."""
    rng = random.Random(seed)
    return [
        Sample(s.timestamp, s.power_w * rng.uniform(0.99, 1.01),
               round(s.temp_c + rng.gauss(0, sigma), 1))
        for s in samples
    ]


def test_measurement_noise_is_tolerated():
    """Die temperature is reported to a tenth of a degree and wanders more."""
    clean = thermal.simulate(THETA, TAU, T_AMB, varying_load(600), DT)
    result = thermal.fit(noisy(clean))

    assert result.ok
    assert result.theta_c_per_w == pytest.approx(THETA, rel=0.10)


def test_instrumenting_the_noisy_regressor_is_what_removes_the_bias():
    """Regression guard on the finding that drove this design.

    T[k] is a regressor and carries sensor noise that also sits in the target
    T[k+1]. Plain least squares reads that correlation as fast decay and lands
    ~37 % low. Feeding T[k-1] in as the instrument is the whole fix; if anyone
    reverts to Z = X, this fails.
    """
    clean = thermal.simulate(THETA, TAU, T_AMB, varying_load(600), DT)
    samples = noisy(clean)

    honest = thermal.fit(samples)
    assert honest.theta_c_per_w == pytest.approx(THETA, rel=0.10)

    # Rebuild the same regression with the instrument replaced by the regressor
    # itself, which is exactly ordinary least squares.
    first, last = 1, len(samples) - 1
    span = last - first
    t_mean = sum(samples[k].temp_c for k in range(first, last)) / span
    p_mean = sum(samples[k].power_w for k in range(first, last)) / span
    k_mean = (first + last - 1) / 2.0

    rows, targets = [], []
    for k in range(first, last):
        rows.append((samples[k].temp_c - t_mean, samples[k].power_w - p_mean,
                     1.0, k - k_mean))
        targets.append(samples[k + 1].temp_c - t_mean)

    a, b, _c, _d = thermal._instrumental_fit(rows, rows, targets, robust=False)
    ols_theta = b / (1.0 - a)

    assert ols_theta < THETA * 0.8, "OLS should be badly biased low; IV should not"


def test_averaging_before_fitting_corrupts_theta():
    """Regression guard on the second finding.

    Block-averaging looks like the obvious cure for sensor noise and is a trap:
    temperature is already low-passed, so averaging costs it more than it costs
    power, the apparent gain collapses and theta collapses with it. `decimate`
    exists for storage rollups only.
    """
    clean = thermal.simulate(THETA, TAU, T_AMB, varying_load(1200), DT)

    direct = thermal.fit(clean)
    averaged = thermal.fit(thermal.decimate(clean, 5))

    assert direct.theta_c_per_w == pytest.approx(THETA, rel=0.02)
    assert averaged.ok
    assert averaged.theta_c_per_w < THETA * 0.75


def test_an_outlier_does_not_drag_the_fit():
    """Huber weighting: one bogus reading must not move the answer much."""
    samples = list(thermal.simulate(THETA, TAU, T_AMB, varying_load(240), DT))
    samples[120] = Sample(samples[120].timestamp, samples[120].power_w,
                          samples[120].temp_c + 40.0)

    robust = thermal.fit(samples, robust=True)
    assert robust.ok
    assert robust.theta_c_per_w == pytest.approx(THETA, rel=0.15)


# --- refusing to answer -----------------------------------------------------

def test_a_flat_load_is_refused_rather_than_guessed():
    """The single most important behaviour here. A constant load makes theta
    unidentifiable, and a plausible-looking wrong number is worse than none."""
    samples = thermal.simulate(THETA, TAU, T_AMB, [8.0] * 240, DT)
    result = thermal.fit(samples)

    assert result.rejection is Rejection.NO_EXCITATION
    assert result.theta_c_per_w is None


def test_a_throttled_window_is_refused():
    """While throttling, the controller cuts power to hold temperature — a
    correlation the fit would read as a very low thermal resistance."""
    samples = list(thermal.simulate(THETA, TAU, T_AMB, varying_load(240), DT))
    samples[100] = Sample(samples[100].timestamp, samples[100].power_w,
                          samples[100].temp_c, throttled=True)

    assert thermal.fit(samples).rejection is Rejection.THROTTLED


def test_too_short_a_window_is_refused():
    samples = thermal.simulate(THETA, TAU, T_AMB, varying_load(20), DT)
    assert thermal.fit(samples).rejection is Rejection.TOO_FEW_SAMPLES


def test_noise_alone_does_not_produce_a_thermal_model():
    """Pure noise must fail a plausibility gate, not yield a confident theta."""
    rng = random.Random(3)
    samples = [
        Sample(k * DT, rng.uniform(2.0, 15.0), rng.uniform(30.0, 70.0))
        for k in range(240)
    ]
    result = thermal.fit(samples)

    assert not result.ok
    assert result.rejection in (
        Rejection.UNSTABLE, Rejection.IMPLAUSIBLE_TAU,
        Rejection.IMPLAUSIBLE_THETA, Rejection.SINGULAR,
    )


def test_a_constant_temperature_is_not_a_thermal_model():
    samples = [Sample(k * DT, 3.0 + (k % 7), 50.0) for k in range(240)]
    assert not thermal.fit(samples).ok


def test_rejections_carry_the_excitation_that_caused_them():
    """An operator needs to see *why* a node has no theta."""
    samples = thermal.simulate(THETA, TAU, T_AMB, [8.0] * 240, DT)
    result = thermal.fit(samples)
    assert result.excitation < thermal.MIN_EXCITATION
    assert result.n_samples == 240


# --- ambient normalisation --------------------------------------------------

def test_normalisation_lowers_theta_measured_at_a_large_delta():
    """Convection gets more effective as the sink runs hotter above the air, so
    a summer measurement genuinely reads lower and must be corrected up to the
    reference before being compared with a winter one."""
    at_reference = thermal.normalise_theta(1.5, delta_t=20.0)
    assert at_reference == pytest.approx(1.5)

    hot_day = thermal.normalise_theta(1.35, delta_t=30.0)
    assert hot_day > 1.35


def test_the_seasonal_swing_is_the_size_of_the_signal_we_hunt():
    """Justifies why normalisation is mandatory rather than a refinement: the
    ambient artefact is comparable to a real degradation."""
    winter = thermal.normalise_theta(1.5, delta_t=12.0)
    summer = thermal.normalise_theta(1.5, delta_t=32.0)
    assert abs(summer - winter) / winter > 0.20


def test_normalisation_needs_a_real_delta():
    assert thermal.normalise_theta(1.5, delta_t=None) is None
    assert thermal.normalise_theta(1.5, delta_t=0) is None
    assert thermal.normalise_theta(None, delta_t=20.0) is None


def test_the_exponent_is_adjustable_because_it_should_be_fitted_per_node():
    flat = thermal.normalise_theta(1.5, delta_t=40.0, exponent=0.0)
    curved = thermal.normalise_theta(1.5, delta_t=40.0, exponent=0.25)
    assert flat == pytest.approx(1.5)
    assert curved > flat
