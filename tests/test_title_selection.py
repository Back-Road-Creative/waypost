"""select_title: a pure, deterministic title decision with a full record.

Every place name here is invented. Candidate 0 is always the caller's
route-only title; any other candidate wins only on enough fresh, thumbnail-
denominated evidence for its own format and traffic source.
"""

import json
import random
from dataclasses import replace

import pytest

from seo_manager.analytics import metric_source
from seo_manager.analytics.title_selection import (
    BASELINE,
    DEFAULT_POLICY,
    EvidenceSnapshot,
    Place,
    TemplateObservation,
    TitleFacts,
    TitleSelectionRefused,
    record_to_dict,
    select_title,
)

THUMB = metric_source.YOUTUBE_THUMBNAIL_IMPRESSION_CTR
AS_OF = "2026-03-20T00:00:00"


def _facts(fmt="longform", highlight=True):
    return TitleFacts(
        format=fmt,
        baseline_title="Brindlemoor to Quillhaven, Nordale in 4K",
        origin=Place("Brindlemoor", "seg-1:gps"),
        destination=Place("Quillhaven", "seg-9:gps"),
        region=Place("Nordale", "seg-1:geocode"),
        highlight=Place("Ashtarn Ridge", "seg-4:gps") if highlight else None,
        suffix=" in 4K",
    )


def _obs(template_id, rate, n=500, fmt="longform", traffic=None, source=THUMB):
    traffic = traffic or DEFAULT_POLICY.traffic_by_format[fmt]
    return TemplateObservation(
        template_id=template_id,
        format=fmt,
        traffic_source=traffic,
        metric_source=source,
        rate=rate,
        n=n,
        window_start="2026-03-01T00:00:00",
        window_end="2026-03-14T00:00:00",
    )


def _snapshot(*obs):
    return EvidenceSnapshot(as_of=AS_OF, observations=tuple(obs))


def _winning_snapshot():
    return _snapshot(_obs(BASELINE, 0.040), _obs("highlight_route", 0.060))


def test_candidate_zero_is_the_route_only_baseline():
    rec = select_title(_facts(), _snapshot(), DEFAULT_POLICY)
    first = rec.candidates[0]
    assert first.index == 0
    assert first.template_id == BASELINE
    assert first.title == "Brindlemoor to Quillhaven, Nordale in 4K"


def test_insufficient_evidence_keeps_the_baseline():
    rec = select_title(_facts(), _snapshot(), DEFAULT_POLICY)
    assert rec.chosen_index == 0
    assert rec.chosen_title == "Brindlemoor to Quillhaven, Nordale in 4K"
    assert rec.reason == "insufficient_evidence"


def test_small_samples_are_insufficient():
    small = DEFAULT_POLICY.min_n - 1
    snap = _snapshot(_obs(BASELINE, 0.04, n=small), _obs("highlight_route", 0.09))
    rec = select_title(_facts(), snap, DEFAULT_POLICY)
    assert rec.chosen_index == 0
    assert rec.reason == "insufficient_evidence"
    assert rec.candidates[0].excluded_reason == "n_below_min"


def test_a_clear_lift_picks_the_candidate_and_says_why():
    rec = select_title(_facts(), _winning_snapshot(), DEFAULT_POLICY)
    chosen = rec.candidates[rec.chosen_index]
    assert chosen.template_id == "highlight_route"
    assert rec.chosen_title == chosen.title
    assert "Ashtarn Ridge" in rec.chosen_title
    assert rec.reason == "lift_over_baseline"
    assert chosen.score == 0.060
    assert chosen.n == 500
    assert chosen.metric_source == THUMB


def test_a_lift_below_the_policy_margin_keeps_the_baseline():
    snap = _snapshot(_obs(BASELINE, 0.040), _obs("highlight_route", 0.041))
    rec = select_title(_facts(), snap, DEFAULT_POLICY)
    assert rec.chosen_index == 0
    assert rec.reason == "baseline_not_beaten"


def test_card_click_rate_and_unknown_rates_are_not_title_evidence():
    for source in (metric_source.YOUTUBE_CARD_CLICK_RATE, metric_source.UNKNOWN):
        snap = _snapshot(
            _obs(BASELINE, 0.040, source=source),
            _obs("highlight_route", 0.090, source=source),
        )
        rec = select_title(_facts(), snap, DEFAULT_POLICY)
        assert rec.chosen_index == 0
        assert rec.candidates[1].excluded_reason == "metric_not_title_evidence"


def test_stale_evidence_cannot_decide():
    old = [
        replace(o, window_start="2025-01-01T00:00:00", window_end="2025-01-14T00:00:00")
        for o in _winning_snapshot().observations
    ]
    rec = select_title(_facts(), _snapshot(*old), DEFAULT_POLICY)
    assert rec.chosen_index == 0
    assert rec.candidates[1].excluded_reason == "stale"


