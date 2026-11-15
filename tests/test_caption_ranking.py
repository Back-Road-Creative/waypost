"""Caption ranking: Meta reach re-orders a caption opener rotation, never replaces it."""

from dataclasses import replace

import pytest

from seo_manager.analytics.caption_ranking import (
    BELOW_REFERENCE,
    DEFAULT_POLICY,
    FACEBOOK_REEL_INITIAL_PLAYS_DAY7,
    FACEBOOK_REEL_REACH_DAY7,
    INSTAGRAM_REEL_REACH_DAY7,
    INSUFFICIENT_EVIDENCE,
    LAG,
    LEAD,
    METRIC_NOT_CAPTION_EVIDENCE,
    MIDDLE,
    N_BELOW_MIN,
    NO_EVIDENCE,
    NO_SPREAD,
    RANKED,
    STALE,
    CaptionEvidence,
    CaptionObservation,
    CaptionRankingRefused,
    rank_captions,
    record_to_dict,
)

AS_OF = "2026-12-01T00:00:00+00:00"
ROTATION = ("a", "b", "c", "d", "e")


def _obs(
    tid,
    median,
    n=20,
    platform="facebook",
    source=FACEBOOK_REEL_INITIAL_PLAYS_DAY7,
    start=None,
    end=None,
):
    return CaptionObservation(
        platform=platform,
        template_id=tid,
        metric_source=source,
        median=median,
        n=n,
        window_start=start or "2026-11-01T00:00:00+00:00",
        window_end=end or "2026-11-28T00:00:00+00:00",
    )


def _rank(*observations, rotation=ROTATION, platform="facebook", policy=DEFAULT_POLICY):
    return rank_captions(
        platform, rotation, CaptionEvidence(AS_OF, tuple(observations)), policy
    )


def test_no_evidence_keeps_the_rotation_exactly():
    r = _rank()
    assert r.order == ROTATION and r.reason == INSUFFICIENT_EVIDENCE
    assert all(e.excluded_reason == NO_EVIDENCE and e.tier == MIDDLE for e in r.entries)


def test_one_measured_opener_is_not_enough_to_rank():
    r = _rank(_obs("a", 900))
    assert r.order == ROTATION and r.reason == INSUFFICIENT_EVIDENCE


def test_leaders_move_to_the_front_and_laggards_to_the_back_keeping_rotation_order():
    # measured medians: a 100, c 500, d 1000, e 120 -> reference (median) = 310
    r = _rank(_obs("a", 100), _obs("c", 500), _obs("d", 1000), _obs("e", 120))
    assert r.reason == RANKED and r.reference == 310.0
    tiers = {e.template_id: e.tier for e in r.entries}
    assert tiers == {"a": LAG, "b": MIDDLE, "c": LEAD, "d": LEAD, "e": LAG}
    assert r.order == ("c", "d", "b", "a", "e")


def test_an_opener_inside_the_margin_stays_in_the_middle():
    # reference = 1000; lift 0.10 -> lead above 1100, lag below 900
    r = _rank(_obs("a", 1050), _obs("b", 1000), _obs("c", 950))
    assert {e.template_id: e.tier for e in r.entries} == {
        "a": MIDDLE, "b": MIDDLE, "c": MIDDLE, "d": MIDDLE, "e": MIDDLE,
    }  # fmt: skip
    assert r.order == ROTATION and r.reason == NO_SPREAD


def test_under_min_n_stale_and_the_wrong_metric_never_count():
    r = _rank(
        _obs("a", 5000, n=DEFAULT_POLICY.min_n - 1),
        _obs(
            "b",
            5000,
            start="2025-12-01T00:00:00+00:00",
            end="2026-01-01T00:00:00+00:00",
        ),
        _obs("c", 5000, source=INSTAGRAM_REEL_REACH_DAY7),
        _obs("d", 100),
        _obs("e", 100),
    )
    reasons = {e.template_id: e.excluded_reason for e in r.entries}
    assert reasons == {
        "a": N_BELOW_MIN, "b": STALE, "c": METRIC_NOT_CAPTION_EVIDENCE, "d": None, "e": None,
    }  # fmt: skip
    assert r.order == ROTATION and r.reason == NO_SPREAD


