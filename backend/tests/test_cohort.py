import pytest

from core import cohort
from core.cohort import Observation, Status


def observations(node_id, *thetas, normalised=None):
    values = normalised if normalised is not None else thetas
    return [
        Observation(node_id=node_id, theta=t, theta_normalised=n)
        for t, n in zip(thetas, values)
    ]


def uniform_fleet(count, theta=1.5, node_offset=0, key="i5-1145g7e"):
    """A cohort of healthy nodes with a little natural spread."""
    by_node, keys = {}, {}
    for i in range(count):
        node_id = node_offset + i + 1
        jitter = 1.0 + (i % 5 - 2) * 0.01
        by_node[node_id] = observations(node_id, *[theta * jitter] * 4)
        keys[node_id] = key
    return by_node, keys


# --- cohort key --------------------------------------------------------------

def test_the_same_part_number_groups_together_despite_cosmetic_differences():
    variants = [
        "11th Gen Intel(R) Core(TM) i5-1145G7E @ 2.60GHz",
        "Intel® Core™ i5-1145G7E CPU @ 2.60GHz",
        "Intel Core  i5-1145G7E",
    ]
    keys = {cohort.cohort_key(v) for v in variants}
    assert len(keys) == 1, f"expected one cohort, got {keys}"


def test_different_silicon_lands_in_different_cohorts():
    i5 = cohort.cohort_key("11th Gen Intel(R) Core(TM) i5-1145G7E @ 2.60GHz")
    i7 = cohort.cohort_key("11th Gen Intel(R) Core(TM) i7-1185G7E @ 2.80GHz")
    assert i5 != i7


def test_an_unrecognised_cpu_string_still_yields_a_stable_key():
    assert cohort.cohort_key("Some Weird SoC") == cohort.cohort_key("Some Weird SoC")


@pytest.mark.parametrize("value", [None, "", "   "])
def test_a_missing_cpu_string_is_its_own_bucket(value):
    assert cohort.cohort_key(value) == "unknown"


# --- cohort comparison -------------------------------------------------------

def test_a_healthy_fleet_produces_no_alerts():
    by_node, keys = uniform_fleet(10)
    verdicts = cohort.assess_cohort(by_node, keys)

    assert len(verdicts) == 10
    assert all(v.status is Status.OK for v in verdicts)


def test_a_node_running_hotter_than_its_peers_is_flagged():
    by_node, keys = uniform_fleet(10)
    by_node[1] = observations(1, *[2.4] * 4)  # 60% above the rest

    verdicts = {v.node_id: v for v in cohort.assess_cohort(by_node, keys)}

    assert verdicts[1].status is Status.ALERT
    assert verdicts[1].excess_ratio > 0.15
    assert "above the cohort median" in verdicts[1].reason
    assert all(verdicts[n].status is Status.OK for n in range(2, 11))


def test_the_flagged_node_is_excluded_from_the_median_it_is_judged_against():
    """Leaving it in would let a badly degraded node drag its own yardstick."""
    by_node, keys = uniform_fleet(6)
    by_node[1] = observations(1, *[5.0] * 4)

    verdict = next(v for v in cohort.assess_cohort(by_node, keys) if v.node_id == 1)

    assert verdict.cohort_median == pytest.approx(1.5, rel=0.05)


def test_a_node_cooler_than_its_peers_is_never_flagged():
    """Only excess resistance is a fault; a node running cool is fine."""
    by_node, keys = uniform_fleet(10)
    by_node[1] = observations(1, *[0.6] * 4)

    verdict = next(v for v in cohort.assess_cohort(by_node, keys) if v.node_id == 1)

    assert verdict.status is Status.OK
    assert verdict.excess_ratio < 0


def test_a_small_deviation_in_a_very_tight_cohort_is_not_an_alert():
    """In a uniform cohort the spread can be so tight that a trivial
    difference scores enormously. Statistical unlikeness is not damage."""
    by_node, keys = {}, {}
    for i in range(1, 11):
        by_node[i] = observations(i, *[1.500] * 4)
        keys[i] = "i5-1145g7e"
    by_node[1] = observations(1, *[1.53] * 4)  # 2% off, but every peer identical

    verdict = next(v for v in cohort.assess_cohort(by_node, keys) if v.node_id == 1)

    assert verdict.status is Status.OK
    assert verdict.excess_ratio < cohort.MIN_EXCESS_RATIO


def test_a_cohort_too_small_to_judge_says_so_rather_than_staying_silent():
    """Silence is indistinguishable from health."""
    by_node, keys = uniform_fleet(3)

    verdicts = cohort.assess_cohort(by_node, keys)

    assert len(verdicts) == 3
    assert all(v.status is Status.INSUFFICIENT_DATA for v in verdicts)
    assert all("too small" in v.reason for v in verdicts)


def test_a_node_with_too_few_fitted_windows_is_reported_as_unassessable():
    by_node, keys = uniform_fleet(10)
    by_node[1] = observations(1, 1.5)  # a single window

    verdict = next(v for v in cohort.assess_cohort(by_node, keys) if v.node_id == 1)

    assert verdict.status is Status.INSUFFICIENT_DATA
    assert "fitted windows" in verdict.reason