def test_evidence_from_the_other_format_or_traffic_source_is_ignored():
    other_format = _snapshot(
        _obs(BASELINE, 0.04, fmt="short"), _obs("highlight_route", 0.09, fmt="short")
    )
    assert select_title(_facts(), other_format, DEFAULT_POLICY).chosen_index == 0
    wrong = (
        "search"
        if DEFAULT_POLICY.traffic_by_format["longform"] == "browse"
        else "browse"
    )
    other_traffic = _snapshot(
        _obs(BASELINE, 0.04, traffic=wrong),
        _obs("highlight_route", 0.09, traffic=wrong),
    )
    assert select_title(_facts(), other_traffic, DEFAULT_POLICY).chosen_index == 0


def test_shorts_and_longform_select_on_their_own_traffic_source():
    policy = replace(
        DEFAULT_POLICY, traffic_by_format={"short": "browse", "longform": "search"}
    )
    snap = _snapshot(
        _obs(BASELINE, 0.04, fmt="short", traffic="browse"),
        _obs("highlight_route", 0.09, fmt="short", traffic="browse"),
        _obs(BASELINE, 0.04, fmt="longform", traffic="browse"),
        _obs("highlight_route", 0.09, fmt="longform", traffic="browse"),
    )
    short = select_title(_facts(fmt="short"), snap, policy)
    lf = select_title(_facts(), snap, policy)
    assert (short.traffic_source, short.chosen_index) == ("browse", 1)
    assert (lf.traffic_source, lf.chosen_index) == ("search", 0)


def test_every_candidate_names_the_evidence_its_claims_rest_on():
    rec = select_title(_facts(), _snapshot(), DEFAULT_POLICY)
    assert rec.candidates[0].claim_evidence == (
        "seg-1:gps",
        "seg-9:gps",
        "seg-1:geocode",
    )
    hl = next(c for c in rec.candidates if c.template_id == "highlight_route")
    assert "seg-4:gps" in hl.claim_evidence


def test_a_template_missing_its_fact_is_recorded_not_generated():
    rec = select_title(_facts(highlight=False), _snapshot(), DEFAULT_POLICY)
    hl = next(c for c in rec.candidates if c.template_id == "highlight_route")
    assert hl.title is None
    assert hl.excluded_reason == "missing_fact"
    assert all("Ashtarn" not in (c.title or "") for c in rec.candidates)


def test_over_length_candidates_are_excluded():
    policy = replace(DEFAULT_POLICY, max_title_chars=45)
    rec = select_title(_facts(), _winning_snapshot(), policy)
    hl = next(c for c in rec.candidates if c.template_id == "highlight_route")
    assert hl.excluded_reason == "too_long"
    assert rec.chosen_index == 0


def test_a_claim_without_evidence_is_refused():
    facts = replace(_facts(), highlight=Place("Ashtarn Ridge", ""))
    with pytest.raises(TitleSelectionRefused, match="evidence"):
        select_title(facts, _snapshot(), DEFAULT_POLICY)


def test_duplicate_observations_are_refused():
    snap = _snapshot(_obs(BASELINE, 0.04), _obs(BASELINE, 0.05))
    with pytest.raises(TitleSelectionRefused, match="duplicate"):
        select_title(_facts(), snap, DEFAULT_POLICY)


def test_unknown_format_is_refused():
    with pytest.raises(TitleSelectionRefused, match="format"):
        select_title(_facts(fmt="reel"), _snapshot(), DEFAULT_POLICY)


def test_the_record_carries_policy_version_dates_and_provenance():
    rec = select_title(_facts(), _winning_snapshot(), DEFAULT_POLICY)
    assert rec.policy_version == DEFAULT_POLICY.version
    assert rec.evidence_as_of == AS_OF
    chosen = rec.candidates[rec.chosen_index]
    assert chosen.evidence_window == ("2026-03-01T00:00:00", "2026-03-14T00:00:00")


def test_same_inputs_always_yield_the_same_record():
    obs = [
        *_winning_snapshot().observations,
        _obs("region_route", 0.05),
        _obs(BASELINE, 0.07, fmt="short", traffic="browse"),
    ]
    first = json.dumps(
        record_to_dict(select_title(_facts(), _snapshot(*obs), DEFAULT_POLICY))
    )
    rng = random.Random(7)
    for _ in range(5):
        rng.shuffle(obs)
        again = record_to_dict(select_title(_facts(), _snapshot(*obs), DEFAULT_POLICY))
        assert json.dumps(again) == first


def test_equal_scores_break_toward_the_lower_index():
    snap = _snapshot(
        _obs(BASELINE, 0.04), _obs("highlight_route", 0.06), _obs("region_route", 0.06)
    )
    rec = select_title(_facts(), snap, DEFAULT_POLICY)
    assert rec.candidates[rec.chosen_index].template_id == "highlight_route"
