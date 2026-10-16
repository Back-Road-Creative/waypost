"""review_final_text: one record binding approved on-screen text to the final title.

Every place name here is invented. The review never approves anything: every
verdict keeps ``human_gate_required`` True. It refuses when evidence is
missing or ambiguous, reports ``disagree`` when the final selected title is
not the one the operator approved, and ``verify_review`` refuses any text or
title that changed after the review.
"""

import random
from dataclasses import replace

import pytest

from seo_manager.analytics import metric_source
from seo_manager.analytics.final_text_review import (
    AGREE,
    DISAGREE,
    REFUSED,
    FinalTextReviewRefused,
    review_final_text,
    review_to_dict,
    verify_review,
)
from seo_manager.analytics.title_selection import (
    BASELINE,
    DEFAULT_POLICY,
    EvidenceSnapshot,
    Place,
    TemplateObservation,
    TitleFacts,
    record_to_dict,
    select_title,
)

BASELINE_TITLE = "Brindlemoor to Quillhaven, Nordale in 4K"
HIGHLIGHT_TITLE = "Ashtarn Ridge: Brindlemoor to Quillhaven, Nordale in 4K"
OST = "Brindlemoor to Quillhaven, Nordale"
DATE = "2026-03-02"

EVIDENCE = [
    {
        "evidence_id": "seg-1:gps",
        "kind": "location",
        "value": "Brindlemoor",
        "source": "gps",
    },
    {
        "evidence_id": "seg-9:gps",
        "kind": "location",
        "value": "Quillhaven",
        "source": "gps",
    },
    {
        "evidence_id": "seg-1:geo",
        "kind": "location",
        "value": "Nordale",
        "source": "geocode",
    },
    {
        "evidence_id": "seg-4:gps",
        "kind": "location",
        "value": "Ashtarn Ridge",
        "source": "gps",
    },
    {
        "evidence_id": "rec:date",
        "kind": "date",
        "value": DATE,
        "source": "file_metadata",
    },
]
OST_IDS = ["seg-1:gps", "seg-9:gps", "seg-1:geo"]


def _facts():
    return TitleFacts(
        format="longform",
        baseline_title=BASELINE_TITLE,
        origin=Place("Brindlemoor", "seg-1:gps"),
        destination=Place("Quillhaven", "seg-9:gps"),
        region=Place("Nordale", "seg-1:geo"),
        highlight=Place("Ashtarn Ridge", "seg-4:gps"),
        suffix=" in 4K",
    )


def _obs(template_id, rate):
    return TemplateObservation(
        template_id=template_id,
        format="longform",
        traffic_source="search",
        metric_source=metric_source.YOUTUBE_THUMBNAIL_IMPRESSION_CTR,
        rate=rate,
        n=500,
        window_start="2026-03-01T00:00:00",
        window_end="2026-03-14T00:00:00",
    )


def _baseline_decision():
    snap = EvidenceSnapshot(as_of="2026-03-20T00:00:00")
    return record_to_dict(select_title(_facts(), snap, DEFAULT_POLICY))


def _highlight_decision():
    snap = EvidenceSnapshot(
        as_of="2026-03-20T00:00:00",
        observations=(_obs(BASELINE, 0.040), _obs("highlight_route", 0.060)),
    )
    return record_to_dict(select_title(_facts(), snap, DEFAULT_POLICY))


def _approved(**overrides):
    approved = {
        "approval_hash": "a" * 64,
        "title": BASELINE_TITLE,
        "recording_date": DATE,
        "date_evidence_id": "rec:date",
        "visible": [
            {"surface": "ost", "text": OST, "evidence_ids": list(OST_IDS)},
            {"surface": "thumbnail", "text": OST, "evidence_ids": list(OST_IDS)},
        ],
    }
    approved.update(overrides)
    return approved


def _review(approved=None, decision=None, evidence=None):
    return review_final_text(
        _approved() if approved is None else approved,
        _baseline_decision() if decision is None else decision,
        EVIDENCE if evidence is None else evidence,
    )


# --- agree -----------------------------------------------------------------


def test_matching_text_title_and_evidence_agree():
    review = _review()
    assert review.verdict == AGREE
    assert review.reasons == ()
    assert review.final_title == BASELINE_TITLE
    assert review.approved_title == BASELINE_TITLE
    assert review.approval_hash == "a" * 64


def test_review_names_the_exact_visible_text_and_title():
    review = _review()
    surfaces = {s.surface: s for s in review.surfaces}
    assert surfaces["ost"].text == OST
    assert surfaces["thumbnail"].text == OST
    assert surfaces["title"].text == BASELINE_TITLE
    assert surfaces["recording_date"].text == DATE