def test_different_hardware_is_judged_separately():
    """An i7 dissipating more heat is not evidence against an i5."""
    by_node, keys = uniform_fleet(6, theta=1.5, key="i5-1145g7e")
    hot, hot_keys = uniform_fleet(6, theta=2.4, node_offset=100, key="i7-1185g7e")
    by_node.update(hot)
    keys.update(hot_keys)

    verdicts = cohort.assess_cohort(by_node, keys)

    assert all(v.status is Status.OK for v in verdicts)
    assert {v.cohort_key for v in verdicts} == {"i5-1145g7e", "i7-1185g7e"}


def test_two_degraded_nodes_do_not_hide_each_other_in_a_large_cohort():
    by_node, keys = uniform_fleet(20)
    by_node[1] = observations(1, *[2.6] * 4)
    by_node[2] = observations(2, *[2.7] * 4)

    verdicts = {v.node_id: v for v in cohort.assess_cohort(by_node, keys)}

    assert verdicts[1].status is Status.ALERT
    assert verdicts[2].status is Status.ALERT


def test_the_median_survives_a_cohort_where_most_nodes_have_degraded():
    """A robust estimator has limits worth knowing: if the majority drifts,
    the majority becomes the norm and only the outliers show. Documented
    rather than fixed — that is what the self-baseline detector is for."""
    by_node, keys = uniform_fleet(10, theta=2.4)
    by_node[1] = observations(1, *[1.5] * 4)  # the only healthy one left

    verdicts = {v.node_id: v for v in cohort.assess_cohort(by_node, keys)}

    assert verdicts[1].status is Status.OK
    assert all(verdicts[n].status is Status.OK for n in range(2, 11))


# --- self-baseline drift ------------------------------------------------------

def test_a_node_matching_its_own_baseline_is_healthy():
    baseline = observations(1, *[1.5] * 5)
    recent = observations(1, *[1.52] * 5)

    verdict = cohort.assess_drift(1, baseline, recent)

    assert verdict.status is Status.OK
    assert verdict.ratio == pytest.approx(1.013, rel=0.02)


def test_a_node_that_has_drifted_well_past_its_baseline_alerts():
    baseline = observations(1, *[1.5] * 5)
    recent = observations(1, *[2.1] * 5)

    verdict = cohort.assess_drift(1, baseline, recent)

    assert verdict.status is Status.ALERT
    assert verdict.ratio == pytest.approx(1.4, rel=0.02)
    assert "worse than its own baseline" in verdict.reason


def test_a_modest_drift_is_a_watch_not_an_alert():
    baseline = observations(1, *[1.5] * 5)
    recent = observations(1, *[1.75] * 5)

    assert cohort.assess_drift(1, baseline, recent).status is Status.WATCH


def test_drift_uses_the_normalised_theta_not_the_raw_one():
    """Uncorrected theta differs 15-20% between a hot afternoon and a cold
    night, which would read as degradation every summer."""
    baseline = [Observation(1, theta=1.5, theta_normalised=1.5) for _ in range(5)]
    # Raw theta fell with the season; normalised is unchanged.
    recent = [Observation(1, theta=1.2, theta_normalised=1.5) for _ in range(5)]

    verdict = cohort.assess_drift(1, baseline, recent)

    assert verdict.status is Status.OK
    assert verdict.ratio == pytest.approx(1.0)


def test_drift_without_a_baseline_says_so():
    verdict = cohort.assess_drift(1, observations(1, 1.5), observations(1, *[2.0] * 5))

    assert verdict.status is Status.INSUFFICIENT_DATA
    assert "baseline needs" in verdict.reason


def test_drift_without_recent_measurements_says_so():
    verdict = cohort.assess_drift(1, observations(1, *[1.5] * 5), observations(1, 2.0))

    assert verdict.status is Status.INSUFFICIENT_DATA
    assert "recent" in verdict.reason
    assert verdict.baseline_theta == 1.5


def test_observations_missing_a_normalised_value_do_not_count():
    baseline = [Observation(1, theta=1.5, theta_normalised=None) for _ in range(5)]
    recent = [Observation(1, theta=2.0, theta_normalised=2.0) for _ in range(5)]

    assert cohort.assess_drift(1, baseline, recent).status is Status.INSUFFICIENT_DATA


# --- combining ----------------------------------------------------------------

def test_the_worse_of_the_two_detectors_wins():
    ok = cohort.CohortVerdict(1, "k", 10, Status.OK)
    alert = cohort.DriftVerdict(1, Status.ALERT)

    assert cohort.combine(ok, alert) is Status.ALERT
    assert cohort.combine(alert if False else ok, None) is Status.OK


def test_a_detector_that_cannot_judge_does_not_discard_what_the_other_found():
    blind = cohort.CohortVerdict(1, "k", 2, Status.INSUFFICIENT_DATA)
    alert = cohort.DriftVerdict(1, Status.ALERT)

    assert cohort.combine(blind, alert) is Status.ALERT


def test_both_blind_means_no_verdict():
    blind = cohort.CohortVerdict(1, "k", 2, Status.INSUFFICIENT_DATA)
    no_drift = cohort.DriftVerdict(1, Status.INSUFFICIENT_DATA)

    assert cohort.combine(blind, no_drift) is Status.INSUFFICIENT_DATA
    assert cohort.combine(None, None) is Status.INSUFFICIENT_DATA
