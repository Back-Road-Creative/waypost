"""Record v2: route places, region fallback, staleness, and the upload gate.

Every place here is invented. The defect this guards: a route through two
places (here Brindlemoor and Quillhaven, both in Harrow County) reduced to
one of them on the on-screen text, thumbnail and title.
"""

import json
from dataclasses import replace

import pytest

from seo_manager.analytics.final_text_review import (
    AGREE,
    DEFAULT_TEXT_POLICY,
    RECORD_VERSION,
    REFUSED,
    FinalTextPolicy,
    FinalTextReviewRefused,
    review_final_text,
    review_to_dict,
    verify_review,
)
from seo_manager.analytics.title_selection import (
    DEFAULT_POLICY,
    EvidenceSnapshot,
    Place,
    TitleFacts,
    record_to_dict,
    select_title,
)

ROUTE = "Brindlemoor to Quillhaven, Harrow County"
TITLE = "Brindlemoor to Quillhaven, Harrow County in 4K"
REGION = "Harrow County"
SOURCE_FP = "src-" + "a" * 60
PLAN_FP = "plan-" + "b" * 59
DATE = "2026-03-02"

EVIDENCE = [
    {"evidence_id": "p-1", "kind": "location", "value": "Brindlemoor", "source": "gps"},
    {"evidence_id": "p-2", "kind": "location", "value": "Quillhaven", "source": "gps"},
    {"evidence_id": "p-3", "kind": "location", "value": "Fenwick", "source": "gps"},
    {"evidence_id": "r-1", "kind": "region", "value": REGION, "source": "geocode"},
    {"evidence_id": "d-1", "kind": "date", "value": DATE, "source": "file_metadata"},
]


def _decision(title=TITLE):
    facts = TitleFacts(
        format="longform",
        baseline_title=title,
        origin=Place("Brindlemoor", "p-1"),
        destination=Place("Quillhaven", "p-2"),
        region=Place(REGION, "r-1"),
        suffix=" in 4K",
    )
    snap = EvidenceSnapshot(as_of="2026-03-20T00:00:00")
    return record_to_dict(select_title(facts, snap, DEFAULT_POLICY))


def _region(**overrides):
    claim = {
        "evidence_id": "r-1",
        "contains": ["p-1", "p-2"],
        "coverage": 1.0,
        "gaps": [],
        "samples_outside": 0,
    }
    claim.update(overrides)
    return claim


def _whole(surface, text, ids):
    return {
        "surface": surface,
        "text": text,
        "evidence_ids": ids,
        "kind": "place_line",
        "scope": "whole",
    }


def _record(text=ROUTE, ids=("p-1", "p-2", "r-1"), title=TITLE, **overrides):
    record = {
        "record_version": RECORD_VERSION,
        "approval_hash": "c" * 64,
        "title": title,
        "recording_date": DATE,
        "date_evidence_id": "d-1",
        "source_fingerprint": SOURCE_FP,
        "plan_fingerprint": PLAN_FP,
        "route_places": [
            {"evidence_id": "p-1", "retained_seconds": 1200.0, "required": True},
            {"evidence_id": "p-2", "retained_seconds": 480.0, "required": True},
        ],
        "region_claims": [],
        "visible": [
            _whole("ost", text, list(ids)),
            _whole("thumbnail", text, list(ids)),
        ],
    }
    record.update(overrides)
    return record


def _review(record=None, decision=None, policy=DEFAULT_TEXT_POLICY):
    return review_final_text(
        _record() if record is None else record,
        _decision() if decision is None else decision,
        EVIDENCE,
        policy=policy,
    )


def _verify(review, **kwargs):
    kwargs.setdefault("final_title", TITLE)
    kwargs.setdefault("visible", {"ost": ROUTE, "thumbnail": ROUTE})
    kwargs.setdefault("source_fingerprint", SOURCE_FP)
    kwargs.setdefault("plan_fingerprint", PLAN_FP)
    kwargs.setdefault("policy_version", DEFAULT_POLICY.version)
    verify_review(review, **kwargs)


def _refused(**kwargs):
    with pytest.raises(FinalTextReviewRefused) as caught:
        _verify(**kwargs)
    return caught.value


# --- the route must survive onto every whole-video surface -----------------


def test_a_route_naming_every_required_place_agrees():
    review = _review()
    assert review.verdict == AGREE, review.reasons
    assert [p.evidence_id for p in review.route_places] == ["p-1", "p-2"]
    _verify(review)


