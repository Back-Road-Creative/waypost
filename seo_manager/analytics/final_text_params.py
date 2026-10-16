"""Slot-typed (parameterized) templates for the typed final-text surfaces.

A ``ParamTemplate`` is a registered skeleton, the template bytes with
``{slot}`` markers, plus one typed ``Slot`` per marker. Its hash covers the
skeleton and the slot specs, never the filled text. A slot never carries a
free word: ``number`` is a finite number (rendered here), ``unit`` and
``enum`` take one of their registered values (case-sensitive), ``duration``
takes the one format ``format_duration`` emits, and ``place`` names an
evidence id, whose cited value is backed by the review like a place line. A
string-typed catch-all slot cannot be registered.
"""

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Tuple

SLOT_TYPES = ("number", "unit", "duration", "enum", "place")
_MARKER = re.compile(r"\{([a-z0-9_]+)\}")
_DURATION = re.compile(r"(?:\d+h [0-5]?\d)m|[0-5]?\dm [0-5]?\ds")


@dataclass(frozen=True)
class Slot:
    name: str
    type: str
    minimum: float = 0.0
    maximum: float = float("inf")
    decimals: int = 0
    group: bool = True
    units: Tuple[str, ...] = ()
    values: Tuple[str, ...] = ()
    hashtag: bool = False

    def __post_init__(self) -> None:
        if self.type not in SLOT_TYPES:
            raise ValueError(f"slot_type_unregistered:{self.name}:{self.type}")
        if (self.type == "unit" and not self.units) or (
            self.type == "enum" and not self.values
        ):
            raise ValueError(f"slot_without_values:{self.name}")


@dataclass(frozen=True)
class ParamTemplate:
    template_id: str
    skeleton: str
    slots: Tuple[Slot, ...]

    def __post_init__(self) -> None:
        names = [s.name for s in self.slots]
        if names != _MARKER.findall(self.skeleton):
            raise ValueError(f"slots_differ_from_markers:{self.template_id}")
        stripped = _MARKER.sub("", self.skeleton)
        if "{" in stripped or "}" in stripped:
            raise ValueError(f"stray_brace:{self.template_id}")


def param_hash(skeleton: Any, template: ParamTemplate) -> str:
    """SHA-256 of ``skeleton`` (markers intact) and ``template``'s slot specs."""
    specs = json.dumps([asdict(s) for s in template.slots], sort_keys=True)
    text = f"{skeleton}\x00{specs}" if isinstance(skeleton, str) else "\x00"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def format_duration(seconds: float) -> str:
    """``1h 5m`` from an hour, ``12m 3s`` below it."""
    minutes, secs = divmod(int(seconds), 60)
    if minutes >= 60:
        return f"{minutes // 60}h {minutes % 60}m"
    return f"{minutes}m {secs}s"


def valid_value(slot: Slot, value: Any) -> bool:
    """True when ``value`` is a legal, non-place value for ``slot``."""
    if slot.type == "number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and slot.minimum <= value <= slot.maximum
        )
    if not isinstance(value, str):
        return False
    if slot.type == "duration":
        return _DURATION.fullmatch(value) is not None
    return value in (slot.units if slot.type == "unit" else slot.values)


def render_value(slot: Slot, value: Any) -> str:
    if slot.type != "number":
        return str(value)
    return f"{value:{',' if slot.group else ''}.{slot.decimals}f}"


def render(skeleton: str, rendered: Mapping[str, str]) -> str:
    return _MARKER.sub(lambda m: rendered[m.group(1)], skeleton)
