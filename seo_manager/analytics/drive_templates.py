"""The registered drive-description blocks, tag set and layout.

``DRIVE_TEXT`` holds fixed lines and non-place tags (hashed by their bytes);
``PARAMS`` holds the parameterized lines (hashed by skeleton and slot specs).
``LAYOUTS`` names the ordered blocks a description may be made of, and
``TAG_ORDER`` the order registered tags must keep. Every id here is part of
``FinalTextPolicy.drive_templates``; any change to a skeleton, a slot spec,
a fixed line or a tag changes its hash, so it needs a reviewed Waypost PR and
a bump of ``DRIVE_TEMPLATES_VERSION``.
"""

from typing import Dict, Tuple

from .final_text_params import ParamTemplate, Slot, param_hash

DRIVE_TEMPLATES_VERSION = "drive-templates-2"
LAYOUT = "gms-drive-1"
RULE = "━" * 24

MONTHS = (
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
)  # fmt: skip
SEASONS = ("winter", "spring", "summer", "autumn")
MONTH_ABBR = tuple(m[:3] for m in MONTHS)
HEADINGS = {
    "N": "Northbound",
    "NE": "Northeastbound",
    "E": "Eastbound",
    "SE": "Southeastbound",
    "S": "Southbound",
    "SW": "Southwestbound",
    "W": "Westbound",
    "NW": "Northwestbound",
}
TERRAINS = ("coastal", "mountain", "winding")
BASE_TAGS = (
    "POV driving", "drive", "road trip", "driving video", "4K driving",
    "relaxing drive", "virtual travel", "dashcam", "drive with me",
    "driving route", "POV", "long drive",
)  # fmt: skip
MAX_HASHTAGS = 12


def _slug(text: str) -> str:
    return text.lower().replace(" ", "_")


def _hashtag(tag: str) -> str:
    return "#" + "".join(word.capitalize() for word in tag.split())


DRIVE_TEXT: Dict[str, str] = {
    "description.insert.one": "\U0001f4f8 1 photo/clip insert",
    "description.rule": "\n" + RULE + "\n",
    "description.cta": (
        "\U0001f514 Subscribe for more POV driving videos!\n"
        "\U0001f44d Like this video if you enjoyed the drive\n"
        "\U0001f4ac Comment your favorite moment!\n"
    ),
    "description.site": "\U0001f30d https://gomoveshift.com\n",
    "description.close": RULE,
    "description.chapterhead": "\n\U0001f4cd CHAPTERS",
}
# Tags keep GMS's order (longform_metadata ``_location_tags``): base, cited
# place parts (``PLACE_TAG``, no template id), "<place> drive", year, month,
# season, season drive. Only the place ranks may repeat.
PLACE_TAG = "tag.place"
TAG_ORDER: Tuple[str, ...] = ()
for _tag in BASE_TAGS:
    DRIVE_TEXT[f"tag.{_slug(_tag)}"] = _tag
    TAG_ORDER += (f"tag.{_slug(_tag)}",)
# ``tag.place_drive`` and ``tag.year`` are parameterized: see PARAMS below.
TAG_ORDER += (PLACE_TAG, "tag.place_drive", "tag.year")
REPEATABLE_TAGS = frozenset({PLACE_TAG, "tag.place_drive"})
for _i, _name in enumerate(MONTHS):
    DRIVE_TEXT[f"tag.month_{_i + 1}"] = _name
    TAG_ORDER += (f"tag.month_{_i + 1}",)
for _season in SEASONS:
    DRIVE_TEXT[f"tag.season_{_season}"] = _season
    TAG_ORDER += (f"tag.season_{_season}",)
for _season in SEASONS:
    DRIVE_TEXT[f"tag.season_{_season}_drive"] = f"{_season} drive"
    TAG_ORDER += (f"tag.season_{_season}_drive",)

PARAMS: Dict[str, ParamTemplate] = {}


def _param(template_id: str, skeleton: str, *slots: Slot) -> None:
    PARAMS[template_id] = ParamTemplate(template_id, skeleton, slots)


_PLACE = lambda name: Slot(name, "place")  # noqa: E731
_DURATION = lambda name: Slot(name, "duration")  # noqa: E731
_YEAR = lambda name: Slot(  # noqa: E731
    name, "number", minimum=1900.0, maximum=2200.0, group=False
)