def test_one_place_standing_in_for_the_route_is_refused():
    record = _record(text="Brindlemoor", ids=("p-1",))
    review = _review(record)
    assert review.verdict == REFUSED
    assert "place_dropped:ost:p-2" in review.reasons
    assert "route_reduced:ost" in review.reasons
    assert "place_dropped:thumbnail:p-2" in review.reasons


def test_a_title_that_drops_a_required_place_is_refused():
    title = "Brindlemoor, Harrow County in 4K"
    review = _review(_record(title=title), _decision(title))
    assert review.verdict == REFUSED
    assert "place_dropped:title:p-2" in review.reasons
    assert "route_reduced:title" in review.reasons


def test_a_place_marked_optional_against_its_retained_time_is_refused():
    places = [
        {"evidence_id": "p-1", "retained_seconds": 1200.0, "required": True},
        {"evidence_id": "p-2", "retained_seconds": 480.0, "required": False},
    ]
    review = _review(_record(route_places=places))
    assert review.verdict == REFUSED
    assert "required_mismatch:p-2" in review.reasons


def test_a_brief_pass_through_need_not_be_named():
    places = [
        {"evidence_id": "p-1", "retained_seconds": 1200.0, "required": True},
        {"evidence_id": "p-2", "retained_seconds": 480.0, "required": True},
        {"evidence_id": "p-3", "retained_seconds": 20.0, "required": False},
    ]
    assert _review(_record(route_places=places)).verdict == AGREE


def test_min_place_seconds_is_the_policy_threshold():
    policy = FinalTextPolicy(version="test-policy", min_place_seconds=600.0)
    review = _review(policy=policy)
    assert "required_mismatch:p-2" in review.reasons
    assert review.text_policy == {
        "version": "test-policy",
        "min_place_seconds": 600.0,
        "region_min_coverage": 1.0,
        "filler_words": DEFAULT_TEXT_POLICY.filler_words,
        "templates": DEFAULT_TEXT_POLICY.templates,
        "drive_templates": DEFAULT_TEXT_POLICY.drive_templates,
        "drive_version": DEFAULT_TEXT_POLICY.drive_version,
    }


def test_an_unknown_or_malformed_route_place_is_refused():
    places = [
        {"evidence_id": "p-1", "retained_seconds": 1200.0, "required": True},
        {"evidence_id": "p-7", "retained_seconds": 900.0, "required": True},
        {"evidence_id": "p-2", "retained_seconds": -1, "required": True},
    ]
    reasons = _review(_record(route_places=places)).reasons
    assert "evidence_missing:p-7" in reasons
    assert "route_place_malformed:2" in reasons


def test_an_empty_route_is_refused():
    assert "missing_field:route_places" in _review(_record(route_places=[])).reasons


# --- region fallback -------------------------------------------------------


def _region_record(**claim):
    return _record(text=REGION, ids=("r-1",), region_claims=[_region(**claim)])


def test_a_region_containing_the_whole_retained_route_passes():
    review = _review(_region_record())
    assert review.verdict == AGREE, review.reasons
    _verify(review, visible={"ost": REGION, "thumbnail": REGION})


def test_a_region_missing_a_required_place_is_partial():
    review = _review(_region_record(contains=["p-1"]))
    assert review.verdict == REFUSED
    assert "region_partial:r-1" in review.reasons
    assert "place_dropped:ost:p-2" in review.reasons


def test_both_places_in_the_region_but_the_route_leaving_it_is_refused():
    review = _review(_region_record(samples_outside=3))
    assert review.verdict == REFUSED
    assert "region_outside:r-1" in review.reasons
    assert "region_not_contained:r-1" in review.reasons


def test_a_region_with_gaps_or_low_coverage_is_refused():
    gaps = _review(_region_record(gaps=[{"start_s": 410.0, "end_s": 446.0}]))
    assert "region_gaps:r-1" in gaps.reasons
    low = _review(_region_record(coverage=0.93))
    assert "region_coverage:r-1" in low.reasons
    assert "region_not_contained:r-1" in gaps.reasons
    assert "region_not_contained:r-1" in low.reasons


def test_a_region_claim_without_its_measurements_is_refused():
    claim = _region()
    del claim["samples_outside"]
    record = _record(text=REGION, ids=("r-1",), region_claims=[claim])
    assert "region_claim_malformed:r-1" in _review(record).reasons


def test_a_region_claim_must_name_region_evidence():
    record = _record(
        text=REGION, ids=("r-1",), region_claims=[_region(evidence_id="p-1")]
    )
    assert "evidence_kind:p-1" in _review(record).reasons


