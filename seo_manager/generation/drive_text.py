"""Typed drive text: the description and tags of a driving video, from data.

``build_drive_text`` takes typed data only (evidence-cited places, numbers,
seconds, enum codes) and returns the typed parts of ``final_text_surfaces``
in the registered ``gms-drive-1`` layout order. It never takes a sentence: a
value of the wrong type raises here, before any review, and a place that
cannot be cited is not expressible. Pure: no I/O.

Limits of the registered blocks: at most ``MAX_ROUTE`` route towns,
``MAX_GEO`` geo hashtags, ``MAX_TOWNS`` towns, ``MAX_ROADS`` roads,
``MAX_STATES`` states, ``MAX_FEATURES`` features and ``MAX_PASSING`` passing
places per description; over a limit the generator raises, it never
truncates. ``through`` alone has an overflow slot: past ``MAX_THROUGH`` it
names the first ``MAX_THROUGH`` and counts the rest.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from seo_manager.analytics.drive_templates import (
    BASE_TAGS,
    HEADINGS,
    MAX_FEATURES,
    MAX_GEO,
    MAX_PASSING,
    MAX_ROADS,
    MAX_ROUTE,
    MAX_STATES,
    MAX_THROUGH,
    MAX_TOWNS,
    MONTH_ABBR,
    REGIONS_DISPLAY,
    SEASONS,
    TERRAINS,
    WEEKDAYS,
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
    for s, months in zip(
        SEASONS, ((12, 1, 2), (3, 4, 5), (6, 7, 8), (9, 10, 11)), strict=False
    )
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
    a: PlaceRef | None = None
    b: PlaceRef | None = None
    road: PlaceRef | None = None


@dataclass(frozen=True)
class ChapterData:
    start_s: int
    place: PlaceRef
    window: tuple[str, ...]  # evidence ids of the places in this chapter's window


@dataclass(frozen=True)
class DriveTextData:
    place: PlaceRef
    final_seconds: float
    source_seconds: float | None = None  # footage length before the cut
    highlights: bool = False  # the hook says "Highlights from <source>"
    edited: bool = False  # "Final cut ... edited from" rather than "Duration"
    seo: SeoArc | None = None
    terrain: str | None = None  # one of TERRAINS; None omits the body line
    body: bool = False
    road: PlaceRef | None = None
    distance_km: float | None = None
    us: bool = False  # miles only
    heading: str | None = None  # compass code, a key of HEADINGS
    through: Sequence[PlaceRef] = ()
    route: Sequence[PlaceRef] = ()
    insert_count: int = 0
    filmed: tuple[int, int] | None = None  # (year, month)
    site: bool = True
    geo_hashtags: Sequence[PlaceRef] = ()
    chapters: Sequence[ChapterData] = ()
    place_tags: Sequence[PlaceRef] = ()
    place_drive_tags: Sequence[PlaceRef] = ()  # "<place> drive" tags
    year: int | None = None  # the recording year tag (the record's date backs it)
    month: int | None = None  # adds the month, season and season-drive tags
    base_tags: bool = True
    # GMS body lines: the arc (start to end, passing places) or else towns,
    # then roads, states (one state prints nothing) and features.
    journey_start: PlaceRef | None = None
    journey_end: PlaceRef | None = None
    passing: Sequence[PlaceRef] = ()
    towns: Sequence[PlaceRef] = ()
    roads: Sequence[PlaceRef] = ()
    states: Sequence[PlaceRef] = ()
    features: Sequence[PlaceRef] = ()
    playlist: tuple[str, str] | None = None  # (region, playlist URL)
    cadence: tuple[str, str] | None = None  # (region, weekday)


def _number(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number, got {type(value).__name__}")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _places(blocks: Sequence[PlaceRef]) -> dict[str, str]:
    return {p.evidence_id: p.text for p in blocks}


def _block(
    surface: str, tid: str, slots: dict[str, Any], *places: PlaceRef | None
) -> ParamBlock:
    refs = [p for p in places if p is not None]
    return ParamBlock(
        f"description.{surface}", f"description.{surface}.{tid}", slots, _places(refs)
    )


def build_drive_text(data: DriveTextData) -> list[Part]:
    """The typed parts for ``data``: description blocks, then tags."""
    final = format_duration(_number("final_seconds", data.final_seconds))
    source = None
    if data.source_seconds is not None:
        source = format_duration(_number("source_seconds", data.source_seconds))
    parts: list[Part] = []
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
    journey = _journey(data)
    if journey:
        parts += [*journey, TemplateBlock("description.gap", "description.gap")]
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
    through = list(data.through)
    if through:
        head = through[:MAX_THROUGH]
        slots: dict[str, Any] = _slots("p", head)
        tid = str(len(head))
        if len(through) > MAX_THROUGH:
            slots["overflow"] = len(through) - MAX_THROUGH
            tid += ".more"
        parts.append(_block("through", tid, slots, *head))
    if len(data.route) > MAX_ROUTE:
        raise ValueError(f"route holds at most {MAX_ROUTE} places")
    if len(data.route) >= 2:
        slots = _slots("p", data.route)
        parts.append(_block("route", str(len(data.route)), slots, *data.route))
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
    if (data.playlist or data.cadence) and not data.site:
        # The site line closes the footer group with its blank line.
        raise ValueError("playlist and cadence lines need the site line")
    if data.playlist is not None:
        region, url = data.playlist
        _enum("playlist region", region, REGIONS_DISPLAY)
        parts.append(_block("playlist", "full", {"region": region, "url": url}))
    if data.cadence is not None:
        region, weekday = data.cadence
        _enum("cadence region", region, REGIONS_DISPLAY)
        _enum("cadence weekday", weekday, WEEKDAYS)
        slots = {"region": region, "weekday": weekday}
        parts.append(_block("cadence", "weekly", slots))
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


def _enum(name: str, value: Any, values: Sequence[str]) -> None:
    if value not in values:
        raise ValueError(f"{name} {value!r} is not registered")


def _slots(prefix: str, refs: Sequence[PlaceRef]) -> dict[str, Any]:
    return {f"{prefix}{i}": r.evidence_id for i, r in enumerate(refs, 1)}


def _capped(name: str, refs: Sequence[PlaceRef], cap: int) -> list[PlaceRef]:
    if len(refs) > cap:
        raise ValueError(f"{name} holds at most {cap} places")
    return list(refs)


def _journey(data: DriveTextData) -> list[Part]:
    """GMS body lines: arc or towns (towns and roads share one line), roads,
    states, features."""
    towns = _capped("towns", data.towns, MAX_TOWNS)
    roads = _capped("roads", data.roads, MAX_ROADS)
    states = _capped("states", data.states, MAX_STATES)
    features = _capped("features", data.features, MAX_FEATURES)
    passing = _capped("passing", data.passing, MAX_PASSING)
    a, b = data.journey_start, data.journey_end
    parts: list[Part] = []
    if (a is None) != (b is None):
        raise ValueError("an arc needs both journey_start and journey_end")
    if a is not None and b is not None:
        if a.evidence_id == b.evidence_id:
            raise ValueError("an arc must end somewhere other than its start")
        if towns:
            raise ValueError("towns are the fallback for no arc, not both")
        slots = {"a": a.evidence_id, "b": b.evidence_id, **_slots("p", passing)}
        parts.append(_block("journey", f"arc.{len(passing)}", slots, a, b, *passing))
    elif passing:
        raise ValueError("passing places need an arc")
    elif towns and roads:
        slots = {**_slots("t", towns), **_slots("r", roads)}
        tid = f"towns_roads.{len(towns)}.{len(roads)}"
        parts.append(_block("journey", tid, slots, *towns, *roads))
        roads = []
    elif towns:
        parts.append(
            _block("journey", f"towns.{len(towns)}", _slots("t", towns), *towns)
        )
    if roads:
        parts.append(_block("roads", str(len(roads)), _slots("r", roads), *roads))
    if len(states) >= 2:
        parts.append(_block("states", str(len(states)), _slots("s", states), *states))
    if features:
        tid = str(len(features))
        parts.append(_block("features", tid, _slots("f", features), *features))
    return parts


def _tags(data: DriveTextData) -> list[Part]:
    """Tags in ``TAG_ORDER``: base, places, place drives, year, month, season."""
    tags: list[Part] = []
    if data.base_tags:
        tags += [
            Tag(f"tag.{n}", template_id="tag." + t.lower().replace(" ", "_"))
            for n, t in enumerate(BASE_TAGS, 1)
        ]
    tags += [
        Tag(f"tag.place.{i}", t.text, (t.evidence_id,))
        for i, t in enumerate(data.place_tags, 1)
    ]
    tags += [
        ParamBlock(
            f"tag.place_drive.{i}",
            "tag.place_drive",
            {"place": t.evidence_id},
            {t.evidence_id: t.text},
        )
        for i, t in enumerate(data.place_drive_tags, 1)
    ]
    if data.year is not None:
        tags.append(ParamBlock("tag.year", "tag.year", {"year": data.year}))
    if data.month is not None:
        month = int(_number("month", data.month))
        if not 1 <= month <= 12:
            raise ValueError("month must be 1-12")
        season = _SEASON_BY_MONTH[month]
        tags += [
            Tag(f"tag.{t}", template_id=f"tag.{t}")
            for t in (f"month_{month}", f"season_{season}", f"season_{season}_drive")
        ]
    return tags
