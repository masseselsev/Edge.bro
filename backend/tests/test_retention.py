"""core.retention must agree with `borg prune`, because it replaces it.

The fleet's prune stopped shelling out to borg once per node and now decides
here and issues one `borg delete`. Every assertion below is derived from
borg 1.2.4's `prune_within` / `prune_split` in `borg/helpers/misc.py`, not from
how retention "ought" to work — where the two differ, borg wins, because borg
is what created the archives and what operators reason about.

A mistake here deletes backups. There is no second copy.
"""
from datetime import datetime, timedelta

import pytest

from core.retention import (
    Archive,
    PRUNING_PATTERNS,
    RetentionRules,
    borg_prune_args,
    parse_within,
    prune_split,
    rules_from_policy,
    select,
)

NOW = datetime(2026, 6, 15, 3, 0, 0)


def daily_archives(count, host="node-01", start=NOW, step=timedelta(days=1)):
    """`count` archives, one per step, newest first at `start`."""
    return [
        Archive(name=f"{host}-{(start - step * i).strftime('%Y%m%d%H%M%S')}",
                ts=start - step * i)
        for i in range(count)
    ]


def names(archives):
    return [a.name for a in archives]


# --- prune_split: the two behaviours that are easy to get wrong ---

def test_the_newest_archive_of_each_period_is_the_one_kept():
    """Not the oldest, and not an arbitrary one — borg sorts reverse by ts."""
    same_day = [
        Archive("a-morning", datetime(2026, 6, 15, 2, 0)),
        Archive("a-evening", datetime(2026, 6, 15, 22, 0)),
        Archive("b-evening", datetime(2026, 6, 14, 22, 0)),
    ]
    keep, delete, _ = select(same_day, RetentionRules(daily=2), now=NOW)
    assert set(names(keep)) == {"a-evening", "b-evening"}
    assert names(delete) == ["a-morning"]


def test_the_oldest_archive_survives_a_rule_that_cannot_fill_its_quota():
    """borg's `rule+"[oldest]"` branch.

    Three archives against keep-daily=7: the loop keeps all three and then the
    fallback fires harmlessly. The case that matters is the one below.
    """
    archives = daily_archives(3)
    keep, delete, _ = select(archives, RetentionRules(daily=7), now=NOW)
    assert delete == []
    assert len(keep) == 3


def test_the_oldest_is_kept_even_when_it_shares_a_period_with_a_kept_archive():
    """The fallback's real purpose, and the reason it cannot be simplified away.

    Two archives on the same day with keep-daily=2: the loop sees one period,
    keeps the newest, and stops with len(keep)==1 < 2. The fallback then keeps
    the oldest as well. Deleting it — the "obvious" reading of keep-daily=2 —
    is not what borg does.
    """
    archives = [
        Archive("newer", datetime(2026, 6, 15, 22, 0)),
        Archive("older", datetime(2026, 6, 15, 2, 0)),
    ]
    keep, delete, reasons = select(archives, RetentionRules(daily=2), now=NOW)
    assert delete == []
    assert reasons["older"][0] == "daily[oldest]"


def test_a_period_already_claimed_by_an_earlier_rule_does_not_consume_a_later_quota():
    """`kept_because` is shared, and the period is skipped rather than re-kept.

    With keep-daily=1 and keep-weekly=1 over archives inside one ISO week, the
    daily rule claims the newest. The weekly rule then finds that week's newest
    is already kept, skips the period without spending its single slot, and
    falls through to its [oldest] fallback.
    """
    archives = daily_archives(3, start=datetime(2026, 6, 17, 12, 0))  # Wed/Tue/Mon
    keep, delete, reasons = select(
        archives, RetentionRules(daily=1, weekly=1), now=NOW
    )
    assert reasons[names(archives)[0]][0] == "daily"
    # The oldest is saved by weekly's fallback, so only the middle one goes.
    assert len(delete) == 1
    assert delete[0].name == names(archives)[1]


def test_zero_keeps_nothing_by_that_rule():
    """Explicit 0 is not the same as unset, and must not trigger the fallback."""
    archives = daily_archives(3)
    assert prune_split(archives, "daily", 0, {}) == []


def test_a_rule_that_fills_its_quota_does_not_also_keep_the_oldest():
    """The [oldest] fallback only fires when the loop ran out of periods.

    Ten distinct days against keep-daily=3: the loop keeps three and breaks on
    `len(keep) == n`, so the fallback's `len(keep) < n` is false and the oldest
    seven go. Contrast the same-period case below, where the loop runs to
    exhaustion and the fallback does fire.
    """
    archives = daily_archives(10)
    keep, delete, _ = select(archives, RetentionRules(daily=3), now=NOW)
    assert len(keep) == 3
    assert len(delete) == 7