# --- segments (Shorts) ------------------------------------------------------


def _short(text="Quillhaven", segment=("p-2",), title="Quillhaven #shorts"):
    facts = TitleFacts(
        format="short", baseline_title=title, place=Place("Quillhaven", "p-2")
    )
    decision = record_to_dict(
        select_title(
            facts, EvidenceSnapshot(as_of="2026-03-20T00:00:00"), DEFAULT_POLICY
        )
    )
    record = _record(
        title=title,
        route_places=[
            {"evidence_id": "p-2", "retained_seconds": 45.0, "required": False},
        ],
        visible=[
            {
                "surface": "shorts_ost",
                "text": text,
                "evidence_ids": ["p-2"],
                "kind": "place_line",
                "scope": "segment",
                "segment_places": list(segment),
            }
        ],
    )
    return review_final_text(record, decision, EVIDENCE)


def test_a_short_names_its_own_segment():
    review = _short()
    assert review.verdict == AGREE, review.reasons
    _verify(
        review,
        final_title="Quillhaven #shorts",
        visible={"shorts_ost": "Quillhaven"},
    )


def test_a_short_whose_text_drops_its_segment_place_is_refused():
    review = _short(text="Harrow County")
    assert "segment_place_dropped:shorts_ost:p-2" in review.reasons


def test_a_segment_place_outside_the_route_is_refused():
    assert "route_place_unknown:p-1" in _short(segment=("p-2", "p-1")).reasons


def test_a_surface_without_scope_is_refused():
    record = _record()
    del record["visible"][0]["scope"]
    assert "missing_field:visible[0]" in _review(record).reasons


def test_a_record_of_another_version_is_refused():
    assert "record_version:other" in _review(_record(record_version="other")).reasons


@pytest.mark.parametrize("field", ["source_fingerprint", "plan_fingerprint"])
def test_a_record_without_fingerprints_is_refused(field):
    assert f"missing_field:{field}" in _review(_record(**{field: ""})).reasons


# --- staleness and empty markers at verify time -----------------------------


def test_a_changed_source_is_stale():
    err = _refused(review=_review(), source_fingerprint="src-other")
    assert err.code == "stale:source"


def test_a_changed_plan_is_stale():
    assert _refused(review=_review(), plan_fingerprint="plan-x").code == "stale:plan"


def test_a_changed_title_policy_is_stale():
    err = _refused(review=_review(), policy_version="title-policy-2")
    assert err.code == "stale:policy"


def test_a_changed_text_policy_is_stale():
    review = _review(policy=replace(DEFAULT_TEXT_POLICY, region_min_coverage=0.9))
    assert _refused(review=review).code == "stale:text_policy"


def test_a_review_against_its_own_text_policy_passes():
    policy = FinalTextPolicy(version="gms-1", min_place_seconds=30.0)
    _verify(_review(policy=policy), text_policy=policy)


def test_changed_text_and_title_are_stale_and_all_reasons_are_kept():
    err = _refused(
        review=_review(),
        final_title=TITLE + "!",
        visible={"ost": ROUTE, "thumbnail": "Brindlemoor"},
        plan_fingerprint="plan-x",
    )
    assert err.code == "stale:plan"
    assert set(err.reasons) == {"stale:plan", "stale:title", "stale:text:thumbnail"}


def test_a_dropped_surface_is_stale():
    err = _refused(review=_review(), visible={"ost": ROUTE})
    assert err.code == "stale:surfaces"


@pytest.mark.parametrize("marker", [None, {}, "", "   "])
def test_an_empty_marker_never_counts(marker):
    assert _refused(review=marker).code == "review_missing"


def test_a_review_with_an_empty_fingerprint_never_counts():
    blank = replace(_review(), fingerprint="")
    assert _refused(review=blank).code == "review_missing"


def test_a_persisted_review_verifies_after_a_json_round_trip():
    stored = json.loads(json.dumps(review_to_dict(_review())))
    _verify(stored)


def test_an_edited_persisted_review_is_tampered():
    stored = review_to_dict(_review(_record(text="Brindlemoor", ids=("p-1",))))
    stored.update(verdict=AGREE, reasons=[])
    assert _refused(review=stored).code == "review_tampered"


def test_a_malformed_persisted_review_is_refused():
    stored = review_to_dict(_review())
    del stored["surfaces"]
    assert _refused(review=stored).code == "review_malformed"


