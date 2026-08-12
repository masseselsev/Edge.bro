"""Turning the node's raw telemetry buffer into something analysable.

The collector ships cumulative counters — RAPL microjoules, CPU jiffies, disk
service time — because differencing them on the node would mean keeping state
between one-shot invocations. Differencing happens here instead, which also
means a re-parse can always be done from the original data if the arithmetic
ever needs correcting.

Everything is defensive by design. A buffer arrives over SSH from hardware
that may have rebooted, had its clock stepped, run out of disk mid-write, or
simply be running an older collector. One bad line must cost one sample, not
the harvest. So parsing never raises: unusable input is counted and dropped,
and the counts come back with the results so a node quietly producing garbage
is visible rather than silently absent.

Pure functions over plain values — no database, no SSH, no clock of its own.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Optional, Sequence

from core import thermal

#: Schema version the collector stamps on every line. A line from the future
#: is kept (fields are additive) but one from an unknown past is dropped.
SCHEMA_VERSION = 1

#: Sanity bounds. These reject physically impossible readings rather than
#: plausible-but-wrong ones; the aim is catching corruption and clock chaos,
#: not second-guessing the sensors.
TEMP_BOUNDS_C = (-50.0, 150.0)
MAX_PLAUSIBLE_POWER_W = 500.0

#: Epoch bounds for a usable timestamp: 2020-01-01 to 2100-01-01.
#:
#: Not paranoia. A roadside unit whose RTC battery has died comes back from a
#: reboot with a garbage clock, and `datetime.fromtimestamp` raises on those
#: values rather than returning something odd — ValueError for year 3170843,
#: OSError for anything past the platform's time_t. Since the harvester has
#: already drained and deleted the node's buffer by the time parsing happens,
#: letting that propagate would lose the whole batch and keep losing every
#: batch for as long as the clock stayed wrong. A sample whose time is unknown
#: is useless in a time series, so it is dropped rather than clamped: clamping
#: would place it at a moment it did not happen and corrupt the series.
TIMESTAMP_BOUNDS = (1_577_836_800, 4_102_444_800)

#: A gap longer than this means the node was off, or the buffer was trimmed.
#: Counters cannot be differenced across it — the deltas would be nonsense —
#: so the series is cut into separate runs at that point.
MAX_SAMPLE_GAP_S = 600.0

#: Rollup bucket for what gets stored long term. Raw minute samples exist only
#: long enough to be fitted; at a thousand nodes, keeping them would be tens
#: of millions of rows a month for data nothing reads twice.
ROLLUP_SECONDS = 900


@dataclass(frozen=True)
class Record:
    """One parsed line, still carrying cumulative counters."""

    timestamp: float
    uptime_s: Optional[float] = None
    rapl_uj: Optional[int] = None
    rapl_max_uj: Optional[int] = None
    cpu_temp_c: Optional[float] = None
    core_temp_max_c: Optional[float] = None
    board_temp_c: Optional[float] = None
    ssd_temp_c: Optional[float] = None
    throttle_pkg: Optional[int] = None
    throttle_core: Optional[int] = None
    load1: Optional[float] = None
    cpu_busy: Optional[int] = None
    cpu_total: Optional[int] = None
    disk_read_ms: Optional[int] = None
    disk_write_ms: Optional[int] = None
    disk_ios: Optional[int] = None

    @property
    def moment(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class Reading:
    """One record differenced against its predecessor.

    This is the analysable form: instantaneous power rather than a counter,
    utilisation rather than jiffies, and a throttled flag rather than a count.
    """

    timestamp: float
    power_w: Optional[float] = None
    cpu_temp_c: Optional[float] = None
    board_temp_c: Optional[float] = None
    ssd_temp_c: Optional[float] = None
    cpu_util: Optional[float] = None
    load1: Optional[float] = None
    #: True when a throttle counter moved since the previous reading.
    throttled: bool = False
    #: Mean service time per I/O in ms over the interval, or None.
    io_service_ms: Optional[float] = None

    @property
    def moment(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc).replace(tzinfo=None)


@dataclass
class ParseResult:
    records: list = field(default_factory=list)
    #: Counts of what was thrown away and why, so a node emitting garbage is
    #: visible in the harvest log instead of just looking quiet.
    malformed: int = 0
    wrong_version: int = 0
    out_of_range: int = 0

    @property
    def dropped(self) -> int:
        return self.malformed + self.wrong_version + self.out_of_range

    def summary(self) -> str:
        parts = [f"{len(self.records)} samples"]
        if self.malformed:
            parts.append(f"{self.malformed} malformed")
        if self.wrong_version:
            parts.append(f"{self.wrong_version} unknown schema")
        if self.out_of_range:
            parts.append(f"{self.out_of_range} out of range")
        return ", ".join(parts)


@dataclass(frozen=True)
class Rollup:
    """A time bucket's worth of readings, reduced to what is worth storing."""

    bucket_start: datetime
    sample_count: int
    power_w_mean: Optional[float] = None
    power_w_max: Optional[float] = None
    cpu_temp_c_mean: Optional[float] = None
    cpu_temp_c_max: Optional[float] = None
    board_temp_c_mean: Optional[float] = None
    ssd_temp_c_mean: Optional[float] = None
    cpu_util_mean: Optional[float] = None
    io_service_ms_mean: Optional[float] = None
    throttled: bool = False


