import json
import os
from datetime import datetime, timedelta

import pytest

from core import smart
from core.smart import Grade, Protocol

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def sata_report(**overrides):
    """A healthy Samsung 870 EVO, the drive in the fleet's EMBC-5000 units."""
    attributes = {
        5: 0,      # Reallocated_Sector_Ct
        177: 99,   # Wear_Leveling_Count, normalised, counts down from 100
        179: 0,    # Used_Rsvd_Blk_Cnt_Tot (SSD-native bad-block signal)
        183: 0,    # Runtime_Bad_Block (SSD-native bad-block signal)
        187: 0,    # Reported_Uncorrect
        197: 0,    # Current_Pending_Sector (HDD-era; kept for that coverage)
        198: 0,    # Offline_Uncorrectable (HDD-era; kept for that coverage)
        199: 0,    # UDMA_CRC_Error_Count
        241: 2_000_000_000,  # Total_LBAs_Written
    }
    attributes.update(overrides.pop("attributes", {}))

    table = []
    for attr_id, raw in attributes.items():
        entry = {"id": attr_id, "name": f"attr{attr_id}", "raw": {"value": raw}}
        if attr_id == 177:
            entry["value"] = raw       # normalised value, not a raw count
            entry["raw"] = {"value": 0}
        table.append(entry)

    report = {
        "device": {"type": "sat"},
        "model_name": "Samsung SSD 870 EVO 500GB",
        "serial_number": "S1234567",
        "firmware_version": "SVT01B6Q",
        "smart_status": {"passed": True},
        "temperature": {"current": 42},
        "power_on_time": {"hours": 8000},
        "logical_block_size": 512,
        "ata_smart_attributes": {"table": table},
    }
    report.update(overrides)
    return report


def nvme_report(**overrides):
    log = {
        "critical_warning": 0,
        "temperature": 45,
        "available_spare": 100,
        "available_spare_threshold": 10,
        "percentage_used": 4,
        "data_units_written": 100_000_000,
        "media_errors": 0,
        "unsafe_shutdowns": 3,
    }
    log.update(overrides.pop("log", {}))

    report = {
        "device": {"type": "nvme"},
        "model_name": "Some NVMe 1TB",
        "smart_status": {"passed": True},
        "power_on_time": {"hours": 5000},
        "nvme_smart_health_information_log": log,
    }
    report.update(overrides)
    return report


# --- parsing ----------------------------------------------------------------

def test_a_sata_report_is_recognised_and_unpacked():
    reading = smart.parse(sata_report())

    assert reading.protocol is Protocol.SATA
    assert reading.model == "Samsung SSD 870 EVO 500GB"
    assert reading.health_passed is True
    assert reading.temperature_c == 42
    assert reading.power_on_hours == 8000


def test_sata_written_bytes_come_from_lbas_times_block_size():
    reading = smart.parse(sata_report())
    assert reading.written_bytes == 2_000_000_000 * 512


def test_sata_wear_is_read_from_the_normalised_levelling_count():
    """Vendors report life *remaining* counting down from 100."""
    reading = smart.parse(sata_report(attributes={177: 82}))
    assert reading.percent_used == pytest.approx(18.0)


def test_an_nvme_report_is_recognised_and_unpacked():
    reading = smart.parse(nvme_report())

    assert reading.protocol is Protocol.NVME
    assert reading.percent_used == 4
    assert reading.temperature_c == 45


def test_nvme_written_bytes_use_the_512000_byte_data_unit():
    reading = smart.parse(nvme_report())
    assert reading.written_bytes == 100_000_000 * 512_000


def test_an_unrecognised_report_does_not_raise():
    """One odd drive must not take down a poll across a thousand nodes."""
    reading = smart.parse({"device": {"type": "scsi"}, "model_name": "Weird"})
    assert reading.protocol is Protocol.UNKNOWN
    assert reading.parse_error is not None


@pytest.mark.parametrize("garbage", [None, "", [], 42])
def test_garbage_input_is_reported_not_raised(garbage):
    assert smart.parse(garbage).parse_error is not None


# --- scoring ----------------------------------------------------------------

def test_a_healthy_drive_scores_well():
    health = smart.score(smart.parse(sata_report()))

    assert health.grade is Grade.OK
    assert health.score >= 95
    assert health.overrides == []


def test_the_score_is_the_worst_subscore_not_an_average():
    """A drive can be 99% unworn and still be in trouble. Averaging would let
    a good wear figure hide a bad integrity one."""
    health = smart.score(smart.parse(sata_report(attributes={197: 8})))

    wear = next(s for s in health.subscores if s.name == "wear")
    assert wear.score > 95          # barely worn
    assert health.score <= 30       # and still condemned


def test_a_failed_self_assessment_overrides_everything():
    report = sata_report()
    report["smart_status"] = {"passed": False}
    health = smart.score(smart.parse(report))

    assert health.score == 0
    assert health.grade is Grade.REPLACE
    assert any("FAILED" in o for o in health.overrides)


