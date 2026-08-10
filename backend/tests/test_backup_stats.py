from datetime import date, datetime, timedelta

import pytest

from core import backup_stats as bs
from core.backup_stats import FailureCategory as FC


# --- failure classification -------------------------------------------------

@pytest.mark.parametrize("log,expected", [
    ("ssh: connect to host 10.0.0.5 port 22: No route to host", FC.UNREACHABLE),
    ("ssh: connect to host node1 port 22: Connection refused", FC.UNREACHABLE),
    ("Could not resolve hostname node-7", FC.UNREACHABLE),
    ("root@10.0.0.5: Permission denied (publickey).", FC.AUTH),
    ("Host key verification failed.", FC.AUTH),
    ("OSError: [Errno 28] No space left on device", FC.DISK_FULL),
    ("Failed to create/acquire the lock /data/borg/fleet/lock.exclusive", FC.REPO_LOCKED),
    ("borg.remote.RemoteRepository.RPCError: Repository check needed", FC.REPO_ERROR),
    ("Backup aborted: Node HASP license status is inactive", FC.LICENCE),
    ("Task revoked, terminating", FC.CANCELLED),
    ("borg: read timed out after 300 seconds", FC.TIMEOUT),
    ("stat: /var/opt/edge: Input/output error", FC.SOURCE_ERROR),
])
def test_classification_recognises_the_common_failures(log, expected):
    assert bs.classify_failure(log) == expected


def test_unrecognised_output_is_not_forced_into_a_bucket():
    assert bs.classify_failure("something nobody has seen before") == FC.UNKNOWN


@pytest.mark.parametrize("log", [None, "", "   "])
def test_missing_log_classifies_as_unknown(log):
    assert bs.classify_failure(log) == FC.UNKNOWN


def test_classification_is_case_insensitive():
    assert bs.classify_failure("NO ROUTE TO HOST") == FC.UNREACHABLE


def test_a_lock_timeout_is_a_lock_problem_not_a_timeout():
    """Both words appear; the specific cause has to win."""
    log = "LockTimeout: Failed to create/acquire the lock, timed out after 60s"
    assert bs.classify_failure(log) == FC.REPO_LOCKED


def test_only_the_tail_of_a_huge_log_is_examined():
    """A path mentioned in a file listing must not decide the category."""
    noise = "A /etc/some/no such file or directory-ish-name\n" * 5000
    log = noise + "\nssh: connect to host 10.0.0.5 port 22: No route to host"
    assert bs.classify_failure(log) == FC.UNREACHABLE


# --- rates and percentiles --------------------------------------------------

def test_success_rate_is_a_rounded_percentage():
    assert bs.success_rate(11, 25) == 44.0
    assert bs.success_rate(1, 3) == 33.3


def test_success_rate_of_nothing_is_unknown_not_zero():
    assert bs.success_rate(0, 0) is None


def test_percentile_interpolates():
    values = [1, 2, 3, 4]
    assert bs.percentile(values, 50) == pytest.approx(2.5)
    assert bs.percentile(values, 0) == 1.0
    assert bs.percentile(values, 100) == 4.0


def test_percentile_of_a_single_sample_is_that_sample():
    assert bs.percentile([7.5], 95) == 7.5


def test_percentile_of_nothing_is_none():
    assert bs.percentile([], 50) is None
    assert bs.median([]) is None


def test_percentile_ignores_missing_samples():
    assert bs.median([None, 4, None, 6]) == pytest.approx(5.0)


# --- failure streaks --------------------------------------------------------

def test_streak_counts_back_from_the_newest_run():
    assert bs.failure_streak(["FAILED", "FAILED", "SUCCESS", "FAILED"]) == 2


def test_a_node_whose_last_run_succeeded_has_no_streak():
    assert bs.failure_streak(["SUCCESS", "FAILED", "FAILED"]) == 0


def test_a_node_that_never_succeeded_streaks_all_the_way():
    assert bs.failure_streak(["FAILED"] * 4) == 4
    assert bs.failure_streak([]) == 0


