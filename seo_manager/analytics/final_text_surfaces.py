"""The typed surfaces a final-text record carries, and Waypost's templates.

A description is never reviewed as free prose: it is split into typed
surfaces, each checked its own way by ``review_final_text``:

- ``place_line``: text whose every name is cited, checked like on-screen
  text (``scope`` ``whole`` or ``segment``);
- ``template``: Waypost-owned boilerplate, accepted only when its
  ``template_id`` is registered and its bytes hash to the registered hash;
- ``param_template``: a registered skeleton with typed slots (number, unit,
  duration, enum, place), accepted only when the skeleton and slot specs hash
  to the registered hash, every slot value is legal for its type, and the text
  is exactly the skeleton filled; a place slot names an evidence id and is
  backed like a place line (``final_text_params``, ``drive_templates``);
- ``chapter``: a chapter label, a segment surface over its own window
  (``segment_places``);
- ``tag``: a place tag backed by evidence, or a tag from a registered tag
  template.

``build_visible`` turns typed parts into the record's ``visible`` entries.
It takes template text only from ``TEMPLATES``, so it cannot emit an
unregistered block. A hash binds a template's bytes, not their meaning:
templates change only in a reviewed Waypost PR.

``compose_description`` and ``compose_tags`` rebuild, from reviewed entries,
the exact description and tags an upload may send. A caller must build the
description and tags it sends with them, so the uploaded bytes are the
reviewed bytes; ``verify_review`` refuses any other text.
"""

import hashlib
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from .drive_templates import DRIVE_TEXT, PARAMS
from .final_text_params import render, render_value, valid_value

PLACE_LINE = "place_line"
TEMPLATE = "template"
CHAPTER = "chapter"
TAG = "tag"
PARAM_TEMPLATE = "param_template"
VISIBLE_KINDS = frozenset({PLACE_LINE, TEMPLATE, CHAPTER, TAG, PARAM_TEMPLATE})

TEMPLATES: Dict[str, str] = {
    "description.chapters_heading": "Chapters",
    "description.footer": "Subscribe for more drives.",
    "tag.scenic_drive": "scenic drive",
    "tag.road_trip": "road trip",
    "tag.driving_tour": "driving tour",
    "tag.4k_drive": "4k drive",
}


def all_templates() -> Dict[str, str]:
    """Every fixed template and tag: the base set and the drive set."""
    return {**TEMPLATES, **DRIVE_TEXT}


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


@dataclass(frozen=True)
class ParamBlock:
    """A parameterized template with typed ``slots`` (``name -> value``).

    A ``place`` slot's value is an evidence id; ``places`` maps each such id to
    the cited text. A value of the wrong type raises when the entry is built.
    """

    surface: str
    template_id: str
    slots: Mapping[str, Any]
    places: Mapping[str, str] = None  # type: ignore[assignment]


Part = Union[PlaceLine, TemplateBlock, Chapter, Tag, ParamBlock]


def place_text(slot: Any, text: str) -> str:
    """A place slot's rendering: the cited text, or its ``#hashtag`` form."""
    return "#" + "".join(text.split()) if slot.hashtag else text


def _param_entry(part: ParamBlock) -> Dict[str, Any]:
    template = PARAMS[part.template_id]
    if set(part.slots) != {s.name for s in template.slots}:
        raise ValueError(f"slots_differ:{part.surface}")
    rendered: Dict[str, str] = {}
    ids: List[str] = []
    for slot in template.slots:
        value = part.slots[slot.name]
        if slot.type == "place":
            if not isinstance(value, str) or value not in (part.places or {}):
                raise ValueError(f"place_slot:{part.surface}:{slot.name}")
            ids.append(value)
            rendered[slot.name] = place_text(slot, part.places[value])
        elif valid_value(slot, value):
            rendered[slot.name] = render_value(slot, value)
        else:
            raise ValueError(f"slot_value:{part.surface}:{slot.name}")
    return {
        "surface": part.surface,
        # A parameterized tag (``tag.year``) is still sent as a tag.
        "kind": TAG if part.template_id.startswith("tag.") else PARAM_TEMPLATE,
        "text": render(template.skeleton, rendered),
        "template_id": part.template_id,
        "skeleton": template.skeleton,
        "slots": dict(part.slots),
        "evidence_ids": ids,
    }


def _timestamp(seconds: int) -> str:
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _entry(part: Part) -> Dict[str, Any]:
    if isinstance(part, ParamBlock):
        return _param_entry(part)
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
            "text": all_templates()[part.template_id],
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
            "text": all_templates()[part.template_id],
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


DESCRIPTION_SURFACE = "description"


def _field(entry: Any, name: str) -> Any:
    if isinstance(entry, Mapping):
        return entry.get(name)
    return getattr(entry, name, None)


def is_description_surface(entry: Any) -> bool:
    """True for a surface that is part of the sent description or its chapters."""
    kind = _field(entry, "kind")
    surface = _field(entry, "surface")
    if kind == CHAPTER:
        return True
    return (
        kind in (PLACE_LINE, TEMPLATE, PARAM_TEMPLATE)
        and isinstance(surface, str)
        and (
            surface == DESCRIPTION_SURFACE
            or surface.startswith(DESCRIPTION_SURFACE + ".")
        )
    )


def is_tag_surface(entry: Any) -> bool:
    """True for a surface that is one of the sent tags."""
    return _field(entry, "kind") == TAG


def compose_description(entries: Sequence[Any]) -> str:
    """The exact description sent: description and chapter texts joined by newlines.

    ``entries`` are ``visible`` entries (mappings) or review surfaces, in
    record order. On-screen surfaces (``ost``, ``thumbnail``, ``shorts_ost``)
    and tags are not part of the description.
    """
    return "\n".join(
        str(_field(e, "text") or "") for e in entries if is_description_surface(e)
    )


def compose_tags(entries: Sequence[Any]) -> List[str]:
    """The exact tags sent: the text of each tag surface, in record order."""
    return [str(_field(e, "text") or "") for e in entries if is_tag_surface(e)]