_param(
    "description.hook.highlights",
    "Highlights from {source} of driving through {place}.",
    _DURATION("source"),
    _PLACE("place"),
)
_param(
    "description.hook.pov",
    "POV drive through {place} ({duration}).",
    _PLACE("place"),
    _DURATION("duration"),
)
_param(
    "description.seo.arc_road",
    "{region} 4K drive — {a} to {b} via {road}.\n",
    *(_PLACE(n) for n in ("region", "a", "b", "road")),
)
_param(
    "description.seo.arc",
    "{region} 4K drive — {a} to {b}.\n",
    *(_PLACE(n) for n in ("region", "a", "b")),
)
_param("description.seo.region", "4K drive through {region}.\n", _PLACE("region"))
_param(
    "description.body.plain",
    "A {duration} drive through {place}.\n",
    _DURATION("duration"),
    _PLACE("place"),
)
_param(
    "description.body.terrain",
    "A {duration} {terrain} drive through {place}.\n",
    _DURATION("duration"),
    Slot("terrain", "enum", values=TERRAINS),
    _PLACE("place"),
)
_param("description.location.place", "\U0001f4cd Location: {place}", _PLACE("place"))
_param(
    "description.location.road",
    "\U0001f4cd Location: {place} via {road}",
    _PLACE("place"),
    _PLACE("road"),
)
_param(
    "description.duration.cut",
    "\U0001f3ac Final cut: {final} (edited from {source} of footage)",
    _DURATION("final"),
    _DURATION("source"),
)
_param("description.duration.plain", "\U0001f3ac Duration: {final}", _DURATION("final"))
_param(
    "description.distance.single",
    "\U0001f6e3️ Distance: {distance} {unit}",
    Slot("distance", "number"),
    Slot("unit", "unit", units=("km", "mi")),
)
_param(
    "description.distance.both",
    "\U0001f6e3️ Distance: {km} km ({mi} mi)",
    Slot("km", "number"),
    Slot("mi", "number"),
)
_param(
    "description.heading.compass",
    "\U0001f9ed Heading: {heading}",
    Slot("heading", "enum", values=tuple(HEADINGS.values())),
)
_param(
    "description.insert.many",
    "\U0001f4f8 {count} photo/clip inserts",
    Slot("count", "number", minimum=2.0),
)
_param(
    "description.filmed.month",
    "\U0001f4c5 Filmed: {month} {year}",
    Slot("month", "enum", values=MONTH_ABBR),
    _YEAR("year"),
)
_param("tag.place_drive", "{place} drive", Slot("place", "place"))
_param("tag.year", "{year}", _YEAR("year"))
MAX_THROUGH, MAX_ROUTE, MAX_GEO = 12, 8, 4
for _n in range(1, MAX_THROUGH + 1):
    _names = [f"p{k}" for k in range(1, _n + 1)]
    _param(
        f"description.through.{_n}",
        "\U0001f5fa️ Through: " + ", ".join("{" + k + "}" for k in _names),
        *(_PLACE(k) for k in _names),
    )
for _n in range(2, MAX_ROUTE + 1):
    _names = [f"p{k}" for k in range(1, _n + 1)]
    _param(
        f"description.route.{_n}",
        "\U0001f6e4️ Route: " + " → ".join("{" + k + "}" for k in _names),
        *(_PLACE(k) for k in _names),
    )
for _n in range(MAX_GEO + 1):
    _names = [f"p{k}" for k in range(1, _n + 1)]
    _base = " ".join(_hashtag(t) for t in BASE_TAGS[: MAX_HASHTAGS - _n])
    _param(
        f"description.hashtags.{_n}",
        "\n" + "".join("{" + k + "} " for k in _names) + _base,
        *(Slot(k, "place", hashtag=True) for k in _names),
    )

# Ordered blocks of the layout: (block name, required). ``chapter`` is the
# chapter lines themselves and may repeat.
LAYOUTS: Dict[str, Tuple[Tuple[str, bool], ...]] = {
    LAYOUT: (
        ("hook", True), ("seo", False), ("body", False), ("location", True),
        ("duration", True), ("distance", False), ("heading", False),
        ("through", False), ("route", False), ("insert", False),
        ("filmed", False), ("rule", True), ("cta", True), ("site", False),
        ("close", True), ("chapterhead", False), ("chapter", False),
        ("hashtags", True),
    ),
}  # fmt: skip


def drive_hashes() -> Tuple[Tuple[str, str], ...]:
    """``(id, hash)`` for every drive template, sorted by id."""
    from .final_text_surfaces import template_hash

    plain = [(i, template_hash(t)) for i, t in DRIVE_TEXT.items()]
    param = [(i, param_hash(t.skeleton, t)) for i, t in PARAMS.items()]
    return tuple(sorted(plain + param))