def test_a_review_of_an_older_review_version_is_stale():
    stored = review_to_dict(_review())
    stored["review_version"] = "final-text-review-0"
    err = _refused(review=stored)
    assert err.code in {"stale:review_version", "review_tampered"}


def test_refusals_never_approve():
    reviews = [_review(), _review(_record(text="Brindlemoor", ids=("p-1",)))]
    assert all(r.human_gate_required is True for r in reviews)


# --- every name in the text needs a claim ----------------------------------


def _unbacked(text, ids=("p-2", "r-1"), title=None, **record):
    """A route whose sound region would otherwise excuse the text."""
    record.setdefault("region_claims", [_region()])
    title = title or text
    return _review(_record(text=text, ids=ids, title=title, **record), _decision(title))


def test_a_name_with_no_claim_is_refused_even_under_a_sound_region():
    # The shape that agreed at 4b27600: the region excuses the omitted
    # required place, and nothing asked what "Longmere" stood on.
    review = _unbacked("Longmere to Quillhaven, Harrow County")
    assert review.verdict == REFUSED
    for surface in ("ost", "thumbnail", "title"):
        assert f"name_unbacked:{surface}:longmere" in review.reasons
    error = _refused(
        review=review,
        final_title="Longmere to Quillhaven, Harrow County",
        visible={
            "ost": "Longmere to Quillhaven, Harrow County",
            "thumbnail": "Longmere to Quillhaven, Harrow County",
        },
    )
    assert error.code == "verdict:refused"


def test_a_known_place_named_without_citing_it_is_refused():
    review = _unbacked("Fenwick to Quillhaven, Harrow County")
    assert "name_unbacked:ost:fenwick" in review.reasons


def test_a_cited_place_off_the_retained_route_is_refused():
    review = _unbacked(
        "Fenwick to Quillhaven, Harrow County", ids=("p-3", "p-2", "r-1")
    )
    assert review.verdict == REFUSED
    assert "place_not_on_route:ost:p-3" in review.reasons


def test_a_brief_retained_place_may_be_named():
    places = [
        {"evidence_id": "p-1", "retained_seconds": 1200.0, "required": True},
        {"evidence_id": "p-2", "retained_seconds": 480.0, "required": True},
        {"evidence_id": "p-3", "retained_seconds": 20.0, "required": False},
    ]
    text = "Brindlemoor to Quillhaven via Fenwick, Harrow County"
    review = _review(
        _record(text=text, ids=("p-1", "p-2", "p-3", "r-1"), route_places=places)
    )
    assert review.verdict == AGREE, review.reasons


def test_a_short_naming_a_place_outside_its_segment_is_refused():
    review = _short(text="Quillhaven to Brindlemoor")
    assert "name_unbacked:shorts_ost:brindlemoor" in review.reasons
    record_review = review_final_text(
        _record(
            visible=[
                {
                    "surface": "shorts_ost",
                    "text": "Quillhaven to Brindlemoor",
                    "evidence_ids": ["p-2", "p-1"],
                    "kind": "place_line",
                    "scope": "segment",
                    "segment_places": ["p-2"],
                }
            ]
        ),
        _decision(),
        EVIDENCE,
    )
    assert "place_not_in_segment:shorts_ost:p-1" in record_review.reasons


def test_words_outside_the_policy_filler_are_refused():
    text = "Brindlemoor to Quillhaven, Harrow County at dawn"
    record = _record(text=text, ids=("p-1", "p-2", "r-1"))
    review = _review(record)
    assert "name_unbacked:ost:dawn" in review.reasons
    assert "name_unbacked:ost:at" not in review.reasons
    policy = FinalTextPolicy(
        version="test-filler",
        filler_words=(*DEFAULT_TEXT_POLICY.filler_words, "dawn"),
    )
    assert _review(record, policy=policy).verdict == AGREE


def test_a_cited_road_backs_its_name():
    road = {"evidence_id": "rd-1", "kind": "road", "value": "Old Mill Road"}
    evidence = [*EVIDENCE, {**road, "source": "gps"}]
    text = "Brindlemoor to Quillhaven via Old Mill Road"
    record = _record(text=text, ids=("p-1", "p-2", "rd-1"))
    review = review_final_text(record, _decision(), evidence)
    assert review.verdict == AGREE, review.reasons
    uncited = _record(text=text, ids=("p-1", "p-2"))
    review = review_final_text(uncited, _decision(), evidence)
    assert "name_unbacked:ost:mill" in review.reasons
