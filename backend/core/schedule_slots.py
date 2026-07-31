"""Single source of truth for *when* a node is scheduled to run.

Both the scheduler (which actually triggers backups) and the scheduler-load
endpoint (which predicts upcoming load for the UI) need to answer "does this
node run at this local time, and where in the window does it land?".

These used to be two independent copies of the same arithmetic, which had
silently drifted apart — most visibly for quarterly groups, where the load map
disagreed with the real scheduler for every single node. Keep the answer here
so the two can no longer diverge.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import hashlib
import zoneinfo

from sqlalchemy.orm import Session

import models


def deterministic_hash(value: str) -> int:
    """
    Computes a deterministic integer hash from a string using MD5.
    Avoids Python's randomized built-in hash().
    """
    return int(hashlib.md5(value.encode('utf-8')).hexdigest(), 16)


def get_tzinfo(tz_name: str, db: Session) -> zoneinfo.ZoneInfo:
    """Resolves a timezone name, falling back to the global setting then UTC."""
    if not tz_name or tz_name == 'Browser Local':
        settings = db.query(models.Settings).first()
        if settings and settings.timezone and settings.timezone != 'Browser Local':
            tz_name = settings.timezone
        else:
            tz_name = 'UTC'
    try:
        return zoneinfo.ZoneInfo(tz_name)
    except Exception:
        return zoneinfo.ZoneInfo('UTC')


# Intervals that ignore the calendar and run on every tick inside the window.
TEST_INTERVALS = ("10min", "30min")


def week_of_month(day_of_month: int) -> int:
    """Week 1-4 of the month; the 29th onwards folds into week 4."""
    return min(4, ((day_of_month - 1) // 7) + 1)


@dataclass(frozen=True)
class WindowBounds:
    start_mins: int
    end_mins: int
    duration_minutes: int

    @property
    def duration_hours(self) -> int:
        """Whole hours in the window, floor, minimum 1 — used for stagger."""
        return max(1, self.duration_minutes // 60)

    def contains(self, local_mins: int) -> bool:
        if self.start_mins < self.end_mins:
            return self.start_mins <= local_mins < self.end_mins
        if self.start_mins > self.end_mins:
            return local_mins >= self.start_mins or local_mins < self.end_mins
        return True  # start == end means a full 24h window

    def is_past(self, local_mins: int) -> bool:
        """True once the window has closed for the current cycle."""
        if self.start_mins < self.end_mins:
            return local_mins >= self.end_mins
        if self.start_mins > self.end_mins:
            return self.end_mins <= local_mins < self.start_mins
        return False  # a 24h window never closes


def parse_window(start_time: str, end_time: str) -> WindowBounds:
    """Parses "HH:MM" bounds, falling back to 02:00-05:00 on malformed input."""
    try:
        start_h, start_m = map(int, start_time.split(":"))
        end_h, end_m = map(int, end_time.split(":"))
    except Exception:
        start_h, start_m, end_h, end_m = 2, 0, 5, 0

    start_mins = start_h * 60 + start_m
    end_mins = end_h * 60 + end_m

    if start_mins < end_mins:
        duration = end_mins - start_mins
    elif start_mins > end_mins:
        duration = (1440 - start_mins) + end_mins
    else:
        duration = 1440

    return WindowBounds(start_mins=start_mins, end_mins=end_mins, duration_minutes=duration)


@dataclass(frozen=True)
class NodeSlot:
    """Where a single node lands within its group's recurrence."""
    day_of_week: int                      # 0 = Monday
    week_of_month: Optional[int]          # 1-4; None when the interval ignores weeks
    month_of_quarter: Optional[int]       # 0-2; quarterly only
    hour_offset: int                      # hours after window start
    minute_offset: int                    # minutes within that hour

    @property
    def stagger_offset_mins(self) -> int:
        return self.hour_offset * 60 + self.minute_offset


def node_slot(group, hostname: str, window: WindowBounds) -> NodeSlot:
    """Computes the node's slot. Deterministic — same node always same slot."""
    node_hash = deterministic_hash(hostname)
    day_index = node_hash % 7 if group.randomize_days else 0

    if group.interval in TEST_INTERVALS:
        return NodeSlot(
            day_of_week=day_index,
            week_of_month=None,
            month_of_quarter=None,
            hour_offset=0,
            minute_offset=0,
        )

    duration_hours = window.duration_hours
    hour_offset = node_hash % duration_hours
    minute_offset = (node_hash // duration_hours) % 60

    if group.interval == "quarterly":
        # Quarterly spreads nodes across all three months of the quarter, and
        # picks its own week/day rather than using the group's target_week.
        return NodeSlot(
            day_of_week=(node_hash // 12) % 7 if group.randomize_days else day_index,
            week_of_month=((node_hash // 3) % 4) + 1,
            month_of_quarter=node_hash % 3,
            hour_offset=hour_offset,
            minute_offset=minute_offset,
        )

    if group.interval in ("monthly", "yearly"):
        target_week = group.target_week
    else:  # weekly — no week component
        target_week = None

    return NodeSlot(
        day_of_week=day_index,
        week_of_month=target_week,
        month_of_quarter=None,
        hour_offset=hour_offset,
        minute_offset=minute_offset,
    )


def is_scheduled_on(group, hostname: str, local_dt: datetime, window: WindowBounds) -> bool:
    """Whether the node is due on the calendar date of `local_dt`.

    `local_dt` must already be in the group's timezone, and for windows that
    cross midnight it should be the window's *start* date, so a run that begins
    at 23:00 is judged against the day it started.
    """
    if group.interval in TEST_INTERVALS:
        return True

    slot = node_slot(group, hostname, window)
    local_dow = local_dt.weekday()
    local_wom = week_of_month(local_dt.day)
    local_month = local_dt.month

    if group.interval == "weekly":
        return slot.day_of_week == local_dow

    if group.interval == "monthly":
        return local_wom == slot.week_of_month and slot.day_of_week == local_dow

    if group.interval == "quarterly":
        quarter_start_month = ((local_month - 1) // 3) * 3 + 1
        target_month = quarter_start_month + slot.month_of_quarter
        return (
            local_month == target_month
            and local_wom == slot.week_of_month
            and local_dow == slot.day_of_week
        )

    if group.interval == "yearly":
        return (
            local_month == 1
            and local_wom == slot.week_of_month
            and slot.day_of_week == local_dow
        )

    return False