# --- parsing ----------------------------------------------------------------

def parse_buffer(text: str) -> ParseResult:
    """Parse a harvested telemetry buffer into records, dropping bad lines.

    Never raises. The buffer comes off hardware that may have been power-cut
    mid-write, so a truncated final line is expected rather than exceptional.
    """
    result = ParseResult()
    if not text:
        return result

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        try:
            payload = json.loads(line)
        except ValueError:
            result.malformed += 1
            continue

        if not isinstance(payload, dict):
            result.malformed += 1
            continue

        version = payload.get("v")
        if version is None or not isinstance(version, int) or version < SCHEMA_VERSION:
            result.wrong_version += 1
            continue

        record = _record_from(payload, result)
        if record is not None:
            result.records.append(record)

    # The collector appends in order, but a clock step can reorder timestamps
    # within a buffer. Sorting here means the differencing stage can assume
    # monotonic time without having to defend against it again.
    result.records.sort(key=lambda r: r.timestamp)
    return result


def _record_from(payload: dict, result: ParseResult) -> Optional[Record]:
    timestamp = _number(payload.get("ts"))
    if timestamp is None or not (TIMESTAMP_BOUNDS[0] <= timestamp <= TIMESTAMP_BOUNDS[1]):
        result.out_of_range += 1
        return None

    temperatures = {}
    for key, name in (
        ("t_pkg", "cpu_temp_c"),
        ("t_core_max", "core_temp_max_c"),
        ("t_board", "board_temp_c"),
        ("t_ssd", "ssd_temp_c"),
    ):
        value = _number(payload.get(key))
        if value is None:
            continue
        if not (TEMP_BOUNDS_C[0] <= value <= TEMP_BOUNDS_C[1]):
            result.out_of_range += 1
            continue
        temperatures[name] = value

    return Record(
        timestamp=timestamp,
        uptime_s=_number(payload.get("up")),
        rapl_uj=_integer(payload.get("rapl_uj")),
        rapl_max_uj=_integer(payload.get("rapl_max")),
        throttle_pkg=_integer(payload.get("thr_pkg")),
        throttle_core=_integer(payload.get("thr_core")),
        load1=_number(payload.get("load1")),
        cpu_busy=_integer(payload.get("cpu_busy")),
        cpu_total=_integer(payload.get("cpu_total")),
        disk_read_ms=_integer(payload.get("dr_ms")),
        disk_write_ms=_integer(payload.get("dw_ms")),
        disk_ios=_integer(payload.get("dio")),
        **temperatures,
    )


def _number(value) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    if math.isnan(value) or math.isinf(value):
        return None
    return float(value)


def _integer(value) -> Optional[int]:
    number = _number(value)
    return None if number is None else int(number)


# --- differencing -----------------------------------------------------------

def to_readings(records: Sequence[Record]) -> list:
    """Difference cumulative counters into instantaneous values.

    The first record of every run produces no reading — there is nothing to
    difference it against. A run ends wherever the series is discontinuous:
    too large a time gap, a clock that went backwards, or an uptime that reset,
    all of which mean the counters restarted and cannot be spanned.
    """
    readings = []
    previous: Optional[Record] = None

    for record in records:
        if previous is not None and _continuous(previous, record):
            reading = _difference(previous, record)
            if reading is not None:
                readings.append(reading)
        previous = record

    return readings


def _continuous(previous: Record, current: Record) -> bool:
    delta = current.timestamp - previous.timestamp
    if delta <= 0 or delta > MAX_SAMPLE_GAP_S:
        return False
    # An uptime that went backwards is a reboot: every counter restarted at
    # zero, and differencing across it would read as a huge negative or,
    # worse, a plausible-looking small positive.
    if (
        previous.uptime_s is not None
        and current.uptime_s is not None
        and current.uptime_s < previous.uptime_s
    ):
        return False
    return True


