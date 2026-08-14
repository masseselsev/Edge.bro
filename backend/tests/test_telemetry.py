import json
from datetime import datetime

import pytest

from core import telemetry, thermal

# Two consecutive lines captured from a live node on
# 2026-08-12. Kept verbatim so the parser is exercised against what the
# collector actually emits on real hardware, not only against what the tests
# think it emits.
# A realistic epoch. The parser rejects timestamps outside 2020-2100, so a
# 1970-era fixture value would be dropped as a broken node clock.
BASE_TS = 1_786_000_000

REAL_LINES = (
    '{"v":1,"ts":1786520135,"up":71105.80,"rapl_uj":198343604665,'
    '"rapl_max":262143328850,"t_pkg":42.0,"t_core_max":38.0,"t_board":27.8,'
    '"thr_pkg":0,"thr_core":0,"load1":1.30,"cpu_busy":4993658,'
    '"cpu_total":56476733,"dr_ms":8510,"dw_ms":507514,"dio":543434}\n'
    '{"v":1,"ts":1786520195,"up":71165.80,"rapl_uj":198757604665,'
    '"rapl_max":262143328850,"t_pkg":43.0,"t_core_max":39.0,"t_board":27.8,'
    '"thr_pkg":0,"thr_core":0,"load1":1.20,"cpu_busy":4995658,'
    '"cpu_total":56482733,"dr_ms":8510,"dw_ms":507600,"dio":543500}\n'
)


def line(**overrides):
    payload = {
        "v": 1, "ts": BASE_TS, "up": 5000.0,
        "rapl_uj": 1_000_000_000, "rapl_max": 262143328850,
        "t_pkg": 45.0, "t_board": 28.0, "t_ssd": 37.0,
        "thr_pkg": 0, "thr_core": 0, "load1": 0.5,
        "cpu_busy": 100_000, "cpu_total": 1_000_000,
        "dr_ms": 1000, "dw_ms": 2000, "dio": 500,
    }
    payload.update(overrides)
    return json.dumps(payload)


def buffer_of(*payloads):
    return "\n".join(payloads) + "\n"


# --- parsing ----------------------------------------------------------------

def test_a_real_capture_from_the_node_parses():
    result = telemetry.parse_buffer(REAL_LINES)

    assert result.dropped == 0
    assert len(result.records) == 2
    first = result.records[0]
    assert first.cpu_temp_c == 42.0
    assert first.board_temp_c == 27.8
    assert first.rapl_uj == 198343604665
    assert first.ssd_temp_c is None  # drivetemp was not loaded on that capture


def test_an_empty_buffer_is_not_an_error():
    result = telemetry.parse_buffer("")
    assert result.records == []
    assert result.dropped == 0


def test_blank_lines_are_ignored_without_being_counted_as_damage():
    result = telemetry.parse_buffer("\n\n" + line() + "\n\n")
    assert len(result.records) == 1
    assert result.dropped == 0


def test_a_truncated_final_line_costs_one_sample_not_the_harvest():
    """Expected rather than exceptional: the node can lose power mid-write."""
    text = buffer_of(line(), line(ts=(BASE_TS + 60))) + '{"v":1,"ts":%d,"rapl' % (BASE_TS + 120)

    result = telemetry.parse_buffer(text)

    assert len(result.records) == 2
    assert result.malformed == 1


def test_lines_from_an_older_schema_are_dropped_and_counted():
    result = telemetry.parse_buffer(buffer_of(line(v=0), line()))
    assert len(result.records) == 1
    assert result.wrong_version == 1


def test_a_line_with_no_timestamp_is_unusable():
    payload = json.loads(line())
    del payload["ts"]
    result = telemetry.parse_buffer(json.dumps(payload))
    assert result.records == []
    assert result.dropped == 1


@pytest.mark.parametrize("ts", [
    99999999999999,      # ValueError: year 3170843 is out of range
    10**18,              # OSError: value too large for defined data type
    253402300800,        # ValueError: year 10000 is out of range
    0,                   # the epoch itself is not a plausible sample time
    -1,
])
def test_a_broken_node_clock_cannot_crash_the_harvest(ts):
    """A roadside unit whose RTC battery has died reboots with a garbage clock,
    and datetime.fromtimestamp raises on those values. The harvester has
    already drained and deleted the node's buffer by then, so letting that
    propagate would lose the batch — and keep losing every batch for as long
    as the clock stayed wrong."""
    result = telemetry.parse_buffer(line(ts=ts))

    assert result.records == []
    assert result.out_of_range == 1


