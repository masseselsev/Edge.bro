import pytest

from core import transfer_speed as ts


def test_kib_per_second_converts_to_megabits():
    # 1024 KiB/s = 1 MiB/s = 8 * 1.048576 Mbit/s
    assert ts.kib_s_to_mbps(1024) == pytest.approx(8.388608)
    assert ts.kib_s_to_mbps(0) == 0.0
    assert ts.kib_s_to_mbps(None) is None


def test_average_over_a_known_transfer():
    # 12.5 MB in 10 s = 1.25 MB/s = 10 Mbit/s
    assert ts.average_mbps(12_500_000, 10.0) == pytest.approx(10.0)


@pytest.mark.parametrize("total,duration", [(0, 10.0), (1000, 0), (1000, None), (None, 10.0)])
def test_average_is_none_when_it_cannot_be_computed(total, duration):
    assert ts.average_mbps(total, duration) is None


def test_max_needs_a_long_enough_span():
    """A single pair of samples milliseconds apart says nothing about throughput."""
    tracker = ts.SpeedTracker()
    tracker.sample(0.0, 0)
    tracker.sample(0.1, 5_000_000)
    assert tracker.max_mbps is None


def test_max_picks_the_fastest_window():
    tracker = ts.SpeedTracker(window_seconds=5.0, min_span_seconds=1.0)
    # 0-10 s: slow, 1 MB/s. 10-20 s: fast, 5 MB/s.
    transferred = 0
    for second in range(0, 11):
        tracker.sample(float(second), transferred)
        transferred += 1_000_000
    for second in range(11, 21):
        tracker.sample(float(second), transferred)
        transferred += 5_000_000
    # 5 MB/s = 40 Mbit/s
    assert tracker.max_mbps == pytest.approx(40.0, rel=0.05)


def test_max_ignores_a_momentary_spike():
    """Borg emits progress several times a second; a single burst between two
    samples must not be reported as the peak."""
    tracker = ts.SpeedTracker(window_seconds=5.0, min_span_seconds=1.0)
    transferred = 0
    for tenth in range(0, 200):
        tracker.sample(tenth * 0.1, transferred)
        # One 50 MB burst inside a single 0.1 s tick.
        transferred += 50_000_000 if tenth == 100 else 100_000
    # Sustained rate is ~1 MB/s = 8 Mbit/s; the burst spread over the 5 s
    # window must stay far below the 4000 Mbit/s the raw tick would imply.
    assert tracker.max_mbps is not None
    assert tracker.max_mbps < 200


def test_observed_average_spans_first_to_last_sample():
    tracker = ts.SpeedTracker()
    tracker.sample(100.0, 0)
    tracker.sample(110.0, 12_500_000)
    assert tracker.observed_average_mbps == pytest.approx(10.0)


def test_counter_reset_does_not_produce_negative_rates():
    tracker = ts.SpeedTracker(min_span_seconds=1.0)
    tracker.sample(0.0, 10_000_000)
    tracker.sample(5.0, 20_000_000)
    tracker.sample(6.0, 0)          # counter restarted
    tracker.sample(11.0, 5_000_000)
    assert tracker.max_mbps is not None
    assert tracker.max_mbps > 0


def test_no_samples_yields_nothing():
    tracker = ts.SpeedTracker()
    assert tracker.max_mbps is None
    assert tracker.observed_average_mbps is None
    assert tracker.total_bytes == 0
    assert tracker.current_mbps is None


def test_current_speed_reports_the_rate_right_now():
    """What the fleet list shows while a backup runs: the rolling window, not
    the average since the start."""
    tracker = ts.SpeedTracker(window_seconds=5.0, min_span_seconds=1.0)
    transferred = 0
    for second in range(0, 11):
        tracker.sample(float(second), transferred)
        transferred += 1_250_000  # 1.25 MB/s == 10 Mbit/s

    assert tracker.current_mbps == pytest.approx(10.0)


def test_a_live_sample_survives_the_round_trip():
    """The worker writes this to Redis and the API reads it back; the pair is
    defined together so the two cannot drift apart."""
    assert ts.parse_live_sample(ts.format_live_sample(42.3, 50.0)) == (42.3, 50.0)


def test_a_live_sample_carries_an_absent_limit():
    """An unlimited node has a speed but no limit to compare it against."""
    assert ts.parse_live_sample(ts.format_live_sample(42.3, None)) == (42.3, None)


def test_a_live_sample_reads_back_from_bytes():
    """redis-py hands back bytes unless a decoding client is configured."""
    assert ts.parse_live_sample(b"42.3:50.0") == (42.3, 50.0)


@pytest.mark.parametrize("raw", [None, "", "not-a-number:50.0", b"", ":"])
def test_an_unusable_live_sample_reads_as_no_data(raw):
    """Nothing here is worth a 500 on the fleet list: a node with no readable
    sample simply shows no speed yet."""
    assert ts.parse_live_sample(raw) == (None, None)


def test_the_feed_publishes_on_its_interval_not_on_every_sample():
    """borg reports several times a second and every publish is a Redis
    write, so the feed thins them out."""
    feed = ts.LiveSpeedFeed(interval_seconds=5.0)

    # 101 readings spanning 10 seconds — ten a second, as borg reports them.
    published = [feed.sample(t / 10.0, 10.0, None) for t in range(0, 101)]

    assert sum(1 for p in published if p is not None) == 3  # t=0, t=5, t=10