def test_evidence_for_another_platform_is_ignored():
    r = _rank(_obs("a", 100, platform="instagram", source=INSTAGRAM_REEL_REACH_DAY7))
    assert (
        next(e for e in r.entries if e.template_id == "a").excluded_reason
        == NO_EVIDENCE
    )


def test_facebook_is_judged_on_initial_plays_until_reel_reach_answers():
    # Graph returns no per-Reel reach for the live Page (every measured Reel
    # stores reach=null), so day-7 initial plays are Facebook's metric.
    assert (
        DEFAULT_POLICY.metric_by_platform["facebook"]
        == FACEBOOK_REEL_INITIAL_PLAYS_DAY7
    )
    r = _rank(
        _obs("a", 5000, source=FACEBOOK_REEL_REACH_DAY7), _obs("b", 100), _obs("c", 900)
    )
    assert r.metric_source == FACEBOOK_REEL_INITIAL_PLAYS_DAY7
    assert r.entries[0].excluded_reason == METRIC_NOT_CAPTION_EVIDENCE
    assert r.order[0] == "c"


def test_instagram_is_judged_on_instagram_reach():
    ig = dict(platform="instagram", source=INSTAGRAM_REEL_REACH_DAY7)
    r = _rank(
        _obs("a", 100, **ig),
        _obs("b", 1000, **ig),
        _obs("c", 300, **ig),
        platform="instagram",
    )
    assert r.metric_source == INSTAGRAM_REEL_REACH_DAY7
    assert r.order[0] == "b" and r.order[-1] == "a"


def test_evidence_for_an_opener_not_in_the_rotation_cannot_add_it():
    r = _rank(_obs("a", 100), _obs("b", 1000), _obs("zz", 99999))
    assert set(r.order) == set(ROTATION) and "zz" not in {
        e.template_id for e in r.entries
    }


def test_the_order_is_a_permutation_of_the_rotation_for_any_input():
    r = _rank(_obs("a", 1), _obs("b", 2), _obs("c", 3), _obs("d", 4), _obs("e", 5))
    assert sorted(r.order) == sorted(ROTATION)


def test_observation_order_does_not_change_the_record():
    obs = [_obs("a", 100), _obs("c", 500), _obs("d", 1000), _obs("e", 120)]
    assert record_to_dict(_rank(*obs)) == record_to_dict(_rank(*reversed(obs)))


def test_a_laggard_names_why_it_lags():
    r = _rank(_obs("a", 100), _obs("c", 500), _obs("d", 1000))
    lag = next(e for e in r.entries if e.template_id == "a")
    assert lag.tier == LAG and lag.score == 100 and lag.n == 20
    assert lag.note == BELOW_REFERENCE


@pytest.mark.parametrize(
    "bad, match",
    [
        (dict(platform="tiktok"), "platform"),
        (dict(rotation=()), "rotation"),
        (dict(rotation=("a", "a")), "rotation"),
    ],
)
def test_unrankable_inputs_are_refused(bad, match):
    with pytest.raises(CaptionRankingRefused, match=match):
        _rank(**bad)


@pytest.mark.parametrize(
    "obs, match",
    [
        (_obs("a", -1), "out of range"),
        (_obs("a", 1, n=-1), "out of range"),
        (_obs("a", 1, end="2027-01-01T00:00:00+00:00"), "before as_of"),
    ],
)
def test_bad_observations_are_refused(obs, match):
    with pytest.raises(CaptionRankingRefused, match=match):
        _rank(obs)


def test_duplicate_observations_are_refused():
    with pytest.raises(CaptionRankingRefused, match="duplicate"):
        _rank(_obs("a", 1), _obs("a", 2))


def test_a_bad_as_of_is_refused():
    with pytest.raises(CaptionRankingRefused, match="as_of"):
        rank_captions(
            "facebook", ROTATION, CaptionEvidence("not a time"), DEFAULT_POLICY
        )


def test_a_policy_change_is_a_new_version():
    policy = replace(DEFAULT_POLICY, min_n=5, version="caption-policy-test")
    assert _rank(policy=policy).policy_version == "caption-policy-test"


def test_the_record_is_json_safe():
    import json

    json.dumps(record_to_dict(_rank(_obs("a", 100), _obs("c", 500), _obs("d", 1000))))