def test_a_rejected_timestamp_does_not_stop_the_rest_of_the_buffer():
    text = buffer_of(line(ts=BASE_TS), line(ts=99999999999999), line(ts=(BASE_TS + 60)))

    result = telemetry.parse_buffer(text)

    assert len(result.records) == 2
    assert result.out_of_range == 1


def test_every_accepted_timestamp_survives_the_whole_pipeline():
    """The bounds exist to make `.moment` and the rollups total, so nothing
    downstream has to defend against a date it cannot represent."""
    for ts in telemetry.TIMESTAMP_BOUNDS:
        result = telemetry.parse_buffer(line(ts=ts))
        assert len(result.records) == 1
        assert isinstance(result.records[0].moment, datetime)

    readings = [
        telemetry.Reading(timestamp=float(telemetry.TIMESTAMP_BOUNDS[0]), power_w=5.0, cpu_temp_c=40.0),
        telemetry.Reading(timestamp=float(telemetry.TIMESTAMP_BOUNDS[1]), power_w=5.0, cpu_temp_c=40.0),
    ]
    assert len(telemetry.rollups(readings)) == 2


def test_an_impossible_temperature_is_dropped_but_the_sample_is_kept():
    """A single bad sensor must not cost the power reading beside it."""
    result = telemetry.parse_buffer(line(t_pkg=3000.0))

    assert len(result.records) == 1
    assert result.records[0].cpu_temp_c is None
    assert result.records[0].rapl_uj is not None
    assert result.out_of_range == 1


@pytest.mark.parametrize("garbage", ["not json", "[]", "null", '"a string"', "{"])
def test_garbage_lines_are_counted_not_raised(garbage):
    result = telemetry.parse_buffer(garbage)
    assert result.records == []
    assert result.malformed == 1


def test_absent_sensors_leave_none_rather_than_zero():
    """The collector omits keys it cannot read; zero would be a real reading."""
    result = telemetry.parse_buffer('{"v":1,"ts":%d}' % BASE_TS)

    record = result.records[0]
    assert record.cpu_temp_c is None
    assert record.rapl_uj is None
    assert record.load1 is None


def test_records_come_back_in_time_order_even_if_the_clock_stepped():
    result = telemetry.parse_buffer(buffer_of(
        line(ts=(BASE_TS + 120)), line(ts=BASE_TS), line(ts=(BASE_TS + 60)),
    ))
    assert [r.timestamp for r in result.records] == [BASE_TS, (BASE_TS + 60), (BASE_TS + 120)]


def test_the_summary_names_what_was_dropped():
    result = telemetry.parse_buffer(buffer_of(line(), "junk", line(v=0)))
    summary = result.summary()
    assert "1 samples" in summary
    assert "malformed" in summary
    assert "unknown schema" in summary


# --- differencing -----------------------------------------------------------

def test_power_comes_from_differencing_the_energy_counter():
    # 900 J over 60 s = 15 W
    text = buffer_of(
        line(ts=BASE_TS, rapl_uj=1_000_000_000),
        line(ts=(BASE_TS + 60), rapl_uj=1_900_000_000),
    )
    readings = telemetry.to_readings(telemetry.parse_buffer(text).records)

    assert len(readings) == 1
    assert readings[0].power_w == pytest.approx(15.0)


def test_the_first_record_of_a_run_yields_no_reading():
    """There is nothing to difference it against."""
    readings = telemetry.to_readings(telemetry.parse_buffer(line()).records)
    assert readings == []


def test_cpu_utilisation_comes_from_differencing_jiffies():
    text = buffer_of(
        line(ts=BASE_TS, cpu_busy=1000, cpu_total=10_000),
        line(ts=(BASE_TS + 60), cpu_busy=1500, cpu_total=12_000),
    )
    readings = telemetry.to_readings(telemetry.parse_buffer(text).records)

    # 500 busy of 2000 total = 25%
    assert readings[0].cpu_util == pytest.approx(0.25)


def test_io_service_time_is_milliseconds_per_completed_io():
    text = buffer_of(
        line(ts=BASE_TS, dr_ms=1000, dw_ms=1000, dio=100),
        line(ts=(BASE_TS + 60), dr_ms=1300, dw_ms=1500, dio=200),
    )
    readings = telemetry.to_readings(telemetry.parse_buffer(text).records)

    # (300 + 500) ms over 100 I/Os
    assert readings[0].io_service_ms == pytest.approx(8.0)