def test_the_feed_publishes_the_first_reading_immediately():
    """Otherwise the fleet list shows nothing for the first interval of
    every backup."""
    feed = ts.LiveSpeedFeed(interval_seconds=5.0)
    assert feed.sample(0.0, 10.0, 50.0) == ts.format_live_sample(10.0, 50.0)


def test_the_feed_says_nothing_before_a_speed_is_known():
    """The window needs a span before it can state a rate, and 'no reading
    yet' must not be published as a reading."""
    feed = ts.LiveSpeedFeed(interval_seconds=5.0)
    assert feed.sample(0.0, None, 50.0) is None
    # Still due, so the first real reading goes out as soon as there is one.
    assert feed.sample(0.5, 10.0, 50.0) == ts.format_live_sample(10.0, 50.0)


def test_a_sample_missing_its_separator_still_yields_the_speed():
    """Read leniently, the way the `backup_running:` key beside it already
    accepts both its old and current shapes. A reading we can still use is
    better than discarding it over a separator."""
    assert ts.parse_live_sample("42.3") == (42.3, None)


def test_current_speed_follows_a_slowdown_while_the_peak_remembers_it():
    """The distinction that makes it worth publishing: a transfer that has
    slowed to a crawl must read as slow now, even though it once ran fast."""
    tracker = ts.SpeedTracker(window_seconds=5.0, min_span_seconds=1.0)
    transferred = 0
    for second in range(0, 11):          # 10 Mbit/s
        tracker.sample(float(second), transferred)
        transferred += 1_250_000
    for second in range(11, 22):         # then a tenth of that
        tracker.sample(float(second), transferred)
        transferred += 125_000

    assert tracker.current_mbps == pytest.approx(1.0)
    assert tracker.max_mbps == pytest.approx(10.0)


def test_parse_progress_line():
    line = (
        '{"type": "archive_progress", "original_size": 100, "compressed_size": 90, '
        '"deduplicated_size": 80, "nfiles": 3, "path": "/etc/hosts", "time": 1234.5}'
    )
    kind, payload = ts.parse_borg_log_line(line)
    assert kind == ts.LineKind.PROGRESS
    assert payload["deduplicated_size"] == 80
    assert payload["time"] == 1234.5


def test_parse_log_message_keeps_text_for_humans():
    line = '{"type": "log_message", "levelname": "WARNING", "message": "file changed", "time": 1.0}'
    kind, payload = ts.parse_borg_log_line(line)
    assert kind == ts.LineKind.MESSAGE
    assert ts.render_message(payload) == "WARNING: file changed"


def test_parse_passes_through_plain_text():
    kind, payload = ts.parse_borg_log_line("ssh: connect to host 10.0.0.1 port 22: No route to host")
    assert kind == ts.LineKind.PLAIN
    assert "No route to host" in payload["message"]


@pytest.mark.parametrize("line", ["", "   ", "\n"])
def test_parse_skips_blank_lines(line):
    kind, _ = ts.parse_borg_log_line(line)
    assert kind == ts.LineKind.SKIP


def test_parse_discards_noisy_progress_chatter():
    """progress_percent/progress_message carry no throughput data and would
    flood the task log."""
    for t in ("progress_percent", "progress_message"):
        kind, _ = ts.parse_borg_log_line('{"type": "%s", "message": "x", "time": 1.0}' % t)
        assert kind == ts.LineKind.SKIP


def test_format_mbps_is_readable():
    assert ts.format_mbps(None) == "n/a"
    assert ts.format_mbps(0.0) == "0.00 Mbit/s"
    assert ts.format_mbps(123.456) == "123.46 Mbit/s"


def test_limit_utilisation_reports_whether_a_cap_binds():
    # 10 Mbit/s cap, peaked at 9.6 -> the cap is doing the limiting
    assert ts.limit_is_binding(measured_mbps=9.6, limit_mbps=10.0) is True
    # Peaked at 4 against the same cap -> something else is the bottleneck
    assert ts.limit_is_binding(measured_mbps=4.0, limit_mbps=10.0) is False
    assert ts.limit_is_binding(measured_mbps=None, limit_mbps=10.0) is None
    assert ts.limit_is_binding(measured_mbps=9.6, limit_mbps=None) is None


def test_a_little_over_the_cap_still_counts_as_bound_by_it():
    """Sampling noise puts a capped transfer slightly either side of the cap."""
    assert ts.limit_is_binding(measured_mbps=10.5, limit_mbps=10.0) is True


def test_running_far_above_the_cap_proves_the_cap_did_not_bind():
    """Throughput is derived from borg's deduplicated_size counter, which
    advances for chunks already in the repository that never cross the wire.
    A heavily deduplicated run can therefore report several times the cap —
    which means the cap was not the bottleneck, not that it was."""
    assert ts.limit_is_binding(measured_mbps=110.0, limit_mbps=20.48) is False


def test_node_rate_limit_overrides_the_group():
    assert ts.resolve_rate_limit(500, 2000) == (500, "node")


def test_group_rate_limit_applies_when_the_node_has_none():
    assert ts.resolve_rate_limit(None, 2000) == (2000, "group")


def test_no_limit_anywhere_means_unlimited():
    assert ts.resolve_rate_limit(None, None) == (0, None)


@pytest.mark.parametrize("node_limit", [0, None])
def test_zero_on_the_node_is_not_treated_as_a_cap(node_limit):
    """0 KiB/s would stall the transfer entirely; treat it as 'not set'."""
    assert ts.resolve_rate_limit(node_limit, 2000) == (2000, "group")