# --- backup windows ---------------------------------------------------------

def test_a_daytime_window_is_its_plain_length():
    assert bs.window_minutes("02:00", "05:00") == 180


def test_an_overnight_window_wraps_past_midnight():
    """22:00-05:00 is seven hours, not minus seventeen."""
    assert bs.window_minutes("22:00", "05:00") == 420


def test_equal_endpoints_mean_the_whole_day():
    assert bs.window_minutes("03:00", "03:00") == 1440


@pytest.mark.parametrize("start,end", [("bogus", "05:00"), ("02:00", None), ("25:00", "05:00"), ("02:70", "05:00")])
def test_an_unparseable_window_is_none(start, end):
    assert bs.window_minutes(start, end) is None


def test_window_usage_is_the_share_consumed():
    # 90 minutes inside a 3 hour window
    assert bs.window_usage(5400, 180) == pytest.approx(0.5)


def test_a_run_can_overrun_its_window():
    assert bs.window_usage(14400, 180) == pytest.approx(1.333, rel=1e-3)


@pytest.mark.parametrize("duration,minutes", [(None, 180), (5400, None), (5400, 0)])
def test_window_usage_is_none_without_both_sides(duration, minutes):
    assert bs.window_usage(duration, minutes) is None


# --- ages -------------------------------------------------------------------

def test_days_since_measures_backwards_from_now():
    now = datetime(2026, 8, 10, 12, 0, 0)
    assert bs.days_since(now - timedelta(days=3, hours=12), now) == pytest.approx(3.5)


def test_a_timestamp_in_the_future_reports_zero_not_a_negative_age():
    """Clock skew between the orchestrator and the database is not an error."""
    now = datetime(2026, 8, 10, 12, 0, 0)
    assert bs.days_since(now + timedelta(hours=2), now) == 0.0


def test_a_node_that_never_backed_up_has_no_age():
    assert bs.days_since(None, datetime(2026, 8, 10)) is None


# --- staleness --------------------------------------------------------------

@pytest.mark.parametrize("interval,days", [
    ("weekly", 7), ("monthly", 30), ("quarterly", 91), ("yearly", 365), ("daily", 1),
    ("Monthly", 30), (" weekly ", 7),
])
def test_known_schedules_map_to_their_length(interval, days):
    assert bs.expected_interval_days(interval) == days


@pytest.mark.parametrize("interval", [None, "", "fortnightly"])
def test_an_unknown_schedule_falls_back_to_weekly(interval):
    """Assuming a node should have run makes a dead node visible; assuming it
    should not would hide one."""
    assert bs.expected_interval_days(interval) == 7


def test_a_node_inside_its_schedule_is_not_stale():
    assert bs.is_stale(days_since_success=5.0, interval_days=7) is False


def test_one_missed_run_is_tolerated():
    """10 days on a weekly schedule is late but within the grace factor."""
    assert bs.is_stale(days_since_success=10.0, interval_days=7) is False


def test_missing_more_than_the_grace_period_is_stale():
    assert bs.is_stale(days_since_success=11.0, interval_days=7) is True


def test_a_monthly_node_is_not_stale_at_three_weeks():
    """The whole point of reading the group's interval rather than a flat cut-off."""
    assert bs.is_stale(days_since_success=21.0, interval_days=30) is False


def test_a_node_that_never_succeeded_is_stale():
    assert bs.is_stale(days_since_success=None, interval_days=365) is True


# --- capacity ---------------------------------------------------------------

def test_inflow_averages_over_the_whole_window_including_idle_days():
    """A weekly fleet must not look as though it grows at its backup-day rate."""
    sizes = {date(2026, 8, 1): 7_000_000_000}
    assert bs.daily_inflow_bytes(sizes, window_days=7) == pytest.approx(1_000_000_000)


def test_no_inflow_is_none():
    assert bs.daily_inflow_bytes({}, window_days=30) is None
    assert bs.daily_inflow_bytes({date(2026, 8, 1): 0}, window_days=30) is None