def test_a_reboot_breaks_the_run_rather_than_producing_a_bogus_delta():
    """Uptime going backwards means every counter restarted at zero."""
    text = buffer_of(
        line(ts=BASE_TS, up=5000.0, rapl_uj=900_000_000),
        line(ts=(BASE_TS + 60), up=30.0, rapl_uj=BASE_TS),
    )
    readings = telemetry.to_readings(telemetry.parse_buffer(text).records)
    assert readings == []


def test_a_long_gap_breaks_the_run():
    """The node was off, or the buffer was trimmed; counters cannot be spanned."""
    text = buffer_of(
        line(ts=BASE_TS, rapl_uj=1_000_000_000),
        line(ts=(BASE_TS + 100_000), rapl_uj=9_000_000_000),
    )
    readings = telemetry.to_readings(telemetry.parse_buffer(text).records)
    assert readings == []


def test_a_throttle_counter_that_moved_marks_the_reading():
    text = buffer_of(
        line(ts=BASE_TS, thr_pkg=0),
        line(ts=(BASE_TS + 60), thr_pkg=3),
    )
    readings = telemetry.to_readings(telemetry.parse_buffer(text).records)
    assert readings[0].throttled is True


def test_a_static_throttle_counter_does_not_mark_the_reading():
    """The counter is cumulative; a node that throttled last week is not
    throttling now."""
    text = buffer_of(line(ts=BASE_TS, thr_pkg=7), line(ts=(BASE_TS + 60), thr_pkg=7))
    readings = telemetry.to_readings(telemetry.parse_buffer(text).records)
    assert readings[0].throttled is False


def test_a_wrapped_energy_counter_is_unwrapped():
    text = buffer_of(
        line(ts=BASE_TS, rapl_uj=262_143_000_000, rapl_max=262143328850),
        line(ts=(BASE_TS + 60), rapl_uj=600_000_000, rapl_max=262143328850),
    )
    readings = telemetry.to_readings(telemetry.parse_buffer(text).records)

    assert readings[0].power_w is not None
    assert 0 < readings[0].power_w < telemetry.MAX_PLAUSIBLE_POWER_W


def test_the_real_capture_differences_to_a_plausible_wattage():
    readings = telemetry.to_readings(telemetry.parse_buffer(REAL_LINES).records)

    assert len(readings) == 1
    # 414 MJ over 60 s on a 15 W part: ~6.9 W, which is what the node idles at.
    assert readings[0].power_w == pytest.approx(6.9, abs=0.2)
    assert readings[0].cpu_temp_c == 43.0


# --- windowing --------------------------------------------------------------

def readings_at(count, step=60.0, start=float(BASE_TS), power=8.0, temp=45.0):
    return [
        telemetry.Reading(timestamp=start + i * step, power_w=power, cpu_temp_c=temp)
        for i in range(count)
    ]


def test_windows_are_split_at_the_requested_length():
    readings = readings_at(400)
    result = telemetry.windows(readings, window_s=3600, min_samples=30)

    assert len(result) >= 6
    assert all(len(w) >= 30 for w in result)


def test_windows_do_not_overlap():
    """Overlapping windows would make theta estimates correlated, and the
    drift detector treats them as independent observations."""
    readings = readings_at(400)
    result = telemetry.windows(readings, window_s=3600, min_samples=30)

    seen = set()
    for window in result:
        for reading in window:
            assert reading.timestamp not in seen
            seen.add(reading.timestamp)


def test_a_window_shorter_than_the_minimum_is_discarded():
    result = telemetry.windows(readings_at(10), window_s=3600, min_samples=60)
    assert result == []


def test_a_gap_starts_a_new_run_rather_than_spanning_the_hole():
    """A three-hour hole in a window would read as an impossibly fast
    thermal response."""
    early = readings_at(100, start=float(BASE_TS))
    late = readings_at(100, start=float(BASE_TS + 50_000))

    runs = telemetry.split_runs(early + late)

    assert len(runs) == 2
    assert len(runs[0]) == 100


# --- handing off to the fit --------------------------------------------------

def test_only_readings_with_both_power_and_temperature_reach_the_fit():
    readings = [
        telemetry.Reading(timestamp=1.0, power_w=8.0, cpu_temp_c=45.0),
        telemetry.Reading(timestamp=2.0, power_w=None, cpu_temp_c=45.0),
        telemetry.Reading(timestamp=3.0, power_w=8.0, cpu_temp_c=None),
    ]
    samples = telemetry.thermal_samples(readings)

    assert len(samples) == 1
    assert isinstance(samples[0], thermal.Sample)


