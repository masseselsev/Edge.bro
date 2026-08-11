"""Reading `smartctl --json` and turning it into a health score.

The score is deliberately not one clever formula. It is the minimum of several
sub-scores, each defensible on its own, and the breakdown travels with it so
the UI can show *why* a drive sits at 62 % rather than asking an operator to
trust a number. A single opaque figure that turns out to be wrong is worse than
no figure at all.

Two things are kept out of the score on purpose:

* **Interface CRC errors** (SATA attribute 199) mean a cable or a connector,
  not a dying disk. Scoring them as wear would send someone to buy an SSD to
  fix a loose SATA lead. They surface as a separate advisory.
* **Access latency.** Rising service time under steady load is a genuine early
  warning, but it comes from /proc/diskstats, not from SMART, and folding a
  filesystem-level signal into a device-level score dilutes both.

The number an operator can actually act on is not the percentage but the
projected date — see `project_wear`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Optional, Sequence

#: NVMe reports written volume in "data units" of 1000 × 512 bytes.
NVME_DATA_UNIT_BYTES = 512_000

#: Default ceiling for a drive's own temperature, overridable globally and per
#: node. Roadside enclosures in full sun legitimately run hot, and one
#: fleet-wide limit would either cry wolf or miss real trouble.
DEFAULT_TEMP_WARN_C = 60
DEFAULT_TEMP_CRIT_C = 70

#: A wear projection needs both a span and an actual movement to divide by;
#: below these the rate is indistinguishable from quantisation.
MIN_PROJECTION_DAYS = 14.0
MIN_PROJECTION_WEAR_POINTS = 1.0


class Protocol(str, Enum):
    SATA = "SATA"
    NVME = "NVME"
    UNKNOWN = "UNKNOWN"


class Grade(str, Enum):
    """What to do about it, which is the only question that matters."""

    OK = "OK"
    WATCH = "WATCH"
    REPLACE = "REPLACE"
    UNKNOWN = "UNKNOWN"


#: Score boundaries between grades.
GRADE_WATCH_BELOW = 80
GRADE_REPLACE_BELOW = 40


@dataclass(frozen=True)
class SubScore:
    """One defensible component of the overall score."""

    name: str
    score: Optional[float]
    #: Machine-readable evidence, so the UI can render the reasoning itself
    #: rather than parsing prose.
    evidence: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Reading:
    """The parts of a smartctl report the rest of the system cares about."""

    protocol: Protocol
    model: Optional[str] = None
    serial: Optional[str] = None
    firmware: Optional[str] = None
    #: None when smartctl could not reach the drive at all.
    health_passed: Optional[bool] = None
    temperature_c: Optional[int] = None
    power_on_hours: Optional[int] = None
    written_bytes: Optional[int] = None
    #: 0-100, the vendor's own wear estimate where it exists.
    percent_used: Optional[float] = None
    #: Raw attribute values, keyed by SATA attribute id or NVMe field name.
    values: dict = field(default_factory=dict)
    parse_error: Optional[str] = None


@dataclass(frozen=True)
class Health:
    score: Optional[int]
    grade: Grade
    subscores: list = field(default_factory=list)
    #: Caps that were applied, and why. These override the sub-scores.
    overrides: list = field(default_factory=list)
    #: Real problems that are not disk wear and must not be scored as such.
    advisories: list = field(default_factory=list)


@dataclass(frozen=True)
class WearProjection:
    """When the drive runs out of endurance, and the arithmetic behind it."""

    projected_date: Optional[datetime] = None
    days_remaining: Optional[float] = None
    percent_used_per_day: Optional[float] = None
    bytes_per_day: Optional[float] = None
    current_percent_used: Optional[float] = None
    observation_days: Optional[float] = None
    observation_points: int = 0
    #: Why there is no projection, when there is none.
    unavailable_reason: Optional[str] = None


# --- parsing ----------------------------------------------------------------

def parse(report: Any) -> Reading:
    """Normalise `smartctl --json` output into a Reading.

    Accepts the parsed dict. Anything unrecognised comes back as a Reading with
    `parse_error` set rather than raising: one odd drive in a fleet of a
    thousand must not take a poll cycle down.
    """
    if not isinstance(report, dict):
        return Reading(Protocol.UNKNOWN, parse_error="report is not an object")

    device_type = (report.get("device", {}) or {}).get("type", "") or ""
    if "nvme" in device_type.lower() or "nvme_smart_health_information_log" in report:
        return _parse_nvme(report)
    if "ata_smart_attributes" in report or device_type.lower().startswith(("sat", "ata")):
        return _parse_sata(report)
    return Reading(
        Protocol.UNKNOWN,
        model=report.get("model_name"),
        health_passed=(report.get("smart_status") or {}).get("passed"),
        parse_error=f"unrecognised device type {device_type!r}",
    )


def _common(report: dict) -> dict:
    return {
        "model": report.get("model_name"),
        "serial": report.get("serial_number"),
        "firmware": report.get("firmware_version"),
        "health_passed": (report.get("smart_status") or {}).get("passed"),
        "temperature_c": (report.get("temperature") or {}).get("current"),
        "power_on_hours": (report.get("power_on_time") or {}).get("hours"),
    }


def _parse_nvme(report: dict) -> Reading:
    log = report.get("nvme_smart_health_information_log") or {}
    common = _common(report)

    written = log.get("data_units_written")
    if written is not None:
        written = int(written) * NVME_DATA_UNIT_BYTES

    if common["temperature_c"] is None:
        common["temperature_c"] = log.get("temperature")

    return Reading(
        protocol=Protocol.NVME,
        written_bytes=written,
        percent_used=_as_float(log.get("percentage_used")),
        values={k: v for k, v in log.items()},
        **common,
    )


def _parse_sata(report: dict) -> Reading:
    table = ((report.get("ata_smart_attributes") or {}).get("table")) or []
    values = {}
    for entry in table:
        if not isinstance(entry, dict) or entry.get("id") is None:
            continue
        values[int(entry["id"])] = {
            "name": entry.get("name"),
            "value": entry.get("value"),      # vendor-normalised, higher is better
            "worst": entry.get("worst"),
            "thresh": entry.get("thresh"),
            "raw": (entry.get("raw") or {}).get("value"),
        }

    common = _common(report)

    written = None
    # 241 is Total_LBAs_Written on every vendor that reports it.
    lbas = (values.get(241) or {}).get("raw")
    if lbas:
        block = (report.get("logical_block_size") or 512)
        written = int(lbas) * int(block)

    # Samsung and most others carry remaining life in the normalised value of
    # 177 Wear_Leveling_Count, counting down from 100.
    percent_used = None
    wear = values.get(177) or values.get(173) or values.get(231)
    if wear and wear.get("value") is not None:
        remaining = _as_float(wear["value"])
        if remaining is not None and 0 <= remaining <= 100:
            percent_used = 100.0 - remaining

    return Reading(
        protocol=Protocol.SATA,
        written_bytes=written,
        percent_used=percent_used,
        values=values,
        **common,
    )


def _as_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _raw(reading: Reading, attribute_id: int) -> Optional[int]:
    entry = reading.values.get(attribute_id)
    if not isinstance(entry, dict):
        return None
    value = entry.get("raw")
    return int(value) if isinstance(value, (int, float)) else None


# --- scoring ----------------------------------------------------------------

def score(
    reading: Reading,
    temp_warn_c: int = DEFAULT_TEMP_WARN_C,
    temp_crit_c: int = DEFAULT_TEMP_CRIT_C,
) -> Health:
    """Grade a drive, showing the work.

    The result is the lowest sub-score, then any hard caps applied on top. A
    drive can be 99 % unworn and still be scored 30 because it has pending
    sectors — wear is not the only way a disk fails, and averaging would let a
    good wear figure hide a bad integrity one.
    """
    if reading.parse_error and reading.protocol is Protocol.UNKNOWN:
        return Health(score=None, grade=Grade.UNKNOWN,
                      advisories=[f"Could not read SMART data: {reading.parse_error}"])

    subscores = [
        _wear_subscore(reading),
        _spare_subscore(reading),
        _integrity_subscore(reading),
        _error_subscore(reading),
        _thermal_subscore(reading, temp_warn_c, temp_crit_c),
    ]
    subscores = [s for s in subscores if s is not None]

    known = [s.score for s in subscores if s.score is not None]
    total = min(known) if known else None

    overrides = []
    advisories = list(_advisories(reading))

    # smartctl's own verdict outranks every heuristic here.
    if reading.health_passed is False:
        total = 0
        overrides.append("SMART overall-health self-assessment reports FAILED")

    pending = _raw(reading, 197)
    uncorrectable = _raw(reading, 198)
    reserved_used = _raw(reading, 179)
    runtime_bad = _raw(reading, 183)
    if pending or uncorrectable or reserved_used or runtime_bad:
        total = min(total if total is not None else 100, 30)
        overrides.append(
            f"Unreadable sectors present (pending={pending or 0}, "
            f"offline uncorrectable={uncorrectable or 0}, "
            f"reserved blocks used={reserved_used or 0}, "
            f"runtime bad block={runtime_bad or 0})"
        )

    spare = _as_float(reading.values.get("available_spare"))
    spare_threshold = _as_float(reading.values.get("available_spare_threshold"))
    if spare is not None and spare_threshold is not None and spare < spare_threshold:
        total = min(total if total is not None else 100, 20)
        overrides.append(
            f"Spare blocks {spare:.0f}% are below the drive's own threshold "
            f"of {spare_threshold:.0f}%"
        )

    critical = reading.values.get("critical_warning")
    if isinstance(critical, int) and critical != 0:
        total = min(total if total is not None else 100, 25)
        overrides.append(f"NVMe critical warning flags set (0x{critical:02x})")

    return Health(
        score=None if total is None else int(round(total)),
        grade=grade_for(total),
        subscores=subscores,
        overrides=overrides,
        advisories=advisories,
    )


def grade_for(value: Optional[float]) -> Grade:
    if value is None:
        return Grade.UNKNOWN
    if value < GRADE_REPLACE_BELOW:
        return Grade.REPLACE
    if value < GRADE_WATCH_BELOW:
        return Grade.WATCH
    return Grade.OK


def _wear_subscore(reading: Reading) -> Optional[SubScore]:
    if reading.percent_used is None:
        return SubScore("wear", None, {"reason": "no wear indicator reported"})
    remaining = max(0.0, 100.0 - reading.percent_used)
    return SubScore("wear", remaining, {
        "percent_used": reading.percent_used,
        "source": "percentage_used" if reading.protocol is Protocol.NVME
                  else "wear levelling count",
    })


def _spare_subscore(reading: Reading) -> Optional[SubScore]:
    if reading.protocol is Protocol.NVME:
        spare = _as_float(reading.values.get("available_spare"))
        threshold = _as_float(reading.values.get("available_spare_threshold"))
        if spare is None:
            return None
        # Scale so that hitting the threshold reads as zero rather than as the
        # raw percentage, which would look survivable when it is not.
        if threshold is not None and threshold < 100:
            scaled = (spare - threshold) / (100.0 - threshold) * 100.0
        else:
            scaled = spare
        return SubScore("spare", max(0.0, min(100.0, scaled)),
                        {"available_spare": spare, "threshold": threshold})

    reallocated = _raw(reading, 5)
    if reallocated is None:
        return None
    # Any reallocation at all is meaningful on an SSD; the count matters less
    # than the fact it started.
    value = 100.0 if reallocated == 0 else max(0.0, 60.0 - reallocated)
    return SubScore("spare", value, {"reallocated_sectors": reallocated})


def _integrity_subscore(reading: Reading) -> Optional[SubScore]:
    """Sectors or blocks the drive itself has flagged as bad since leaving the factory.

    Checks two attribute families, because a drive reports one or the other
    and not both. 197 (Current_Pending_Sector) and 198 (Offline_Uncorrectable)
    are the classic HDD pair. Plenty of SSDs — including the Samsung 870 EVO
    in the fleet's own EMBC-5000 units, confirmed against a live unit's actual
    smartctl output — report neither and instead carry 179
    (Used_Rsvd_Blk_Cnt_Tot: reserved blocks consumed by failures) and 183
    (Runtime_Bad_Block: blocks that went bad during operation, as opposed to
    being culled at the factory). Checking only the HDD pair would leave this
    subscore silently unavailable on exactly the drive the fleet actually
    uses — any of the four moving off zero means the same thing regardless of
    which family the drive speaks.
    """
    if reading.protocol is Protocol.NVME:
        critical = reading.values.get("critical_warning")
        if critical is None:
            return None
        return SubScore("integrity", 100.0 if critical == 0 else 0.0,
                        {"critical_warning": critical})

    pending = _raw(reading, 197)
    uncorrectable = _raw(reading, 198)
    reserved_used = _raw(reading, 179)
    runtime_bad = _raw(reading, 183)
    if all(v is None for v in (pending, uncorrectable, reserved_used, runtime_bad)):
        return None
    bad = sum(v or 0 for v in (pending, uncorrectable, reserved_used, runtime_bad))
    return SubScore("integrity", 100.0 if bad == 0 else 0.0, {
        "pending": pending,
        "offline_uncorrectable": uncorrectable,
        "reserved_blocks_used": reserved_used,
        "runtime_bad_block": runtime_bad,
    })


def _error_subscore(reading: Reading) -> Optional[SubScore]:
    if reading.protocol is Protocol.NVME:
        errors = reading.values.get("media_errors")
        if errors is None:
            return None
        return SubScore("errors", 100.0 if errors == 0 else max(0.0, 70.0 - errors),
                        {"media_errors": errors})

    reported = _raw(reading, 187)
    if reported is None:
        return None
    return SubScore("errors", 100.0 if reported == 0 else max(0.0, 70.0 - reported),
                    {"reported_uncorrect": reported})


def _thermal_subscore(reading: Reading, warn_c: int, crit_c: int) -> Optional[SubScore]:
    temperature = reading.temperature_c
    if temperature is None:
        return None
    evidence = {"temperature_c": temperature, "warn_c": warn_c, "crit_c": crit_c}
    if temperature < warn_c:
        return SubScore("thermal", 100.0, evidence)
    if temperature >= crit_c:
        return SubScore("thermal", 0.0, evidence)
    # Linear between the two thresholds rather than a cliff: a drive one degree
    # over the warning line is not in the same state as one at the critical.
    span = max(1, crit_c - warn_c)
    return SubScore("thermal", 100.0 * (crit_c - temperature) / span, evidence)


def _advisories(reading: Reading):
    """Real problems that are not the disk wearing out."""
    crc = _raw(reading, 199)
    if crc:
        yield (
            f"Interface CRC errors ({crc}) indicate a cable or connector fault, "
            f"not drive wear. Reseat the SATA cable before replacing anything."
        )

    unsafe = reading.values.get("unsafe_shutdowns")
    if isinstance(unsafe, int) and unsafe > 100:
        yield (
            f"{unsafe} unsafe shutdowns recorded — the unit is losing power "
            f"without shutting down. This damages drives over time."
        )

    if reading.health_passed is None:
        yield "smartctl returned no overall-health verdict for this device."


# --- endurance projection ---------------------------------------------------

def project_wear(
    points: Sequence,
    now: Optional[datetime] = None,
) -> WearProjection:
    """When wear reaches 100 %, from the drive's own wear indicator over time.

    A percentage answers "how worn", which nobody can act on. A date answers
    "when do I buy", which maps onto a procurement cycle — so the date is what
    gets reported, with every input that produced it attached so the UI can
    show the derivation instead of asking for trust.

    Deliberately built on the vendor's own wear indicator rather than on
    written bytes against a rated endurance figure. Rated TBW is not in SMART,
    would need a per-model lookup table to maintain, and is a warranty number
    that drives routinely outlive. `percentage_used` is the manufacturer's own
    estimate of the same thing and needs no table.

    `points` are (datetime, percent_used) pairs in any order. The rate is the
    median of pairwise slopes, so one resampled or misread point cannot swing
    the answer.
    """
    now = now or datetime.utcnow()
    clean = sorted(
        (t, float(p)) for t, p in points
        if t is not None and p is not None
    )
    if len(clean) < 2:
        return WearProjection(observation_points=len(clean),
                              current_percent_used=clean[-1][1] if clean else None,
                              unavailable_reason="need at least two readings")

    current = clean[-1][1]
    span_days = (clean[-1][0] - clean[0][0]).total_seconds() / 86400.0
    base = WearProjection(
        current_percent_used=current,
        observation_days=round(span_days, 1),
        observation_points=len(clean),
    )

    if span_days < MIN_PROJECTION_DAYS:
        return _with(base, unavailable_reason=(
            f"only {span_days:.1f} days of history; needs {MIN_PROJECTION_DAYS:.0f}"
        ))

    if clean[-1][1] - clean[0][1] < MIN_PROJECTION_WEAR_POINTS:
        # percentage_used moves in whole points, so below one point of change
        # the rate is quantisation noise, not a trend.
        return _with(base, unavailable_reason=(
            "wear indicator has not moved enough to establish a rate"
        ))

    slopes = []
    for i in range(len(clean)):
        for j in range(i + 1, len(clean)):
            days = (clean[j][0] - clean[i][0]).total_seconds() / 86400.0
            if days >= MIN_PROJECTION_DAYS:
                slopes.append((clean[j][1] - clean[i][1]) / days)
    if not slopes:
        return _with(base, unavailable_reason="no pair of readings far enough apart")

    slopes.sort()
    middle = len(slopes) // 2
    rate = slopes[middle] if len(slopes) % 2 else (slopes[middle - 1] + slopes[middle]) / 2.0

    if rate <= 0:
        return _with(base, percent_used_per_day=rate,
                     unavailable_reason="wear is not increasing")

    days_remaining = max(0.0, (100.0 - current) / rate)
    # Past a decade the projection says nothing except "not soon", and a date
    # in 2190 reads as a bug rather than as reassurance.
    projected = now + timedelta(days=days_remaining) if days_remaining <= 3650 else None

    return _with(
        base,
        percent_used_per_day=rate,
        days_remaining=round(days_remaining, 1),
        projected_date=projected,
        unavailable_reason=None if projected else "projected beyond ten years",
    )


def bytes_per_day(points: Sequence) -> Optional[float]:
    """Write rate from (datetime, written_bytes) pairs. Informational only.

    Useful context next to the projection — "this node writes 40 GB a day" is
    something an operator can sanity-check against what the workload should be
    doing — but the projection itself runs off the wear indicator.
    """
    clean = sorted((t, int(b)) for t, b in points if t is not None and b is not None)
    if len(clean) < 2:
        return None
    span_days = (clean[-1][0] - clean[0][0]).total_seconds() / 86400.0
    if span_days <= 0:
        return None
    written = clean[-1][1] - clean[0][1]
    return written / span_days if written > 0 else None


def _with(projection: WearProjection, **changes) -> WearProjection:
    merged = {
        "projected_date": projection.projected_date,
        "days_remaining": projection.days_remaining,
        "percent_used_per_day": projection.percent_used_per_day,
        "bytes_per_day": projection.bytes_per_day,
        "current_percent_used": projection.current_percent_used,
        "observation_days": projection.observation_days,
        "observation_points": projection.observation_points,
        "unavailable_reason": projection.unavailable_reason,
    }
    merged.update(changes)
    return WearProjection(**merged)
