"""The typed surfaces a final-text record carries, and Waypost's templates.

A description is never reviewed as free prose: it is split into typed
surfaces, each checked its own way by ``review_final_text``:

- ``place_line``: text whose every name is cited, checked like on-screen
  text (``scope`` ``whole`` or ``segment``);
- ``template``: Waypost-owned boilerplate, accepted only when its
  ``template_id`` is registered and its bytes hash to the registered hash;
- ``chapter``: a chapter label, a segment surface over its own window
  (``segment_places``);
- ``tag``: a place tag backed by evidence, or a tag from a registered tag
  template.

``build_visible`` turns typed parts into the record's ``visible`` entries.
It takes template text only from ``TEMPLATES``, so it cannot emit an
unregistered block. A hash binds a template's bytes, not their meaning:
templates change only in a reviewed Waypost PR.
"""

import hashlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

PLACE_LINE = "place_line"
TEMPLATE = "template"
CHAPTER = "chapter"
TAG = "tag"
VISIBLE_KINDS = frozenset({PLACE_LINE, TEMPLATE, CHAPTER, TAG})

TEMPLATES: Dict[str, str] = {
    "description.chapters_heading": "Chapters",
    "description.footer": "Subscribe for more drives.",
    "tag.scenic_drive": "scenic drive",
    "tag.road_trip": "road trip",
    "tag.driving_tour": "driving tour",
    "tag.4k_drive": "4k drive",
}


def template_hash(text: str) -> str:
    """SHA-256 of ``text``'s UTF-8 bytes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def template_hashes() -> Tuple[Tuple[str, str], ...]:
    """``(template_id, hash)`` for every registered template, sorted by id."""
    return tuple(sorted((tid, template_hash(t)) for tid, t in TEMPLATES.items()))


@dataclass(frozen=True)
class PlaceLine:
    surface: str
    text: str
    evidence_ids: Tuple[str, ...]
    scope: str = "whole"
    segment_places: Tuple[str, ...] = ()


@dataclass(frozen=True)
class TemplateBlock:
    surface: str
    template_id: str


@dataclass(frozen=True)
class Chapter:
    """A chapter starting ``start_s`` into the video, over its own window."""

    surface: str
    start_s: int
    label: str
    evidence_ids: Tuple[str, ...]
    segment_places: Tuple[str, ...]


@dataclass(frozen=True)
class Tag:
    """A place tag (``text`` + ``evidence_ids``) or a template tag."""

    surface: str
    text: str = ""
    evidence_ids: Tuple[str, ...] = ()
    template_id: Optional[str] = None


Part = Union[PlaceLine, TemplateBlock, Chapter, Tag]


def _timestamp(seconds: int) -> str:
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _entry(part: Part) -> Dict[str, Any]:
    if isinstance(part, PlaceLine):
        entry: Dict[str, Any] = {
            "surface": part.surface,
            "kind": PLACE_LINE,
            "text": part.text,
            "evidence_ids": list(part.evidence_ids),
            "scope": part.scope,
        }
        if part.segment_places:
            entry["segment_places"] = list(part.segment_places)
        return entry
    if isinstance(part, TemplateBlock):
        return {
            "surface": part.surface,
            "kind": TEMPLATE,
            "text": TEMPLATES[part.template_id],
            "template_id": part.template_id,
        }
    if isinstance(part, Chapter):
        return {
            "surface": part.surface,
            "kind": CHAPTER,
            "text": f"{_timestamp(part.start_s)} {part.label}",
            "evidence_ids": list(part.evidence_ids),
            "segment_places": list(part.segment_places),
        }
    if part.template_id is not None:
        return {
            "surface": part.surface,
            "kind": TAG,
            "text": TEMPLATES[part.template_id],
            "evidence_ids": [],
            "template_id": part.template_id,
        }
    if not part.evidence_ids:
        raise ValueError(f"tag {part.surface!r} needs evidence or a template")
    return {
        "surface": part.surface,
        "kind": TAG,
        "text": part.text,
        "evidence_ids": list(part.evidence_ids),
    }


def build_visible(parts: Sequence[Part]) -> List[Dict[str, Any]]:
    """The record's ``visible`` entries for ``parts``, in order."""
    return [_entry(part) for part in parts]