def test_the_throttled_flag_survives_into_the_fit_samples():
    readings = [telemetry.Reading(timestamp=1.0, power_w=8.0, cpu_temp_c=45.0, throttled=True)]
    assert telemetry.thermal_samples(readings)[0].throttled is True


def test_a_parsed_buffer_fits_end_to_end_to_the_right_theta():
    """The whole chain: simulate a known system, serialise it the way the
    collector would, parse it back, and check theta survives the round trip."""
    theta, tau, ambient, dt = 1.5, 2100.0, 25.0, 60.0
    powers = [4.0 + 8.0 * (i % 17) / 16.0 for i in range(400)]
    truth = thermal.simulate(theta, tau, ambient, powers, dt)

    energy_uj = 1_000_000_000
    lines = []
    for i, sample in enumerate(truth):
        lines.append(json.dumps({
            "v": 1,
            "ts": BASE_TS + int(i * dt),
            "up": 10_000.0 + i * dt,
            "rapl_uj": energy_uj,
            "rapl_max": 262143328850,
            "t_pkg": round(sample.temp_c, 1),
            "thr_pkg": 0,
        }))
        # The counter advances by the energy the *next* interval will report.
        energy_uj += int(sample.power_w * dt * 1_000_000)

    parsed = telemetry.parse_buffer("\n".join(lines))
    readings = telemetry.to_readings(parsed.records)
    result = thermal.fit(telemetry.thermal_samples(readings))

    assert result.ok, result.rejection
    assert result.theta_c_per_w == pytest.approx(theta, rel=0.15)


# --- rollups ----------------------------------------------------------------

def test_rollups_bucket_by_the_configured_interval():
    readings = readings_at(60, step=60.0, start=float(BASE_TS))
    result = telemetry.rollups(readings, bucket_s=900)

    assert all(isinstance(r.bucket_start, datetime) for r in result)
    assert sum(r.sample_count for r in result) == 60
    assert result == sorted(result, key=lambda r: r.bucket_start)


def test_buckets_are_aligned_to_absolute_time_not_to_the_first_sample():
    """Alignment to the epoch grid is what lets two nodes' rollups be compared
    in the same bucket — which the cohort detector depends on. A grid anchored
    on each node's first sample would put every node on its own offset."""
    bucket = 900
    early = telemetry.rollups(readings_at(5, start=float(BASE_TS)), bucket_s=bucket)
    late = telemetry.rollups(readings_at(5, start=float(BASE_TS + 120)), bucket_s=bucket)

    assert early[0].bucket_start == late[0].bucket_start
    assert int(early[0].bucket_start.timestamp()) % bucket == 0


def test_a_rollup_keeps_the_maximum_alongside_the_mean():
    """A peak temperature averaged away is exactly what somebody will later
    wish had been kept."""
    readings = [
        telemetry.Reading(timestamp=float(BASE_TS), power_w=5.0, cpu_temp_c=40.0),
        telemetry.Reading(timestamp=float(BASE_TS + 60), power_w=15.0, cpu_temp_c=80.0),
    ]
    rollup = telemetry.rollups(readings, bucket_s=900)[0]

    assert rollup.cpu_temp_c_mean == pytest.approx(60.0)
    assert rollup.cpu_temp_c_max == pytest.approx(80.0)
    assert rollup.power_w_max == pytest.approx(15.0)


def test_a_rollup_is_throttled_if_any_reading_in_it_was():
    readings = [
        telemetry.Reading(timestamp=float(BASE_TS), power_w=5.0, cpu_temp_c=40.0),
        telemetry.Reading(timestamp=float(BASE_TS + 60), power_w=5.0, cpu_temp_c=40.0, throttled=True),
    ]
    assert telemetry.rollups(readings, bucket_s=900)[0].throttled is True


def test_missing_values_do_not_drag_a_rollup_mean_down():
    """Averaging None as zero would invent a cold reading."""
    readings = [
        telemetry.Reading(timestamp=float(BASE_TS), cpu_temp_c=40.0),
        telemetry.Reading(timestamp=float(BASE_TS + 60), cpu_temp_c=None),
        telemetry.Reading(timestamp=float(BASE_TS + 120), cpu_temp_c=50.0),
    ]
    rollup = telemetry.rollups(readings, bucket_s=900)[0]

    assert rollup.cpu_temp_c_mean == pytest.approx(45.0)
    assert rollup.power_w_mean is None


def test_rolling_up_nothing_yields_nothing():
    assert telemetry.rollups([]) == []
