"""A real final-text review for an upload title, for uploader tests.

The one invented place is named after the whole title, so any title the
tests send can carry a review that agrees with it. The review also covers a
description (a route line naming that place, then the registered footer) and
one template tag; ``sent_text`` returns the description and tags an upload
must send, composed from the review itself.
"""

from typing import Any, Dict, List, Tuple

from seo_manager.analytics.final_text_review import (
    RECORD_VERSION,
    review_final_text,
)
from seo_manager.analytics.final_text_surfaces import (
    Chapter,
    PlaceLine,
    Tag,
    TemplateBlock,
    build_visible,
    compose_description,
    compose_tags,
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


def reviewed(
    title: str, ost: str = "", chapters: bool = False, tags=("tag.scenic_drive",)
):
    """Return ``(final_text_review, final_text_context)`` agreeing on ``title``.

    ``chapters`` adds two chapter lines (``0:00`` and ``2:00``) to the description;
    ``tags`` are the template ids of the reviewed tags, in order.
    """
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
            {
                "surface": "ost",
                "text": ost,
                "evidence_ids": ["p-1"],
                "kind": "place_line",
                "scope": "whole",
            },
            *build_visible(
                [
                    PlaceLine("description.route", title, ("p-1",)),
                    *(
                        [
                            Chapter("chapter.1", 0, title, ("p-1",), ("p-1",)),
                            Chapter("chapter.2", 120, title, ("p-1",), ("p-1",)),
                        ]
                        if chapters
                        else []
                    ),
                    TemplateBlock("description.footer", "description.footer"),
                    *(
                        Tag(f"tag.{i}", template_id=t)
                        for i, t in enumerate(tags, start=1)
                    ),
                ]
            ),
        ],
    }
    review = review_final_text(record, decision, evidence)
    context = {
        "visible": {"ost": ost},
        "source_fingerprint": SOURCE_FP,
        "plan_fingerprint": PLAN_FP,
    }
    return review, context


def sent_text(review) -> Tuple[str, List[str]]:
    """The ``(description, tags)`` an upload must send to match ``review``."""
    surfaces = review["surfaces"] if isinstance(review, dict) else review.surfaces
    return compose_description(surfaces), compose_tags(surfaces)


def sent_fields(title: str) -> Dict[str, Any]:
    """``description`` and ``tags`` overrides matching ``reviewed(title)``."""
    description, tags = sent_text(reviewed(title)[0])
    return {"description": description, "tags": tags}


def upload_kwargs(title: str):
    review, context = reviewed(title)
    return {"final_text_review": review, "final_text_context": context}