def test_pending_sectors_cap_the_score():
    health = smart.score(smart.parse(sata_report(attributes={197: 1})))

    assert health.score <= 30
    assert any("Unreadable sectors" in o for o in health.overrides)


def test_offline_uncorrectable_sectors_cap_the_score():
    health = smart.score(smart.parse(sata_report(attributes={198: 4})))
    assert health.score <= 30


def test_spare_below_the_drives_own_threshold_caps_the_score():
    health = smart.score(smart.parse(
        nvme_report(log={"available_spare": 5, "available_spare_threshold": 10})
    ))

    assert health.score <= 20
    assert health.grade is Grade.REPLACE
    assert any("threshold" in o for o in health.overrides)


def test_an_nvme_critical_warning_caps_the_score():
    health = smart.score(smart.parse(nvme_report(log={"critical_warning": 0x04})))
    assert health.score <= 25


def test_a_worn_drive_grades_as_watch_before_it_grades_as_replace():
    watch = smart.score(smart.parse(sata_report(attributes={177: 70})))
    assert watch.grade is Grade.WATCH

    replace = smart.score(smart.parse(sata_report(attributes={177: 20})))
    assert replace.grade is Grade.REPLACE


def test_the_breakdown_travels_with_the_score():
    """An opaque 62% that turns out to be wrong is worse than no number."""
    health = smart.score(smart.parse(sata_report()))
    names = {s.name for s in health.subscores}

    assert {"wear", "spare", "integrity", "errors", "thermal"} <= names
    assert all(isinstance(s.evidence, dict) for s in health.subscores)


# --- SSD-native bad-block attributes (179/183), not just the HDD pair ------
#
# Confirmed against a live Samsung 870 EVO in the fleet's own EMBC-5000 units:
# this drive reports neither 197 (Current_Pending_Sector) nor 198
# (Offline_Uncorrectable) at all. Without checking the SSD-native equivalents,
# "integrity" would go silently unavailable on exactly the drive the fleet
# uses, which would have meant relying on wear and CRC alone to notice a
# drive that is actively finding bad blocks.

def sata_report_without_the_hdd_pair(**overrides):
    """The fleet's actual drive's attribute set: no 197/198 at all."""
    report = sata_report(**overrides)
    table = report["ata_smart_attributes"]["table"]
    report["ata_smart_attributes"]["table"] = [e for e in table if e["id"] not in (197, 198)]
    return report


def test_integrity_falls_back_to_ssd_native_attributes_when_the_hdd_pair_is_absent():
    report = sata_report_without_the_hdd_pair()
    health = smart.score(smart.parse(report))

    integrity = next((s for s in health.subscores if s.name == "integrity"), None)
    assert integrity is not None
    assert integrity.score == 100.0


def test_reserved_blocks_consumed_caps_the_score_even_without_the_hdd_pair():
    report = sata_report_without_the_hdd_pair(attributes={179: 3})
    health = smart.score(smart.parse(report))

    assert health.score <= 30
    assert any("reserved blocks used=3" in o for o in health.overrides)


def test_runtime_bad_blocks_cap_the_score_even_without_the_hdd_pair():
    report = sata_report_without_the_hdd_pair(attributes={183: 2})
    health = smart.score(smart.parse(report))

    assert health.score <= 30
    assert any("runtime bad block=2" in o for o in health.overrides)


def test_a_drive_reporting_neither_family_at_all_has_no_integrity_subscore():
    """Genuinely nothing to check must stay None, not a fabricated 100."""
    report = sata_report()
    table = report["ata_smart_attributes"]["table"]
    report["ata_smart_attributes"]["table"] = [
        e for e in table if e["id"] not in (197, 198, 179, 183)
    ]

    health = smart.score(smart.parse(report))

    integrity = next((s for s in health.subscores if s.name == "integrity"), None)
    assert integrity is None


def test_the_real_samsung_870_evo_capture_scores_as_healthy():
    """Golden-file regression test against the fleet's actual hardware.

    Captured 2026-08-11 from a live EMBC-5000 test node (WS20240170) via
    `smartctl -j -a /dev/sda`. Guards against ever losing coverage on the
    drive the fleet actually ships.
    """
    with open(os.path.join(FIXTURES_DIR, "samsung_870_evo_smartctl.json")) as f:
        report = json.load(f)

    reading = smart.parse(report)
    assert reading.protocol is Protocol.SATA
    assert reading.model == "Samsung SSD 870 EVO 500GB"
    assert reading.percent_used == pytest.approx(1.0)

    health = smart.score(reading)
    assert health.grade is Grade.OK
    assert health.score >= 95
    assert health.overrides == []

    names = {s.name for s in health.subscores}
    assert "integrity" in names, "179/183 are present on this capture; must be picked up"


# --- temperature ------------------------------------------------------------

def test_temperature_below_the_warning_line_is_not_penalised():
    health = smart.score(smart.parse(sata_report(temperature={"current": 50})))
    thermal = next(s for s in health.subscores if s.name == "thermal")
    assert thermal.score == 100.0


