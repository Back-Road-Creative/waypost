"""Viewer-facing locality naming (Waypost owns which place names reach prose).

A reverse geocode hands back whatever label sits in the address (``hamlet``,
``village`` ...), including tract / subdivision / postal-vicinity names that no
viewer would search ("Longwood", "Oceanmarsh Subdivision"). Reliability rule,
based only on fields the geocoder record already carries
(``match_type``, ``place_class``, ``addresstype``, ``full_address``):

- keep a CENSUS-style place: the matched OSM object is itself a city / town /
  village / hamlet (``match_type``), or - when no ``match_type`` was recorded -
  its ``addresstype`` is city / town / village. A ``house``/``residential``
  match that merely has a hamlet in its address is a vicinity, not a place;
- keep a named LANDMARK: ``place_class`` historic / leisure / tourism /
  military, or a Fort / Park / Battlefield ... name;
- keep a named ISLAND (name ends in "Island");
- drop everything else. When the dropped name lies on a named island, that
  island stands in for it: the island named in its ``full_address``, else the
  one whose bounding box (``NAMED_ISLANDS``) holds its coordinates
  (``entry_latitude``/``entry_longitude`` or ``latitude``/``longitude``).
  Geocoder records seldom carry the island in the address (Beaufort SC: no
  record names St. Helena Island), hence the offline table.

A record with no geocoder facts at all is kept (no evidence to judge by).
Names are deduplicated, first-seen order, case-insensitive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

SETTLEMENT_TYPES = frozenset({"city", "town", "village", "hamlet"})
_ADDRESSTYPE_SETTLEMENTS = frozenset({"city", "town", "village"})
LANDMARK_CLASSES = frozenset({"historic", "leisure", "tourism", "military"})
_LANDMARK_NAME = re.compile(
    r"\b(Fort|Park|Battlefield|Lighthouse|Monument|Preserve|Forest|Beach|Pier)\b"
)
_ISLAND_NAME = re.compile(r"\bIsland$")
# (name, south, north, west, east) - approximate extents of named islands where
# obscure tract names are common; offline, no lookups. Add rows, never guess.
NAMED_ISLANDS = (("St. Helena Island", 32.27, 32.44, -80.66, -80.47),)
_FACT_KEYS = ("match_type", "place_class", "addresstype")


@dataclass
class ViewerLocalities:
    """Reliable names plus ``{dropped name: island or ""}`` for what changed."""

    names: list[str] = field(default_factory=list)
    substitutions: dict[str, str] = field(default_factory=dict)


def dedupe_places(names: Iterable[Any]) -> list[str]:
    """Each place once, first-seen order, case-insensitive; blanks removed."""
    seen: set[str] = set()
    out: list[str] = []
    for n in names or []:
        if not isinstance(n, str) or not n.strip():
            continue
        key = n.strip().lower()
        if key not in seen:
            seen.add(key)
            out.append(n.strip())
    return out


def _island_at(record: dict) -> str | None:
    lat = record.get("entry_latitude", record.get("latitude"))
    lon = record.get("entry_longitude", record.get("longitude"))
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    for name, south, north, west, east in NAMED_ISLANDS:
        if south <= lat <= north and west <= lon <= east:
            return name
    return None


def _island_of(record: dict) -> str | None:
    for part in str(record.get("full_address") or "").split(","):
        part = part.strip()
        if (
            _ISLAND_NAME.search(part)
            and part.lower() != str(record.get("name", "")).lower()
        ):
            return part
    return _island_at(record)


def _is_reliable(record: dict) -> bool:
    name = str(record.get("name") or "")
    if not any(record.get(k) for k in _FACT_KEYS):
        return True
    if _ISLAND_NAME.search(name) or _LANDMARK_NAME.search(name):
        return True
    if record.get("place_class") in LANDMARK_CLASSES:
        return True
    match_type = record.get("match_type")
    if match_type:
        return match_type in SETTLEMENT_TYPES
    addresstype = record.get("addresstype")
    if not addresstype:
        return True  # an OSM class alone is no basis to call a name vicinity
    return addresstype in _ADDRESSTYPE_SETTLEMENTS


def viewer_localities(records: Iterable[Any]) -> ViewerLocalities:
    """Filter locality records (dicts with ``name``, or bare strings)."""
    result = ViewerLocalities()
    for rec in records or []:
        record = {"name": rec} if isinstance(rec, str) else rec
        name = str(record.get("name") or "").strip()
        if not name:
            continue
        if _is_reliable(record):
            result.names.append(name)
            continue
        island = _island_of(record)
        result.substitutions[name] = island or ""
        if island:
            result.names.append(island)
    result.names = dedupe_places(result.names)
    return result