# --- grandfathering: daily + weekly + monthly together ---

def test_daily_weekly_monthly_grandfathering_over_a_year():
    """The default policy, on a node backed up every day for a year."""
    archives = daily_archives(365)
    keep, delete, reasons = select(
        archives, RetentionRules(daily=7, weekly=4, monthly=6), now=NOW
    )
    assert len(keep) + len(delete) == 365
    # Seven consecutive days survive as dailies.
    daily_kept = [n for n, r in reasons.items() if r[0] == "daily"]
    assert len(daily_kept) == 7
    # Weeklies and monthlies land on distinct earlier periods.
    weekly_kept = [n for n, r in reasons.items() if r[0] == "weekly"]
    monthly_kept = [n for n, r in reasons.items() if r[0] == "monthly"]
    assert len(weekly_kept) == 4
    assert len(monthly_kept) == 6
    assert len(keep) == 17
    # Nothing is kept twice.
    assert len(set(names(keep))) == len(keep)


def test_the_most_recent_archive_is_never_deleted_under_any_sane_policy():
    """The single property an operator would notice immediately if broken."""
    archives = daily_archives(400)
    for rules in (
        RetentionRules(daily=7, weekly=4, monthly=6),
        RetentionRules(secondly=1),
        RetentionRules(secondly=5),
        RetentionRules(daily=1),
        RetentionRules(within_hours=24, secondly=1),
    ):
        keep, delete, _ = select(archives, rules, now=NOW)
        assert archives[0].name in names(keep), rules
        assert archives[0].name not in names(delete), rules


# --- keep-last / count policies ---

def test_keep_last_keeps_exactly_the_n_newest():
    """--keep-last is --keep-secondly, and no two backups share a second."""
    archives = daily_archives(10)
    keep, delete, _ = select(archives, RetentionRules(secondly=3), now=NOW)
    assert names(keep) == names(archives[:3])
    assert len(delete) == 7


def test_keep_last_larger_than_the_archive_count_deletes_nothing():
    archives = daily_archives(2)
    keep, delete, _ = select(archives, RetentionRules(secondly=10), now=NOW)
    assert delete == []


# --- keep-within / timeframe policies ---

@pytest.mark.parametrize("value,hours", [
    ("2d", 48), ("1H", 1), ("3w", 504), ("1m", 744), ("1y", 8760),
])
def test_within_intervals_match_borgs_units(value, hours):
    """Borg counts a month as 31 days and a year as 365 — erring towards keeping."""
    assert parse_within(value) == hours


@pytest.mark.parametrize("value", ["", "d", "5x", "abc", "x5"])
def test_an_unparseable_interval_is_none_rather_than_a_guess(value):
    assert parse_within(value) is None


def test_keep_within_keeps_everything_inside_the_window():
    """And the comparison is strict: an archive exactly on the boundary is out."""
    archives = daily_archives(10)
    keep, delete, _ = select(archives, RetentionRules(within_hours=72), now=NOW)
    # now, -1d and -2d are strictly newer than now-72h; -3d sits exactly on it.
    assert len(keep) == 3
    assert len(delete) == 7


def test_keep_last_1_alongside_a_window_saves_one_archive_beyond_it():
    """A consequence of the shared kept_because that is easy to misread.

    With --keep-within 72H --keep-last 1, the three archives inside the window
    are claimed by `within`. The secondly rule then walks past all three
    without spending its single slot — they are already kept — and lands on the
    first archive *outside* the window, which it keeps.

    So the timeframe policy retains one archive older than its window. That is
    borg's behaviour, and it is the safe direction: a node whose backups stop
    does not fall off a cliff the moment the window passes.
    """
    archives = daily_archives(10)
    keep, delete, reasons = select(
        archives, RetentionRules(within_hours=72, secondly=1), now=NOW
    )
    assert len(keep) == 4
    assert reasons[archives[3].name][0] == "secondly"
    assert len(delete) == 6