def test_review_carries_the_evidence_behind_each_surface():
    surfaces = {s.surface: s for s in _review().surfaces}
    assert [e.evidence_id for e in surfaces["ost"].evidence] == OST_IDS
    assert [e.evidence_id for e in surfaces["title"].evidence] == OST_IDS
    assert [e.value for e in surfaces["recording_date"].evidence] == [DATE]
    assert surfaces["title"].evidence[0].source == "gps"


def test_review_records_the_decision_it_checked():
    review = _review()
    assert review.decision_policy_version == DEFAULT_POLICY.version
    assert review.decision_chosen_index == 0
    assert review.decision_reason == "insufficient_evidence"


def test_every_verdict_keeps_the_human_gate():
    reviews = [
        _review(),
        _review(decision=_highlight_decision()),
        _review(evidence=[]),
    ]
    assert {r.verdict for r in reviews} == {AGREE, DISAGREE, REFUSED}
    assert all(r.human_gate_required is True for r in reviews)
    assert all(review_to_dict(r)["human_gate_required"] is True for r in reviews)


# --- disagree --------------------------------------------------------------


def test_a_later_selected_title_disagrees_with_the_approved_one():
    review = _review(decision=_highlight_decision())
    assert review.verdict == DISAGREE
    assert review.reasons == ("title_changed_after_approval",)
    assert review.final_title == HIGHLIGHT_TITLE
    assert review.approved_title == BASELINE_TITLE
    title = next(s for s in review.surfaces if s.surface == "title")
    assert "seg-4:gps" in [e.evidence_id for e in title.evidence]


def test_approving_the_selected_title_agrees():
    review = _review(_approved(title=HIGHLIGHT_TITLE), _highlight_decision())
    assert review.verdict == AGREE


# --- refused: missing evidence ---------------------------------------------


def test_a_title_with_no_claim_evidence_is_refused():
    decision = {
        "policy_version": DEFAULT_POLICY.version,
        "format": "longform",
        "chosen_index": 0,
        "chosen_title": BASELINE_TITLE,
        "reason": "facts_unavailable",
        "candidates": [],
    }
    review = _review(decision=decision)
    assert review.verdict == REFUSED
    assert "title_without_evidence" in review.reasons


def test_an_evidence_id_not_supplied_is_refused():
    evidence = [e for e in EVIDENCE if e["evidence_id"] != "seg-9:gps"]
    review = _review(evidence=evidence)
    assert review.verdict == REFUSED
    assert "evidence_missing:seg-9:gps" in review.reasons


def test_visible_text_without_evidence_is_refused():
    approved = _approved(
        visible=[
            {"surface": "ost", "text": OST, "evidence_ids": []},
            {"surface": "thumbnail", "text": OST, "evidence_ids": list(OST_IDS)},
        ]
    )
    review = _review(approved)
    assert review.verdict == REFUSED
    assert "surface_without_evidence:ost" in review.reasons


def test_a_missing_date_evidence_id_is_refused():
    review = _review(_approved(date_evidence_id=None))
    assert review.verdict == REFUSED
    assert "date_without_evidence" in review.reasons


def test_a_missing_recording_date_is_refused():
    review = _review(_approved(recording_date=""))
    assert review.verdict == REFUSED
    assert "missing_field:recording_date" in review.reasons


def test_a_missing_approval_hash_is_refused():
    approved = _approved()
    del approved["approval_hash"]
    review = _review(approved)
    assert review.verdict == REFUSED
    assert "missing_field:approval_hash" in review.reasons


def test_no_visible_surfaces_is_refused():
    review = _review(_approved(visible=[]))
    assert review.verdict == REFUSED
    assert "missing_field:visible" in review.reasons


# --- refused: ambiguous or contradicting evidence --------------------------


def test_one_id_with_two_values_is_ambiguous():
    evidence = [*EVIDENCE, {**EVIDENCE[1], "value": "Quillhaven Junction"}]
    review = _review(evidence=evidence)
    assert review.verdict == REFUSED
    assert "evidence_ambiguous:seg-9:gps" in review.reasons


def test_an_identical_repeated_item_is_not_ambiguous():
    assert _review(evidence=[*EVIDENCE, dict(EVIDENCE[1])]).verdict == AGREE


def test_a_date_id_that_names_a_location_is_refused():
    review = _review(_approved(date_evidence_id="seg-1:gps"))
    assert review.verdict == REFUSED
    assert "evidence_kind:seg-1:gps" in review.reasons


def test_a_date_that_differs_from_its_evidence_is_refused():
    review = _review(_approved(recording_date="2026-03-03"))
    assert review.verdict == REFUSED
    assert "date_mismatch:rec:date" in review.reasons


def test_evidence_for_a_place_the_text_does_not_show_is_refused():
    approved = _approved(
        visible=[
            {
                "surface": "ost",
                "text": "Quillhaven, Nordale",
                "evidence_ids": list(OST_IDS),
            },
            {"surface": "thumbnail", "text": OST, "evidence_ids": list(OST_IDS)},
        ]
    )
    review = _review(approved)
    assert review.verdict == REFUSED
    assert "claim_not_visible:ost:seg-1:gps" in review.reasons


