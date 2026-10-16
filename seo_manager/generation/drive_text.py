"""Typed drive text: the description and tags of a driving video, from data.

``build_drive_text`` takes typed data only (evidence-cited places, numbers,
seconds, enum codes) and returns the typed parts of ``final_text_surfaces``
in the registered ``gms-drive-1`` layout order. It never takes a sentence: a
value of the wrong type raises here, before any review, and a place that
cannot be cited is not expressible. Pure: no I/O.

Limits of the registered blocks: at most ``MAX_THROUGH`` regions,
``MAX_ROUTE`` route towns and ``MAX_GEO`` geo hashtags per description; the
caller truncates, the generator raises.
"""

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from seo_manager.analytics.drive_templates import (
    BASE_TAGS,
    HEADINGS,
    MAX_GEO,
    MAX_ROUTE,
    MAX_THROUGH,
    MONTH_ABBR,
    SEASONS,
    TERRAINS,
)
from seo_manager.analytics.final_text_params import format_duration
from seo_manager.analytics.final_text_surfaces import (
    Chapter,
    ParamBlock,
    Part,
    Tag,
    TemplateBlock,
)

_SEASON_BY_MONTH = {
    m: s
    for s, months in zip(SEASONS, ((12, 1, 2), (3, 4, 5), (6, 7, 8), (9, 10, 11)))
    for m in months
}


@dataclass(frozen=True)
class PlaceRef:
    """A place cited by evidence: its id and the exact cited text."""

    evidence_id: str
    text: str


@dataclass(frozen=True)
class SeoArc:
    region: PlaceRef
    a: Optional[PlaceRef] = None
    b: Optional[PlaceRef] = None
    road: Optional[PlaceRef] = None


@dataclass(frozen=True)
class ChapterData:
    start_s: int
    place: PlaceRef
    window: Tuple[str, ...]  # evidence ids of the places in this chapter's window


@dataclass(frozen=True)
class DriveTextData:
    place: PlaceRef
    final_seconds: float
    source_seconds: Optional[float] = None  # footage length before the cut
    highlights: bool = False  # the hook says "Highlights from <source>"
    edited: bool = False  # "Final cut ... edited from" rather than "Duration"
    seo: Optional[SeoArc] = None
    terrain: Optional[str] = None  # one of TERRAINS; None omits the body line
    body: bool = False
    road: Optional[PlaceRef] = None
    distance_km: Optional[float] = None
    us: bool = False  # miles only
    heading: Optional[str] = None  # compass code, a key of HEADINGS
    through: Sequence[PlaceRef] = ()
    route: Sequence[PlaceRef] = ()
    insert_count: int = 0
    filmed: Optional[Tuple[int, int]] = None  # (year, month)
    site: bool = True
    geo_hashtags: Sequence[PlaceRef] = ()
    chapters: Sequence[ChapterData] = ()
    place_tags: Sequence[PlaceRef] = ()
    month: Optional[int] = None  # adds the month, season and season-drive tags
    base_tags: bool = True