def test_a_timeframe_policy_keeps_the_last_archive_of_a_long_dead_node():
    """A node that stopped backing up must not lose everything to a window.

    This is why the timeframe policy pairs --keep-within with --keep-last 1.
    """
    stale = daily_archives(3, start=NOW - timedelta(days=400))
    rules = rules_from_policy({"type": "timeframe", "within_value": 3, "within_unit": "m"}, None)
    keep, delete, _ = select(stale, rules, now=NOW)
    assert len(keep) == 1
    assert keep[0].name == stale[0].name


# --- policy translation ---

def test_interval_policy_translation():
    rules = rules_from_policy(
        {"type": "interval", "keep_daily": 3, "keep_weekly": 2, "keep_monthly": 1}, None
    )
    assert (rules.daily, rules.weekly, rules.monthly) == (3, 2, 1)
    assert rules.secondly is None


def test_count_policy_translation():
    rules = rules_from_policy({"type": "count", "keep_last": 9}, None)
    assert rules.secondly == 9
    assert rules.daily is None


def test_timeframe_policy_translation():
    rules = rules_from_policy(
        {"type": "timeframe", "within_value": 2, "within_unit": "w"}, None
    )
    assert rules.within_hours == 336
    assert rules.secondly == 1


def test_a_missing_policy_falls_back_to_the_legacy_settings_columns():
    """Deployments that never opened the retention UI still run on these."""
    class LegacySettings:
        keep_daily, keep_weekly, keep_monthly = 5, 3, 2

    rules = rules_from_policy(None, LegacySettings())
    assert (rules.daily, rules.weekly, rules.monthly) == (5, 3, 2)


def test_an_empty_policy_keeps_everything_rather_than_deleting_everything():
    """The failure mode that must not exist.

    A Settings row that is missing, or whose columns are all NULL, produces no
    rules at all. Read as "nothing is protected" that deletes the entire
    repository on the next nightly run.
    """
    archives = daily_archives(50)
    class Empty:
        keep_daily = keep_weekly = keep_monthly = None

    rules = rules_from_policy(None, Empty())
    assert rules.is_empty()
    keep, delete, _ = select(archives, rules, now=NOW)
    assert delete == []
    assert len(keep) == 50


def test_no_archives_is_not_an_error():
    keep, delete, _ = select([], RetentionRules(daily=7), now=NOW)
    assert keep == [] and delete == []


# --- the flags this policy would have produced, for cross-checking by hand ---

def test_borg_prune_args_round_trip():
    rules = rules_from_policy(
        {"type": "interval", "keep_daily": 7, "keep_weekly": 4, "keep_monthly": 6}, None
    )
    assert borg_prune_args(rules) == [
        "--keep-daily", "7", "--keep-weekly", "4", "--keep-monthly", "6",
    ]


def test_borg_prune_args_uses_keep_last_for_the_secondly_rule():
    """Operators read --keep-last; --keep-secondly would be technically equal
    and confusing next to a UI that says "keep last N"."""
    assert borg_prune_args(RetentionRules(secondly=5)) == ["--keep-last", "5"]


def test_period_patterns_match_borg_exactly():
    """Copied from borg 1.2.4 PRUNING_PATTERNS. %G-%V is ISO year and ISO week
    — using %Y-%W instead would put the turn of the year in the wrong bucket.
    """
    assert PRUNING_PATTERNS == {
        "secondly": "%Y-%m-%d %H:%M:%S",
        "minutely": "%Y-%m-%d %H:%M",
        "hourly": "%Y-%m-%d %H",
        "daily": "%Y-%m-%d",
        "weekly": "%G-%V",
        "monthly": "%Y-%m",
        "yearly": "%Y",
    }


def test_weekly_buckets_use_iso_weeks_across_a_year_boundary():
    """29 Dec 2025 and 1 Jan 2026 are the same ISO week (2026-W01)."""
    archives = [
        Archive("jan01", datetime(2026, 1, 1, 12, 0)),
        Archive("dec29", datetime(2025, 12, 29, 12, 0)),
    ]
    assert archives[0].ts.strftime("%G-%V") == archives[1].ts.strftime("%G-%V")

    # Both fall in one bucket, so keep-weekly=1 keeps the newer and the older
    # goes. The quota is filled, so the [oldest] fallback does not rescue it.
    keep, delete, reasons = select(archives, RetentionRules(weekly=1), now=NOW)
    assert names(keep) == ["jan01"]
    assert names(delete) == ["dec29"]

    # Under %Y-%W they would land in different buckets (2025-52 and 2026-00)
    # and both would be kept — the bug this pattern choice avoids.
    assert archives[0].ts.strftime("%Y-%W") != archives[1].ts.strftime("%Y-%W")
