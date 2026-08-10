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
