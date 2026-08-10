"""Turning backup history into the numbers the Archives page reports.

Everything here is pure arithmetic over plain values — no database, no borg, no
clock of its own. The router pulls rows, hands the relevant fields over, and
gets back numbers it can serialise. That keeps the interesting parts testable
without a fleet to run against.

Two things are deliberately conservative:

* Rates and percentiles return None rather than a made-up zero when there is
  nothing to divide. A panel showing "n/a" is honest; one showing "0%" is not.
* The capacity forecast is an upper bound on growth, not a prediction. It sums
  what backups add and ignores what retention prunes away, so it runs out of
  disk sooner on paper than in reality. Erring that way is the useful direction.
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta
from typing import Iterable, Mapping, Optional, Sequence

#: A backup that took longer than this share of its window is worth flagging
#: before it actually starts overrunning.
WINDOW_WARN_FRACTION = 0.8

#: How far past its schedule a node may drift before it counts as stale. One
#: missed run is normal — a node was off, a window was busy. Half again as long
#: as the interval means it has missed one and is late for the next.
STALENESS_GRACE = 1.5

#: What to assume for a node with no group, or a group whose interval we do not
#: recognise. Weekly is the shortest schedule the UI offers.
DEFAULT_INTERVAL_DAYS = 7

#: Nominal length of each schedule the scheduler supports.
INTERVAL_DAYS = {
    "daily": 1,
    "weekly": 7,
    "monthly": 30,
    "quarterly": 91,
    "yearly": 365,
}

_SECONDS_PER_DAY = 86400
_MINUTES_PER_DAY = 1440


class FailureCategory:
    """Why a backup failed, coarse enough to be worth counting.

    The point is to answer "are these fourteen failures one problem or
    fourteen?" — not to reproduce borg's exit codes.
    """

    UNREACHABLE = "UNREACHABLE"
    AUTH = "AUTH"
    REPO_LOCKED = "REPO_LOCKED"
    REPO_ERROR = "REPO_ERROR"
    DISK_FULL = "DISK_FULL"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    SOURCE_ERROR = "SOURCE_ERROR"
    LICENCE = "LICENCE"
    UNKNOWN = "UNKNOWN"


#: Ordered: the first category whose pattern appears wins. Order matters where
#: phrases overlap — a lock timeout is a lock problem, not a generic timeout,
#: and an unreachable host is reported before the ssh layer calls it an auth
#: failure.
_FAILURE_PATTERNS: Sequence[tuple[str, Sequence[str]]] = (
    (FailureCategory.DISK_FULL, (
        "no space left on device",
        "disk quota exceeded",
    )),
    (FailureCategory.REPO_LOCKED, (
        "failed to create/acquire the lock",
        "lock.exclusive",
        "locktimeout",
        "repository is already locked",
    )),
    (FailureCategory.UNREACHABLE, (
        "no route to host",
        "connection refused",
        "connection timed out",
        "connection closed by remote host",
        "host is down",
        "network is unreachable",
        "name or service not known",
        "could not resolve hostname",
    )),
    (FailureCategory.AUTH, (
        "permission denied",
        "host key verification failed",
        "too many authentication failures",
        "no supported authentication methods",
        "remote host identification has changed",
    )),
    (FailureCategory.LICENCE, (
        "hasp license status is inactive",
        "licence update is required",
        "license update is required",
    )),
    (FailureCategory.REPO_ERROR, (
        "does not exist",
        "is not a valid repository",
        "data integrity error",
        "repository check needed",
        "unexpected rpc data format",
    )),
    (FailureCategory.CANCELLED, (
        "keyboardinterrupt",
        "terminated by signal",
        "sigterm",
        "aborted by user",
        "soft_time_limit",
        "task revoked",
    )),
    (FailureCategory.TIMEOUT, (
        "timed out",
        "timeout",
    )),
    (FailureCategory.SOURCE_ERROR, (
        "no such file or directory",
        "input/output error",
        "stale file handle",
    )),
)


def classify_failure(log_output: Optional[str]) -> str:
    """Bucket a failed run by what its log says went wrong.

    Only the tail of the log is examined: borg prints the fatal error last, and
    a full backup log can be megabytes of file listings that would otherwise
    dominate the match.
    """
    if not log_output:
        return FailureCategory.UNKNOWN

    haystack = log_output[-8000:].lower()
    for category, patterns in _FAILURE_PATTERNS:
        if any(pattern in haystack for pattern in patterns):
            return category
    return FailureCategory.UNKNOWN


def success_rate(successes: int, total: int) -> Optional[float]:
    """Percentage of runs that succeeded, or None when nothing ran."""
    if total <= 0:
        return None
    return round(successes * 100.0 / total, 1)


def median(values: Sequence[float]) -> Optional[float]:
    return percentile(values, 50)


def percentile(values: Sequence[float], p: float) -> Optional[float]:
    """Linear-interpolated percentile, the same convention numpy uses.

    Written out rather than pulled from a dependency so the backend keeps no
    numeric stack it does not otherwise need.
    """
    cleaned = sorted(v for v in values if v is not None)
    if not cleaned:
        return None
    if len(cleaned) == 1:
        return float(cleaned[0])

    rank = (len(cleaned) - 1) * (p / 100.0)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return float(cleaned[int(rank)])
    return float(cleaned[low] + (cleaned[high] - cleaned[low]) * (rank - low))


def failure_streak(statuses: Sequence[str]) -> int:
    """How many runs in a row have failed, counting back from the newest.

    `statuses` must be ordered newest first. A node whose last success was
    months ago but which failed only twice since is a different problem from
    one that has failed thirty times running.
    """
    streak = 0
    for status in statuses:
        if status == "SUCCESS":
            break
        streak += 1
    return streak


def parse_hhmm(value: Optional[str]) -> Optional[int]:
    """Minutes past midnight for an "HH:MM" string, or None if unparseable."""
    if not value:
        return None
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", value)
    if not match:
        return None
    hours, minutes = int(match.group(1)), int(match.group(2))
    if hours > 23 or minutes > 59:
        return None
    return hours * 60 + minutes


def window_minutes(start_time: Optional[str], end_time: Optional[str]) -> Optional[int]:
    """Length of a backup window, handling the usual overnight case.

    A 22:00-05:00 window is seven hours, not minus seventeen. Equal endpoints
    mean a full day rather than nothing: that is how the scheduler reads them.
    """
    start = parse_hhmm(start_time)
    end = parse_hhmm(end_time)
    if start is None or end is None:
        return None
    if end == start:
        return _MINUTES_PER_DAY
    if end > start:
        return end - start
    return _MINUTES_PER_DAY - start + end


def window_usage(duration_seconds: Optional[float], minutes: Optional[int]) -> Optional[float]:
    """Share of the window a run consumed. 1.0 means it exactly filled it."""
    if duration_seconds is None or not minutes or minutes <= 0:
        return None
    return duration_seconds / (minutes * 60.0)


def days_since(then: Optional[datetime], now: datetime) -> Optional[float]:
    """Age in days, floored at zero so clock skew cannot report the future."""
    if then is None:
        return None
    return max(0.0, (now - then).total_seconds() / _SECONDS_PER_DAY)


def expected_interval_days(interval: Optional[str]) -> int:
    """How often a group is supposed to back up, in days.

    Unknown or missing schedules fall back to weekly rather than to "never":
    assuming a node should have run recently produces a visible warning, while
    assuming it should not would quietly hide a dead node.
    """
    if not interval:
        return DEFAULT_INTERVAL_DAYS
    return INTERVAL_DAYS.get(interval.strip().lower(), DEFAULT_INTERVAL_DAYS)


def is_stale(
    days_since_success: Optional[float],
    interval_days: int,
    grace: float = STALENESS_GRACE,
) -> bool:
    """Whether a node has gone too long without a *successful* backup.

    A node that has never succeeded is stale by definition — that is the case
    the whole panel exists to surface.
    """
    if days_since_success is None:
        return True
    return days_since_success > interval_days * grace


def daily_inflow_bytes(
    sizes_by_day: Mapping[date, int],
    window_days: int,
) -> Optional[float]:
    """Mean bytes added to the repository per day over the window.

    Days with no backup count as zero rather than being skipped — a fleet that
    backs up weekly grows a seventh as fast as one that backs up nightly, and
    averaging only over active days would hide that.
    """
    if window_days <= 0:
        return None
    total = sum(sizes_by_day.values())
    if total <= 0:
        return None
    return total / float(window_days)


def days_until_full(
    free_bytes: Optional[int],
    growth_bytes_per_day: Optional[float],
) -> Optional[float]:
    """How long the free space lasts at the current inflow.

    None when there is no measurable growth — an unchanging repository never
    fills up, and reporting "infinity" as a number invites it being formatted
    as one.
    """
    if free_bytes is None or not growth_bytes_per_day or growth_bytes_per_day <= 0:
        return None
    return max(0.0, free_bytes / growth_bytes_per_day)


def projected_full_date(
    now: datetime,
    free_bytes: Optional[int],
    growth_bytes_per_day: Optional[float],
) -> Optional[datetime]:
    days = days_until_full(free_bytes, growth_bytes_per_day)
    if days is None:
        return None
    # Beyond a decade the forecast says nothing except "not soon", and a date in
    # 2190 makes the panel look broken.
    if days > 3650:
        return None
    return now + timedelta(days=days)


def deduplication_ratio(original: int, deduplicated: int) -> Optional[float]:
    """How many bytes of source each stored byte represents."""
    if deduplicated <= 0:
        return None
    return round(original / float(deduplicated), 2)


def top_counts(values: Iterable[str], limit: int = 5) -> list[tuple[str, int]]:
    """The `limit` most common values, ties broken alphabetically for stability."""
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return ordered[:limit]
