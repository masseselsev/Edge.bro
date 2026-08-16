"""Whether the repository capacity report tells the truth about sharding.

Three things here are easy to get wrong in a way that produces a confident,
useless number, so each has a test that fails loudly if the model drifts back:

* Two nodes on one repository serialize. The whole point of counting shards is
  that a lock is per repository, and a model that just divides by the shard
  count would report parallelism nobody gets.
* Raising `BORG_SHARD_COUNT` does not move existing nodes. A forecast that
  spreads today's load over tomorrow's repositories would recommend a change
  that delivers nothing.
* A deployment that has never run two backups at once cannot say what its
  storage can carry. "Not enough data" is the correct answer, and it has to
  stay distinguishable from a measured one.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from core import repo_capacity as rc


def _utc(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=timezone.utc)


def _run(node_id, shard, day_num, hours, win_start=2, win_end=5):
    return rc.ScheduledRun(
        node_id=node_id,
        shard_index=shard,
        day=date(2026, 8, day_num),
        hours=hours,
        window_start=_utc(day_num, win_start),
        window_end=_utc(day_num, win_end),
    )


# ─────────────────────────── window arithmetic ───────────────────────────


def test_overlapping_windows_count_once():
    """Two groups sharing a window do not double the time available."""
    hours = rc.union_hours([
        (_utc(14, 2), _utc(14, 5)),
        (_utc(14, 3), _utc(14, 6)),
    ])
    assert hours == pytest.approx(4.0)


def test_touching_windows_merge_into_one_span():
    hours = rc.union_hours([
        (_utc(14, 2), _utc(14, 4)),
        (_utc(14, 4), _utc(14, 6)),
    ])
    assert hours == pytest.approx(4.0)


def test_disjoint_windows_in_different_timezones_both_count():
    """Groups six hours apart genuinely give a repository two working spans."""
    hours = rc.union_hours([
        (_utc(14, 2), _utc(14, 5)),
        (_utc(14, 20), _utc(14, 23)),
    ])
    assert hours == pytest.approx(6.0)


def test_zero_length_window_is_not_reported_as_overloaded():
    """A misconfigured window is a configuration error, not a full repository."""
    assert rc.utilization_pct(4.0, 0.0) is None


# ─────────────────────────── serialization ───────────────────────────


def test_two_nodes_on_one_repository_add_up():
    nights = rc.shard_nights([
        _run(1, shard=0, day_num=14, hours=2.0),
        _run(2, shard=0, day_num=14, hours=2.0),
    ])
    worst = rc.busiest_night_per_shard(nights)

    assert worst[0].hours == pytest.approx(4.0)
    assert worst[0].window_hours == pytest.approx(3.0)
    assert worst[0].utilization_pct == pytest.approx(133.33, abs=0.1)
    assert rc.distinct_shards_on([
        _run(1, shard=0, day_num=14, hours=2.0),
        _run(2, shard=0, day_num=14, hours=2.0),
    ]) == 1


def test_the_same_two_nodes_split_across_repositories_do_not():
    nights = rc.shard_nights([
        _run(1, shard=0, day_num=14, hours=2.0),
        _run(2, shard=1, day_num=14, hours=2.0),
    ])
    worst = rc.busiest_night_per_shard(nights)

    assert worst[0].hours == pytest.approx(2.0)
    assert worst[1].hours == pytest.approx(2.0)
    assert worst[0].utilization_pct == pytest.approx(66.67, abs=0.1)


def test_a_group_whose_nodes_all_hash_to_one_shard_has_concurrency_one():
    """Shard assignment is node_id % count, which knows nothing about groups."""
    runs = [_run(node_id, shard=0, day_num=14, hours=1.0) for node_id in (5, 10, 15)]
    assert rc.distinct_shards_on(runs) == 1


def test_the_busiest_night_wins_not_the_average():
    """A repository idle six nights and overrunning on the seventh fails weekly."""
    nights = rc.shard_nights([
        _run(1, shard=0, day_num=10, hours=0.5),
        _run(2, shard=0, day_num=11, hours=0.5),
        _run(3, shard=0, day_num=12, hours=9.0),
    ])
    worst = rc.busiest_night_per_shard(nights)

    assert worst[0].hours == pytest.approx(9.0)
    assert worst[0].day == date(2026, 8, 12)


# ─────────────────────────── capacity ───────────────────────────


def test_nodes_per_night_is_whole_nodes():
    assert rc.nodes_per_night(window_hours=8.0, node_hours=1.5) == 5
    assert rc.nodes_per_night(window_hours=1.0, node_hours=1.5) == 0


def test_sustained_capacity_exceeds_one_night_when_nodes_run_rarely():
    """A monthly node occupies a thirtieth of a slot, not a whole one."""
    nightly = rc.nodes_per_night(window_hours=8.0, node_hours=2.0)
    sustained = rc.sustained_nodes_per_shard(
        period_nights=30, window_hours=8.0, runs_per_node=1.0, node_hours=2.0
    )
    assert nightly == 4
    assert sustained == 120


def test_capacity_is_zero_rather_than_infinite_when_duration_is_unknown():
    assert rc.nodes_per_night(window_hours=8.0, node_hours=0.0) == 0
    assert rc.sustained_nodes_per_shard(30, 8.0, 1.0, 0.0) == 0


# ─────────────────────────── expansion forecast ───────────────────────────


def test_raising_the_count_does_not_relieve_the_existing_fleet():
    """The assertion the whole forecast rests on.

    A node's repository is fixed at enrolment, so an overloaded shard 0 stays
    exactly as overloaded at any higher count. Reporting relief here would
    recommend a change that delivers nothing.
    """
    outlooks = rc.project_expansion(
        shard_hours={0: 10.0},
        window_hours=8.0,
        node_hours=1.0,
        current_count=1,
        candidate_counts=[1, 2, 5, 10],
    )

    assert [o.shard_count for o in outlooks] == [1, 2, 5, 10]
    for outlook in outlooks:
        assert outlook.relieves_existing is False
        assert outlook.busiest_utilization_pct == pytest.approx(125.0), (
            "expansion reported relief for nodes that cannot move"
        )


def test_expansion_offers_headroom_for_new_nodes_only():
    """More repositories do buy something: room for enrolments still to come."""
    outlooks = rc.project_expansion(
        shard_hours={0: 4.0},
        window_hours=8.0,
        node_hours=1.0,
        current_count=1,
        candidate_counts=[1, 4],
    )
    at_one, at_four = outlooks

    # One repository: four spare hours, one hour per node.
    assert at_one.new_node_headroom == 4
    # Four: the fullest is still shard 0 with four spare hours, but new nodes
    # only land there one time in four.
    assert at_four.new_node_headroom == 16


def test_a_saturated_repository_offers_no_headroom():
    outlooks = rc.project_expansion(
        shard_hours={0: 12.0}, window_hours=8.0, node_hours=1.0,
        current_count=1, candidate_counts=[1, 5],
    )
    assert all(o.new_node_headroom == 0 for o in outlooks)


def test_lowering_the_count_is_never_projected():
    """`repo_paths` floors the count by the directories on disk; projecting a
    lower one would only suggest a change that strands archives."""
    outlooks = rc.project_expansion(
        shard_hours={0: 1.0, 1: 1.0}, window_hours=8.0, node_hours=1.0,
        current_count=2, candidate_counts=[1, 2, 3],
    )
    assert [o.shard_count for o in outlooks] == [2, 3]


# ─────────────────────────── measured storage ceiling ───────────────────────────


def _observed(start_hour, duration_hours, mbps, day=14, node_id=None):
    start = _utc(day, start_hour)
    return rc.ObservedRun(
        start=start,
        end=start + timedelta(hours=duration_hours),
        mbps=mbps,
        node_id=node_id,
    )


def test_one_node_cannot_be_two_writers():
    """Overlapping records for a single node are duplicates, not contention.

    A node backs up one archive at a time. Rows that overlap for one node come
    from a backfill, a re-import or a clock that moved, and summing them would
    report throughput the deployment never achieved at once — then recommend
    repositories on the strength of it.
    """
    ceiling = rc.measure_shared_ceiling([
        _observed(1, 2, 40.0, node_id=7),
        _observed(1, 2, 40.0, node_id=7),
        _observed(1, 2, 40.0, node_id=7),
    ])

    assert ceiling.max_observed_writers == 1
    assert ceiling.ceiling_mbps == pytest.approx(40.0)
    assert ceiling.sufficient is False


def test_different_nodes_overlapping_are_real_contention():
    ceiling = rc.measure_shared_ceiling([
        _observed(1, 2, 40.0, node_id=1),
        _observed(1, 2, 40.0, node_id=2),
        _observed(1, 2, 40.0, node_id=3),
    ])

    assert ceiling.max_observed_writers == 3
    assert ceiling.ceiling_mbps == pytest.approx(120.0)


def test_sequential_backups_cannot_measure_a_ceiling():
    """A deployment that has never run two at once has nothing to say."""
    ceiling = rc.measure_shared_ceiling([
        _observed(1, 1, 50.0),
        _observed(3, 1, 50.0),
        _observed(5, 1, 50.0),
    ])

    assert ceiling.sufficient is False
    assert ceiling.max_observed_writers == 1
    assert ceiling.saturated is False


def test_no_history_at_all_reports_nothing_rather_than_zero():
    ceiling = rc.measure_shared_ceiling([])
    assert ceiling.sufficient is False
    assert ceiling.ceiling_mbps is None


def test_concurrent_backups_measure_their_aggregate():
    """Three overlapping writers at 40 Mbit/s each is a 120 Mbit/s observation."""
    ceiling = rc.measure_shared_ceiling([
        _observed(1, 3, 40.0),
        _observed(1, 3, 40.0),
        _observed(1, 3, 40.0),
        _observed(6, 1, 40.0),
    ])

    assert ceiling.max_observed_writers == 3
    assert ceiling.ceiling_mbps == pytest.approx(120.0)
    assert ceiling.sufficient is True
    assert ceiling.saturated is False


def test_throughput_that_stops_growing_is_reported_as_saturated():
    """Adding writers without adding throughput is the shared path binding."""
    runs = [
        # One writer alone, achieving 100.
        _observed(1, 1, 100.0),
        # Two writers together, still only 100 between them.
        _observed(4, 2, 50.0),
        _observed(4, 2, 50.0),
        # A third changes nothing.
        _observed(8, 2, 33.0),
        _observed(8, 2, 33.0),
        _observed(8, 2, 34.0),
    ]
    ceiling = rc.measure_shared_ceiling(runs)

    assert ceiling.sufficient is True
    assert ceiling.saturated is True
    assert ceiling.ceiling_mbps == pytest.approx(100.0)


def test_a_ceiling_is_a_lower_bound_not_a_verdict_on_the_hardware():
    """Two writers that scale cleanly are not evidence of any limit."""
    ceiling = rc.measure_shared_ceiling([
        _observed(1, 1, 100.0),
        _observed(4, 2, 100.0),
        _observed(4, 2, 100.0),
    ])
    assert ceiling.saturated is False
    assert ceiling.ceiling_mbps == pytest.approx(200.0)


def test_partial_overlap_still_counts_the_overlapping_stretch():
    """Runs that only overlap for part of their length still contend."""
    ceiling = rc.measure_shared_ceiling([
        _observed(1, 3, 30.0),   # 01:00-04:00
        _observed(3, 3, 30.0),   # 03:00-06:00
        _observed(3, 3, 30.0),
    ])
    assert ceiling.max_observed_writers == 3
    assert ceiling.ceiling_mbps == pytest.approx(90.0)


def test_two_backups_brushing_past_each_other_do_not_count_as_concurrency():
    """A few seconds of overlap is not a sustained rate.

    Without a minimum, one backup finishing as another starts would flip the
    whole report from "not enough data" to a measured ceiling derived from a
    sliver of time.
    """
    first = rc.ObservedRun(_utc(14, 1), _utc(14, 2), 50.0)
    # Starts ten seconds before the first one ends.
    second = rc.ObservedRun(
        _utc(14, 1, 59) + timedelta(seconds=50), _utc(14, 4), 50.0
    )

    ceiling = rc.measure_shared_ceiling([first, second])
    assert ceiling.sufficient is False, "a ten-second overlap measured as contention"
    assert ceiling.max_observed_writers == 1


def test_writers_supported_needs_a_measured_ceiling():
    insufficient = rc.measure_shared_ceiling([_observed(1, 1, 50.0)])
    assert rc.writers_supported_by_storage(insufficient, 10.0) is None


def test_writers_supported_divides_the_ceiling_by_a_node():
    ceiling = rc.measure_shared_ceiling([
        _observed(1, 2, 45.0),
        _observed(1, 2, 45.0),
        _observed(4, 1, 45.0),
    ])
    assert rc.writers_supported_by_storage(ceiling, 30.0) == 3


def test_writers_supported_never_reports_zero():
    """A node slower than nothing is a division artefact, not a storage verdict."""
    ceiling = rc.measure_shared_ceiling([
        _observed(1, 2, 10.0),
        _observed(1, 2, 10.0),
        _observed(4, 1, 10.0),
    ])
    assert rc.writers_supported_by_storage(ceiling, 500.0) == 1
