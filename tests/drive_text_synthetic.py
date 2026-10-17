"""Synthetic drive-text inputs shared by the review and generator tests.

Every place, road and number is invented (synthetic); no production text,
route or location appears here.
"""

import copy

from seo_manager.analytics.drive_templates import LAYOUT
from seo_manager.analytics.final_text_review import (
    DEFAULT_TEXT_POLICY,
    RECORD_VERSION,
    review_final_text,
)
from seo_manager.analytics.final_text_surfaces import (
    Chapter,
    ParamBlock,
    Tag,
    TemplateBlock,
    build_visible,
)
from seo_manager.analytics.title_selection import (
    DEFAULT_POLICY,
    EvidenceSnapshot,
    Place,
    TitleFacts,
    record_to_dict,
    select_title,
)
from seo_manager.generation.drive_text import PlaceRef

TITLE = "Brindlemoor to Quillhaven, Harrow County in 4K"
SRC, PLAN = "src-" + "a" * 60, "plan-" + "b" * 59
BRIN = PlaceRef("p-1", "Brindlemoor")
QUILL = PlaceRef("p-2", "Quillhaven")
OCEAN = PlaceRef("p-3", "Oceanmarsh")
FENWICK = PlaceRef("p-4", "Fenwick Reach")  # evidence, but not on the route
HARROW = PlaceRef("r-1", "Harrow County")
ROAD = PlaceRef("rd-1", "Tidewater Road")
EVIDENCE = [
    {"evidence_id": p.evidence_id, "kind": kind, "value": p.text, "source": "gps"}
    for p, kind in (
        (BRIN, "location"), (QUILL, "location"), (OCEAN, "location"),
        (FENWICK, "location"), (HARROW, "region"), (ROAD, "road"),
    )
] + [{"evidence_id": "d-1", "kind": "date", "value": "2026-03-02", "source": "exif"}]  # fmt: skip


def _p(surface, tid, slots, *refs):
    places = {r.evidence_id: r.text for r in refs}
    return ParamBlock(
        f"description.{surface}", f"description.{surface}.{tid}", slots, places
    )


# The typed parts of the (synthetic) golden drive, built by hand.
GOLDEN_PARTS = [
    _p("hook", "highlights", {"source": "2h 5m", "place": "p-2"}, QUILL),
    _p("seo", "arc_road", {"region": "r-1", "a": "p-1", "b": "p-2", "road": "rd-1"},
       HARROW, BRIN, QUILL, ROAD),
    _p("body", "terrain", {"duration": "45m 23s", "terrain": "coastal", "place": "p-2"}, QUILL),
    _p("location", "road", {"place": "p-2", "road": "rd-1"}, QUILL, ROAD),
    _p("duration", "cut", {"final": "45m 23s", "source": "2h 5m"}),
    _p("distance", "both", {"km": 47, "mi": 29}),
    _p("heading", "compass", {"heading": "Westbound"}),
    _p("through", "1", {"p1": "r-1"}, HARROW),
    _p("route", "3", {"p1": "p-1", "p2": "p-3", "p3": "p-2"}, BRIN, OCEAN, QUILL),
    _p("insert", "many", {"count": 3}),
    _p("filmed", "month", {"month": "Sep", "year": 2025}),
    *(TemplateBlock(f"description.{b}", f"description.{b}")
      for b in ("rule", "cta", "site", "close", "chapterhead")),
    Chapter("chapter.1", 0, "Brindlemoor", ("p-1",), ("p-1",)),
    Chapter("chapter.2", 1500, "Quillhaven", ("p-2",), ("p-2",)),
    _p("hashtags", "2", {"p1": "p-1", "p2": "p-2"}, BRIN, QUILL),
    # Tag order: base, year, month, season, season drive, then place tags.
    *(Tag(f"tag.{n}", template_id=f"tag.{t}") for n, t in enumerate((
        "pov_driving", "drive", "road_trip", "driving_video", "4k_driving",
        "relaxing_drive", "virtual_travel", "dashcam", "drive_with_me",
        "driving_route", "pov", "long_drive"), 1)),
    ParamBlock("tag.year", "tag.year", {"year": 2026}),
    *(Tag(f"tag.{t}", template_id=f"tag.{t}")
      for t in ("month_9", "season_autumn", "season_autumn_drive")),
    Tag("tag.place.1", "Brindlemoor", ("p-1",)),
    Tag("tag.place.2", "Quillhaven", ("p-2",)),
]  # fmt: skip


def _decision():
    facts = TitleFacts(
        format="longform", baseline_title=TITLE, origin=Place("Brindlemoor", "p-1"),
        destination=Place("Quillhaven", "p-2"), region=Place("Harrow County", "r-1"),
        suffix=" in 4K",
    )  # fmt: skip
    snap = EvidenceSnapshot(as_of="2026-03-20T00:00:00")
    return record_to_dict(select_title(facts, snap, DEFAULT_POLICY))


def _review(entries, layout=LAYOUT, policy=DEFAULT_TEXT_POLICY):
    record = {
        "record_version": RECORD_VERSION, "approval_hash": "c" * 64, "title": TITLE,
        "recording_date": "2026-03-02", "date_evidence_id": "d-1",
        "source_fingerprint": SRC, "plan_fingerprint": PLAN,
        "route_places": [
            {"evidence_id": "p-1", "retained_seconds": 1200.0, "required": True},
            {"evidence_id": "p-2", "retained_seconds": 480.0, "required": True},
            {"evidence_id": "p-3", "retained_seconds": 30.0, "required": False},
        ],
        "region_claims": [], "visible": list(entries),
    }  # fmt: skip
    if layout:
        record["description_layout"] = layout
    return review_final_text(record, _decision(), EVIDENCE, policy)


def golden_entries():
    return build_visible(GOLDEN_PARTS)


def _find(entries, surface):
    return next(e for e in entries if e["surface"] == surface)


def _edit(surface, **changes):
    entries = copy.deepcopy(golden_entries())
    _find(entries, surface).update(changes)
    return entries