def test_temperature_degrades_gradually_between_the_thresholds():
    """One degree over the warning line is not the same state as critical."""
    mid = smart.score(smart.parse(sata_report(temperature={"current": 65})),
                      temp_warn_c=60, temp_crit_c=70)
    thermal = next(s for s in mid.subscores if s.name == "thermal")
    assert 40 < thermal.score < 60


def test_thresholds_are_adjustable_because_a_unit_in_the_sun_runs_hot():
    report = sata_report(temperature={"current": 62})

    strict = smart.score(smart.parse(report), temp_warn_c=55, temp_crit_c=65)
    lenient = smart.score(smart.parse(report), temp_warn_c=70, temp_crit_c=85)

    strict_t = next(s for s in strict.subscores if s.name == "thermal")
    lenient_t = next(s for s in lenient.subscores if s.name == "thermal")
    assert strict_t.score < lenient_t.score == 100.0


# --- advisories -------------------------------------------------------------

def test_crc_errors_are_an_advisory_and_never_scored_as_wear():
    """They mean a cable, not a dying disk. Scoring them would send someone to
    buy an SSD to fix a loose SATA lead."""
    health = smart.score(smart.parse(sata_report(attributes={199: 240})))

    assert health.grade is Grade.OK
    assert health.score >= 95
    assert any("cable" in a for a in health.advisories)


def test_frequent_unsafe_shutdowns_are_flagged():
    health = smart.score(smart.parse(nvme_report(log={"unsafe_shutdowns": 4000})))
    assert any("unsafe shutdown" in a for a in health.advisories)


def test_a_missing_health_verdict_is_called_out():
    report = sata_report()
    del report["smart_status"]
    health = smart.score(smart.parse(report))
    assert any("no overall-health verdict" in a for a in health.advisories)


# --- wear projection --------------------------------------------------------

def series(pairs, start=None):
    start = start or datetime(2026, 1, 1)
    return [(start + timedelta(days=d), p) for d, p in pairs]


def test_the_projection_answers_when_not_how_worn():
    # 10 percentage points over 100 days = 0.1 pt/day. At 20% used, the
    # remaining 80 points take 800 days.
    now = datetime(2026, 4, 11)
    projection = smart.project_wear(series([(0, 10), (50, 15), (100, 20)]), now=now)

    assert projection.percent_used_per_day == pytest.approx(0.1, rel=0.01)
    assert projection.days_remaining == pytest.approx(800, rel=0.01)
    assert projection.projected_date is not None


def test_the_projection_carries_its_own_derivation():
    """So the UI can show the arithmetic rather than ask for trust."""
    projection = smart.project_wear(series([(0, 10), (100, 20)]))

    assert projection.current_percent_used == 20
    assert projection.observation_days == pytest.approx(100.0)
    assert projection.observation_points == 2
    assert projection.percent_used_per_day is not None


def test_too_little_history_yields_no_date_and_says_why():
    projection = smart.project_wear(series([(0, 10), (3, 11)]))

    assert projection.projected_date is None
    assert "days of history" in projection.unavailable_reason


def test_a_wear_indicator_that_has_not_moved_yields_no_rate():
    """percentage_used steps in whole points; below one point it is noise."""
    projection = smart.project_wear(series([(0, 4), (60, 4)]))

    assert projection.projected_date is None
    assert "has not moved" in projection.unavailable_reason


def test_a_single_reading_is_not_a_trend():
    projection = smart.project_wear(series([(0, 10)]))
    assert projection.unavailable_reason == "need at least two readings"


def test_an_outlier_reading_does_not_swing_the_date():
    """Median of pairwise slopes: one misread point must not move the answer."""
    honest = smart.project_wear(series([(0, 10), (30, 13), (60, 16), (90, 19)]))
    spiked = smart.project_wear(series([(0, 10), (30, 13), (45, 61), (60, 16), (90, 19)]))

    assert spiked.percent_used_per_day == pytest.approx(
        honest.percent_used_per_day, rel=0.35
    )


def test_a_projection_past_a_decade_is_reported_as_no_date():
    projection = smart.project_wear(series([(0, 1), (400, 2)]))
    assert projection.projected_date is None
    assert projection.days_remaining is not None


def test_a_drive_already_at_full_wear_projects_to_now():
    projection = smart.project_wear(series([(0, 90), (100, 100)]))
    assert projection.days_remaining == 0.0


# --- write rate -------------------------------------------------------------

def test_write_rate_is_reported_for_context():
    points = series([(0, 1_000_000_000), (100, 5_000_000_000)])
    assert smart.bytes_per_day(points) == pytest.approx(40_000_000)


def test_write_rate_needs_two_readings():
    assert smart.bytes_per_day(series([(0, 1000)])) is None


def test_a_counter_that_went_backwards_yields_no_rate():
    points = series([(0, 5_000_000_000), (100, 1_000_000_000)])
    assert smart.bytes_per_day(points) is None