def _difference(previous: Record, current: Record) -> Optional[Reading]:
    dt = current.timestamp - previous.timestamp

    power = None
    if previous.rapl_uj is not None and current.rapl_uj is not None:
        power = thermal.power_watts(
            previous.rapl_uj, current.rapl_uj, dt, current.rapl_max_uj
        )
        if power is not None and power > MAX_PLAUSIBLE_POWER_W:
            power = None

    utilisation = None
    if None not in (previous.cpu_busy, current.cpu_busy, previous.cpu_total, current.cpu_total):
        busy = current.cpu_busy - previous.cpu_busy
        total = current.cpu_total - previous.cpu_total
        if total > 0 and busy >= 0:
            utilisation = min(1.0, busy / total)

    io_service = None
    if None not in (previous.disk_ios, current.disk_ios):
        ios = current.disk_ios - previous.disk_ios
        read_ms = _delta(previous.disk_read_ms, current.disk_read_ms)
        write_ms = _delta(previous.disk_write_ms, current.disk_write_ms)
        if ios > 0 and read_ms is not None and write_ms is not None:
            io_service = (read_ms + write_ms) / ios

    throttled = _counter_moved(previous.throttle_pkg, current.throttle_pkg) or _counter_moved(
        previous.throttle_core, current.throttle_core
    )

    return Reading(
        timestamp=current.timestamp,
        power_w=power,
        cpu_temp_c=current.cpu_temp_c,
        board_temp_c=current.board_temp_c,
        ssd_temp_c=current.ssd_temp_c,
        cpu_util=utilisation,
        load1=current.load1,
        throttled=throttled,
        io_service_ms=io_service,
    )


def _delta(previous: Optional[int], current: Optional[int]) -> Optional[int]:
    if previous is None or current is None:
        return None
    delta = current - previous
    return delta if delta >= 0 else None


def _counter_moved(previous: Optional[int], current: Optional[int]) -> bool:
    if previous is None or current is None:
        return False
    return current > previous


# --- handing off to the thermal fit ------------------------------------------

def thermal_samples(readings: Iterable[Reading]) -> list:
    """The subset of readings the thermal fit can use, in its own type.

    Requires both power and die temperature; a reading missing either says
    nothing about thermal resistance. Note that readings are *not* averaged
    on the way through — see core.thermal on why that would bias theta.
    """
    return [
        thermal.Sample(
            timestamp=r.timestamp,
            power_w=r.power_w,
            temp_c=r.cpu_temp_c,
            throttled=r.throttled,
        )
        for r in readings
        if r.power_w is not None and r.cpu_temp_c is not None
    ]


def split_runs(readings: Sequence[Reading], max_gap_s: float = MAX_SAMPLE_GAP_S) -> list:
    """Break readings into contiguous runs at every discontinuity.

    A window handed to the fit must be continuous: the model relates each
    sample to the one before it, so a three-hour hole in the middle would be
    read as an impossibly fast thermal response.
    """
    runs = []
    current = []
    for reading in readings:
        if current and reading.timestamp - current[-1].timestamp > max_gap_s:
            runs.append(current)
            current = []
        current.append(reading)
    if current:
        runs.append(current)
    return runs


def windows(readings: Sequence[Reading], window_s: float, min_samples: int) -> list:
    """Split into fitting windows, discarding any that are too short.

    Windows do not overlap. Overlapping ones would produce correlated theta
    estimates, and the drift detector downstream treats its inputs as
    independent observations.
    """
    result = []
    for run in split_runs(readings):
        if not run:
            continue
        start = run[0].timestamp
        current = []
        for reading in run:
            if reading.timestamp - start >= window_s and len(current) >= min_samples:
                result.append(current)
                current = []
                start = reading.timestamp
            current.append(reading)
        if len(current) >= min_samples:
            result.append(current)
    return result


# --- rollups ----------------------------------------------------------------

def rollups(readings: Sequence[Reading], bucket_s: int = ROLLUP_SECONDS) -> list:
    """Aggregate readings into fixed time buckets for storage.

    What survives the harvest. Raw samples are fitted and discarded; these are
    what the charts read months later, so the bucket carries maxima alongside
    means — a peak temperature averaged away is exactly the thing somebody
    will later wish had been kept.
    """
    if not readings:
        return []

    buckets: dict = {}
    for reading in readings:
        key = int(reading.timestamp // bucket_s) * bucket_s
        buckets.setdefault(key, []).append(reading)

    out = []
    for key in sorted(buckets):
        group = buckets[key]
        out.append(Rollup(
            bucket_start=datetime.fromtimestamp(key, tz=timezone.utc).replace(tzinfo=None),
            sample_count=len(group),
            power_w_mean=_mean(r.power_w for r in group),
            power_w_max=_max(r.power_w for r in group),
            cpu_temp_c_mean=_mean(r.cpu_temp_c for r in group),
            cpu_temp_c_max=_max(r.cpu_temp_c for r in group),
            board_temp_c_mean=_mean(r.board_temp_c for r in group),
            ssd_temp_c_mean=_mean(r.ssd_temp_c for r in group),
            cpu_util_mean=_mean(r.cpu_util for r in group),
            io_service_ms_mean=_mean(r.io_service_ms for r in group),
            throttled=any(r.throttled for r in group),
        ))
    return out


def _mean(values: Iterable[Optional[float]]) -> Optional[float]:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return round(sum(present) / len(present), 3)


def _max(values: Iterable[Optional[float]]) -> Optional[float]:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return round(max(present), 3)