def test_a_duplicate_surface_is_refused():
    approved = _approved(
        visible=[
            {"surface": "ost", "text": OST, "evidence_ids": list(OST_IDS)},
            {"surface": "ost", "text": "Quillhaven", "evidence_ids": ["seg-9:gps"]},
        ]
    )
    review = _review(approved)
    assert review.verdict == REFUSED
    assert "duplicate_surface:ost" in review.reasons


def test_a_decision_whose_title_is_not_its_chosen_candidate_is_refused():
    decision = _baseline_decision()
    decision["chosen_title"] = "Somewhere Else in 4K"
    review = _review(decision=decision)
    assert review.verdict == REFUSED
    assert "decision_malformed" in review.reasons


def test_refusal_still_records_a_disagreement():
    review = _review(decision=_highlight_decision(), evidence=EVIDENCE[:-1])
    assert review.verdict == REFUSED
    assert "title_changed_after_approval" in review.reasons
    assert "evidence_missing:rec:date" in review.reasons


# --- Shorts ----------------------------------------------------------------


def test_a_short_reviews_its_own_burned_text_and_place_title():
    facts = TitleFacts(
        format="short",
        baseline_title="Quillhaven #shorts",
        place=Place("Quillhaven", "seg-9:gps"),
    )
    decision = record_to_dict(
        select_title(
            facts, EvidenceSnapshot(as_of="2026-03-20T00:00:00"), DEFAULT_POLICY
        )
    )
    approved = _approved(
        title="Quillhaven #shorts",
        visible=[
            {
                "surface": "shorts_ost",
                "text": "Quillhaven",
                "evidence_ids": ["seg-9:gps"],
            }
        ],
    )
    review = review_final_text(approved, decision, EVIDENCE)
    assert review.verdict == AGREE
    assert [s.surface for s in review.surfaces] == [
        "shorts_ost",
        "title",
        "recording_date",
    ]
    verify_review(
        review, final_title="Quillhaven #shorts", visible={"shorts_ost": "Quillhaven"}
    )
    with pytest.raises(FinalTextReviewRefused, match="shorts_ost"):
        verify_review(
            review,
            final_title="Quillhaven #shorts",
            visible={"shorts_ost": "Quillhaven Junction"},
        )


# --- determinism and binding -----------------------------------------------


def test_the_same_inputs_in_any_order_give_the_same_fingerprint():
    shuffled = list(EVIDENCE)
    random.Random(7).shuffle(shuffled)
    assert _review(evidence=shuffled).fingerprint == _review().fingerprint
    assert len(_review().fingerprint) == 64


def test_a_changed_approval_changes_the_fingerprint():
    assert (
        _review(_approved(approval_hash="b" * 64)).fingerprint != _review().fingerprint
    )


def _visible():
    return {"ost": OST, "thumbnail": OST}


def test_verify_passes_for_the_reviewed_payload():
    verify_review(_review(), final_title=BASELINE_TITLE, visible=_visible())


def test_verify_refuses_a_later_title_mutation():
    with pytest.raises(FinalTextReviewRefused, match="title"):
        verify_review(_review(), final_title=HIGHLIGHT_TITLE, visible=_visible())


def test_verify_refuses_changed_visible_text():
    with pytest.raises(FinalTextReviewRefused, match="thumbnail"):
        verify_review(
            _review(),
            final_title=BASELINE_TITLE,
            visible={"ost": OST, "thumbnail": "Quillhaven"},
        )


def test_verify_refuses_a_surface_added_or_dropped():
    with pytest.raises(FinalTextReviewRefused, match="surfaces"):
        verify_review(_review(), final_title=BASELINE_TITLE, visible={"ost": OST})
    with pytest.raises(FinalTextReviewRefused, match="surfaces"):
        verify_review(
            _review(),
            final_title=BASELINE_TITLE,
            visible={**_visible(), "shorts_ost": "Quillhaven"},
        )


@pytest.mark.parametrize("decision,evidence", [(None, []), ("highlight", None)])
def test_verify_refuses_a_review_that_did_not_agree(decision, evidence):
    review = _review(
        decision=_highlight_decision() if decision else None, evidence=evidence
    )
    with pytest.raises(FinalTextReviewRefused, match="verdict"):
        verify_review(review, final_title=review.final_title, visible=_visible())


def test_verify_refuses_an_edited_review():
    forged = replace(_review(decision=_highlight_decision()), verdict=AGREE, reasons=())
    with pytest.raises(FinalTextReviewRefused, match="fingerprint"):
        verify_review(forged, final_title=HIGHLIGHT_TITLE, visible=_visible())


def test_review_to_dict_is_json_safe():
    import json

    data = review_to_dict(_review())
    assert json.loads(json.dumps(data)) == data
    assert data["verdict"] == AGREE