def test_inflow_needs_a_positive_window():
    assert bs.daily_inflow_bytes({date(2026, 8, 1): 100}, window_days=0) is None


def test_days_until_full_divides_free_space_by_growth():
    assert bs.days_until_full(100_000_000_000, 1_000_000_000) == pytest.approx(100.0)


@pytest.mark.parametrize("free,growth", [(None, 1000.0), (1000, None), (1000, 0), (1000, -5.0)])
def test_a_repository_that_is_not_growing_never_fills(free, growth):
    assert bs.days_until_full(free, growth) is None


def test_a_full_disk_reports_zero_days_rather_than_a_negative():
    assert bs.days_until_full(0, 1_000_000.0) == 0.0


def test_projected_date_is_now_plus_the_runway():
    now = datetime(2026, 8, 10, 12, 0, 0)
    projected = bs.projected_full_date(now, 10_000_000_000, 1_000_000_000)
    assert projected == now + timedelta(days=10)


def test_a_forecast_past_a_decade_is_reported_as_no_forecast():
    """A date in 2190 reads as a bug, not as reassurance."""
    now = datetime(2026, 8, 10)
    assert bs.projected_full_date(now, 10_000_000_000_000, 1_000.0) is None


# --- ratio and top-N --------------------------------------------------------

def test_deduplication_ratio_is_source_bytes_per_stored_byte():
    assert bs.deduplication_ratio(13_200_000_000, 5_670_000_000) == 2.33


def test_ratio_of_an_empty_repository_is_none_not_one():
    assert bs.deduplication_ratio(0, 0) is None


def test_base_totals_ignore_repeat_backups_of_unchanged_data():
    """A weekly node that barely changes writes gigabytes of source for a few
    hundred kilobytes of new data. Summing every archive would measure how
    rarely it changes, not how well the repository packs the fleet."""
    rows = [
        (1, 3_160_000_000, 986_000_000),   # base backup
        (1, 3_160_000_000, 366_000),       # incrementals, nearly nothing new
        (1, 3_170_000_000, 328_000),
        (1, 3_170_000_000, 381_000),
    ]
    original, deduplicated, nodes = bs.base_archive_totals(rows)

    assert original == 3_160_000_000
    assert deduplicated == 986_000_000
    assert nodes == 1


def test_base_totals_sum_across_nodes():
    rows = [
        (1, 3_000_000_000, 900_000_000),
        (1, 3_000_000_000, 500_000),
        (2, 2_000_000_000, 400_000_000),
        (2, 2_000_000_000, 300_000),
    ]
    assert bs.base_archive_totals(rows) == (5_000_000_000, 1_300_000_000, 2)


def test_the_base_is_picked_by_size_not_by_age():
    """The oldest surviving row is not stable: retention prunes the real first
    backup and deletes its history row, leaving an incremental behind. Picking
    the largest contribution survives that."""
    rows = [
        (1, 3_160_000_000, 366_000),       # oldest survivor, an incremental
        (1, 3_160_000_000, 986_000_000),   # the actual base
    ]
    original, deduplicated, _ = bs.base_archive_totals(rows)

    assert deduplicated == 986_000_000
    assert bs.deduplication_ratio(original, deduplicated) == pytest.approx(3.2, rel=0.01)


def test_base_totals_of_an_empty_fleet():
    assert bs.base_archive_totals([]) == (0, 0, 0)


def test_a_node_with_no_deduplicated_bytes_still_counts_as_a_node():
    assert bs.base_archive_totals([(1, 1000, 0)]) == (1000, 0, 1)


def test_top_counts_orders_by_frequency():
    values = ["A", "B", "A", "C", "A", "B"]
    assert bs.top_counts(values, limit=2) == [("A", 3), ("B", 2)]


def test_top_counts_breaks_ties_alphabetically_so_the_panel_does_not_shuffle():
    assert bs.top_counts(["B", "A"], limit=2) == [("A", 1), ("B", 1)]


def test_top_counts_of_nothing_is_empty():
    assert bs.top_counts([]) == []
