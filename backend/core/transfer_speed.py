"""Throughput measurement for borg backups.

Borg reports cumulative byte counters several times a second while an archive
is being created. Turning those into a useful number takes a little care: the
raw delta between two consecutive reports covers a fraction of a second, so a
single burst reads as an absurd peak. Rates are therefore measured over a
sliding window, and a window has to be wide enough before it counts.

Pure functions and one small class, no I/O, so the arithmetic can be tested
without running a backup.
"""
from __future__ import annotations

import json
from enum import Enum
from typing import Any, Optional

#: Bytes per second -> megabits per second. Network speeds are decimal.
_BITS_PER_BYTE = 8
_MEGA = 1_000_000
#: KiB is binary, unlike Mbit.
_BYTES_PER_KIB = 1024


class LineKind(str, Enum):
    #: Carries byte counters; feed it to SpeedTracker.
    PROGRESS = "progress"
    #: Human-readable borg log entry worth keeping in the task log.
    MESSAGE = "message"
    #: Not JSON at all — ssh errors and the like. Keep verbatim.
    PLAIN = "plain"
    #: Noise: blank lines and progress chatter with no data in it.
    SKIP = "skip"


def kib_s_to_mbps(kib_per_second: Optional[int]) -> Optional[float]:
    """Convert a borg rate limit (KiB/s) into Mbit/s."""
    if kib_per_second is None:
        return None
    return (kib_per_second * _BYTES_PER_KIB * _BITS_PER_BYTE) / _MEGA


def average_mbps(total_bytes: Optional[int], duration_seconds: Optional[float]) -> Optional[float]:
    """Mean throughput, or None when there is nothing meaningful to divide."""
    if not total_bytes or not duration_seconds or duration_seconds <= 0:
        return None
    return (total_bytes * _BITS_PER_BYTE) / (duration_seconds * _MEGA)


def format_mbps(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f} Mbit/s"


def limit_is_binding(
    measured_mbps: Optional[float],
    limit_mbps: Optional[float],
    threshold: float = 0.9,
    over_tolerance: float = 1.2,
) -> Optional[bool]:
    """Whether a configured rate limit is what actually held the transfer back.

    Answers the practical question: is this backup slow because we capped it,
    or because the link is slow? None when either side is unknown.

    Running *above* the cap is not evidence that the cap bound the transfer —
    it is evidence that it did not. That happens for real: throughput is
    derived from borg's deduplicated_size counter, which advances for chunks
    already present in the repository that never cross the wire, so a heavily
    deduplicated run can report far more than the link ever carried. Only a
    measurement sitting just under the cap means the cap is the bottleneck;
    `over_tolerance` leaves room for ordinary sampling noise above it.
    """
    if measured_mbps is None or not limit_mbps:
        return None
    return limit_mbps * threshold <= measured_mbps <= limit_mbps * over_tolerance


def parse_borg_log_line(line: str) -> tuple[LineKind, dict[str, Any]]:
    """Classify one line of borg's --log-json output on stderr."""
    stripped = line.strip()
    if not stripped:
        return LineKind.SKIP, {}

    try:
        payload = json.loads(stripped)
    except (ValueError, TypeError):
        return LineKind.PLAIN, {"message": stripped}

    if not isinstance(payload, dict):
        return LineKind.PLAIN, {"message": stripped}

    kind = payload.get("type")
    if kind == "archive_progress":
        return LineKind.PROGRESS, payload
    if kind == "log_message":
        return LineKind.MESSAGE, payload
    # progress_percent / progress_message / file_status carry no throughput and
    # would flood the task log.
    return LineKind.SKIP, payload


def render_message(payload: dict[str, Any]) -> str:
    level = payload.get("levelname")
    message = payload.get("message", "")
    return f"{level}: {message}" if level else str(message)


class SpeedTracker:
    """Tracks peak sustained throughput from cumulative byte counters."""

    def __init__(self, window_seconds: float = 5.0, min_span_seconds: float = 1.0):
        #: How far back a rate is measured over.
        self.window_seconds = window_seconds
        #: A window narrower than this is discarded as too short to be a rate.
        self.min_span_seconds = min_span_seconds
        self._samples: list[tuple[float, int]] = []
        self._max_bytes_per_second = 0.0
        self._first: Optional[tuple[float, int]] = None
        self._last: Optional[tuple[float, int]] = None
        self._baseline = 0

    def sample(self, timestamp: float, transferred_bytes: int) -> None:
        """Record a cumulative byte count observed at `timestamp`."""
        if timestamp is None or transferred_bytes is None:
            return

        # A counter that goes backwards means borg started over; treat what came
        # before as already banked so totals stay monotonic.
        if self._last is not None and transferred_bytes < self._last[1]:
            self._baseline += self._last[1]
            self._samples = []

        absolute = self._baseline + transferred_bytes

        if self._first is None:
            self._first = (timestamp, absolute)
        self._last = (timestamp, transferred_bytes)

        self._samples.append((timestamp, absolute))
        self._prune(timestamp)
        self._update_max(timestamp, absolute)

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        # Keep one sample older than the cutoff so a full-width window is
        # available as soon as enough time has passed.
        while len(self._samples) > 2 and self._samples[1][0] < cutoff:
            self._samples.pop(0)

    def _update_max(self, now: float, absolute: int) -> None:
        if not self._samples:
            return
        oldest_time, oldest_bytes = self._samples[0]
        span = now - oldest_time
        if span < self.min_span_seconds:
            return
        rate = (absolute - oldest_bytes) / span
        if rate > self._max_bytes_per_second:
            self._max_bytes_per_second = rate

    @property
    def total_bytes(self) -> int:
        if self._last is None:
            return 0
        return self._baseline + self._last[1]

    @property
    def max_mbps(self) -> Optional[float]:
        if self._max_bytes_per_second <= 0:
            return None
        return (self._max_bytes_per_second * _BITS_PER_BYTE) / _MEGA

    @property
    def observed_average_mbps(self) -> Optional[float]:
        """Mean over the span actually covered by progress reports."""
        if self._first is None or self._last is None:
            return None
        duration = self._last[0] - self._first[0]
        transferred = self.total_bytes - self._first[1]
        return average_mbps(transferred, duration)


def resolve_rate_limit(
    node_limit: Optional[int], group_limit: Optional[int]
) -> tuple[int, Optional[str]]:
    """Effective upload cap in KiB/s, and where it came from.

    Node overrides group, group overrides unlimited — the same precedence the
    rest of the node settings use. 0 means no cap.
    """
    if node_limit is not None and node_limit > 0:
        return node_limit, "node"
    if group_limit is not None and group_limit > 0:
        return group_limit, "group"
    return 0, None