def _number(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number, got {type(value).__name__}")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _places(blocks: Sequence[PlaceRef]) -> Dict[str, str]:
    return {p.evidence_id: p.text for p in blocks}


def _block(
    surface: str, tid: str, slots: Dict[str, Any], *places: Optional[PlaceRef]
) -> ParamBlock:
    refs = [p for p in places if p is not None]
    return ParamBlock(
        f"description.{surface}", f"description.{surface}.{tid}", slots, _places(refs)
    )


def build_drive_text(data: DriveTextData) -> List[Part]:
    """The typed parts for ``data``: description blocks, then tags."""
    final = format_duration(_number("final_seconds", data.final_seconds))
    source = None
    if data.source_seconds is not None:
        source = format_duration(_number("source_seconds", data.source_seconds))
    parts: List[Part] = []
    p = data.place
    if data.highlights:
        if source is None:
            raise ValueError("highlights needs source_seconds")
        parts.append(
            _block("hook", "highlights", {"source": source, "place": p.evidence_id}, p)
        )
    else:
        parts.append(
            _block("hook", "pov", {"place": p.evidence_id, "duration": final}, p)
        )
    if (arc := data.seo) is not None:
        names = {"region": arc.region.evidence_id}
        if arc.a and arc.b:
            names |= {"a": arc.a.evidence_id, "b": arc.b.evidence_id}
            if arc.road:
                names["road"] = arc.road.evidence_id
            tid = "arc_road" if arc.road else "arc"
        else:
            tid = "region"
        parts.append(_block("seo", tid, names, arc.region, arc.a, arc.b, arc.road))
    if data.body:
        if data.terrain is None:
            parts.append(
                _block("body", "plain", {"duration": final, "place": p.evidence_id}, p)
            )
        else:
            if data.terrain not in TERRAINS:
                raise ValueError(f"terrain {data.terrain!r} is not registered")
            slots = {"duration": final, "terrain": data.terrain, "place": p.evidence_id}
            parts.append(_block("body", "terrain", slots, p))
    if data.road is None:
        parts.append(_block("location", "place", {"place": p.evidence_id}, p))
    else:
        slots = {"place": p.evidence_id, "road": data.road.evidence_id}
        parts.append(_block("location", "road", slots, p, data.road))
    if data.edited:
        if source is None:
            raise ValueError("edited needs source_seconds")
        parts.append(_block("duration", "cut", {"final": final, "source": source}))
    else:
        parts.append(_block("duration", "plain", {"final": final}))
    if data.distance_km is not None and _number("distance_km", data.distance_km) > 0:
        miles = round(data.distance_km * 0.621371)
        if data.us:
            parts.append(
                _block("distance", "single", {"distance": miles, "unit": "mi"})
            )
        else:
            parts.append(
                _block("distance", "both", {"km": round(data.distance_km), "mi": miles})
            )
    if data.heading is not None:
        if data.heading not in HEADINGS:
            raise ValueError(f"heading {data.heading!r} is not a compass code")
        parts.append(_block("heading", "compass", {"heading": HEADINGS[data.heading]}))
    for name, refs, cap, floor in (
        ("through", data.through, MAX_THROUGH, 1),
        ("route", data.route, MAX_ROUTE, 2),
    ):
        if len(refs) > cap:
            raise ValueError(f"{name} holds at most {cap} places")
        if len(refs) >= floor:
            slots = {f"p{i}": r.evidence_id for i, r in enumerate(refs, 1)}
            parts.append(_block(name, str(len(refs)), slots, *refs))
    count = int(_number("insert_count", data.insert_count))
    if count == 1:
        parts.append(TemplateBlock("description.insert", "description.insert.one"))
    elif count > 1:
        parts.append(_block("insert", "many", {"count": count}))
    if data.filmed is not None:
        year, month = (int(_number("filmed", v)) for v in data.filmed)
        if not 1 <= month <= 12:
            raise ValueError("filmed month must be 1-12")
        parts.append(
            _block("filmed", "month", {"month": MONTH_ABBR[month - 1], "year": year})
        )
    for block in ("rule", "cta"):
        parts.append(TemplateBlock(f"description.{block}", f"description.{block}"))
    if data.site:
        parts.append(TemplateBlock("description.site", "description.site"))
    parts.append(TemplateBlock("description.close", "description.close"))
    if data.chapters:
        parts.append(
            TemplateBlock("description.chapterhead", "description.chapterhead")
        )
    for n, c in enumerate(data.chapters, 1):
        ids = (c.place.evidence_id,)
        parts.append(Chapter(f"chapter.{n}", c.start_s, c.place.text, ids, c.window))
    geo = list(data.geo_hashtags)
    if len(geo) > MAX_GEO:
        raise ValueError(f"geo hashtags hold at most {MAX_GEO} places")
    slots = {f"p{i}": r.evidence_id for i, r in enumerate(geo, 1)}
    parts.append(_block("hashtags", str(len(geo)), slots, *geo))
    parts += _tags(data)
    return parts


def _tags(data: DriveTextData) -> List[Part]:
    tags: List[Part] = [
        Tag(f"tag.place.{i}", t.text, (t.evidence_id,))
        for i, t in enumerate(data.place_tags, 1)
    ]
    ids: List[str] = []
    if data.month is not None:
        month = int(_number("month", data.month))
        if not 1 <= month <= 12:
            raise ValueError("month must be 1-12")
        season = _SEASON_BY_MONTH[month]
        ids += [
            f"tag.month_{month}",
            f"tag.season_{season}",
            f"tag.season_{season}_drive",
        ]
    if data.base_tags:
        ids += ["tag." + t.lower().replace(" ", "_") for t in BASE_TAGS]
    return tags + [Tag(f"tag.{n}", template_id=t) for n, t in enumerate(ids, 1)]
