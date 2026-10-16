"""A real final-text review for an upload title, for uploader tests.

The one invented place is named after the whole title, so any title the
tests send can carry a review that agrees with it.
"""

from seo_manager.analytics.final_text_review import (
    RECORD_VERSION,
    review_final_text,
)
from seo_manager.analytics.title_selection import (
    DEFAULT_POLICY,
    EvidenceSnapshot,
    Place,
    TitleFacts,
    record_to_dict,
    select_title,
)

SOURCE_FP = "src-" + "e" * 60
PLAN_FP = "plan-" + "f" * 59


def reviewed(title: str, ost: str = ""):
    """Return ``(final_text_review, final_text_context)`` agreeing on ``title``."""
    ost = ost or title
    facts = TitleFacts(format="short", baseline_title=title, place=Place(title, "p-1"))
    decision = record_to_dict(
        select_title(
            facts, EvidenceSnapshot(as_of="2026-03-20T00:00:00"), DEFAULT_POLICY
        )
    )
    evidence = [
        {"evidence_id": "p-1", "kind": "location", "value": title, "source": "gps"},
        {"evidence_id": "d-1", "kind": "date", "value": "2026-03-02", "source": "exif"},
    ]
    record = {
        "record_version": RECORD_VERSION,
        "approval_hash": "a" * 64,
        "title": title,
        "recording_date": "2026-03-02",
        "date_evidence_id": "d-1",
        "source_fingerprint": SOURCE_FP,
        "plan_fingerprint": PLAN_FP,
        "route_places": [
            {"evidence_id": "p-1", "retained_seconds": 300.0, "required": True}
        ],
        "region_claims": [],
        "visible": [
            {"surface": "ost", "text": ost, "evidence_ids": ["p-1"], "scope": "whole"}
        ],
    }
    review = review_final_text(record, decision, evidence)
    context = {
        "visible": {"ost": ost},
        "source_fingerprint": SOURCE_FP,
        "plan_fingerprint": PLAN_FP,
    }
    return review, context


def upload_kwargs(title: str):
    review, context = reviewed(title)
    return {"final_text_review": review, "final_text_context": context}
