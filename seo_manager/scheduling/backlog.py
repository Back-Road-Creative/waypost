"""``drive_backlog.yaml`` -- the ordered manifest of drives still to audit (tenant data).

Editing that file is the only human input the cadence needs. The reader fails closed
(unknown key, empty sources, duplicate project) and ``next_row`` picks the first row not
yet past its audit (see ``next_row``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from seo_manager.core.profile import Cadence, canon_region
from seo_manager.scheduling.store import ScheduleStore

_FIELDS = {
    "project", "region", "sources", "ingest_mode", "lf_mode", "notes",
    "day_gpx", "camera_utc_offset_hours", "placeless_label",
}  # fmt: skip
_REQUIRED = _FIELDS - {"notes", "day_gpx", "camera_utc_offset_hours", "placeless_label"}
_MAX_UTC_OFFSET_H = 14


@dataclass(frozen=True)
class BacklogRow:
    project: str
    region: str
    sources: tuple[str, ...]
    ingest_mode: str
    lf_mode: str
    notes: str = ""
    # A whole-day track for footage with no GPS of its own, and the hours the camera clock
    # runs from UTC (US Eastern summer = -4). Unset, the drive measures the offset from
    # each recording's creation_time and refuses a set one that disagrees.
    day_gpx: str = ""
    camera_utc_offset_hours: float | None = None
    # On-screen text for footage with no GPS at all and no track to slice: a label that
    # names no place (the drive refuses one that does).
    placeless_label: str = ""


def load(path: Path | str) -> list[BacklogRow]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path}: manifest must be a list of rows")
    rows: list[BacklogRow] = []
    for i, r in enumerate(raw):
        where = f"{path} row {i + 1}"
        if not isinstance(r, dict):
            raise ValueError(f"{where}: expected a mapping")
        if unknown := sorted(set(r) - _FIELDS):
            raise ValueError(f"{where}: unknown key(s) {unknown}")
        if missing := sorted(k for k in _REQUIRED if not r.get(k)):
            raise ValueError(f"{where}: missing or empty {missing}")
        if not isinstance(r["sources"], list):
            raise ValueError(f"{where}: sources must be a list")
        rows.append(
            BacklogRow(
                r["project"],
                canon_region(r["region"]),
                tuple(str(s) for s in r["sources"]),
                r["ingest_mode"],
                r["lf_mode"],
                r.get("notes") or "",
                *_day_gpx(r, where),
                _placeless_label(r, where),
            )
        )
    names = [r.project for r in rows]
    if dupes := sorted({n for n in names if names.count(n) > 1}):
        raise ValueError(f"{path}: duplicate project(s) {dupes}")
    return rows


def _day_gpx(r: dict, where: str) -> tuple[str, float | None]:
    """``(day_gpx, camera_utc_offset_hours)``: an offset needs a track; a track may leave
    the offset unset for the drive to measure."""
    gpx, off = str(r.get("day_gpx") or ""), r.get("camera_utc_offset_hours")
    if off is not None and not gpx:
        raise ValueError(f"{where}: camera_utc_offset_hours needs day_gpx")
    if off is None:
        return gpx, None
    if isinstance(off, bool) or not isinstance(off, (int, float)):
        raise ValueError(
            f"{where}: camera_utc_offset_hours must be a number, got {off!r}"
        )
    if abs(off) > _MAX_UTC_OFFSET_H:
        raise ValueError(
            f"{where}: camera_utc_offset_hours must be between -14 and 14, got {off}"
        )
    return gpx, float(off)


def _placeless_label(r: dict, where: str) -> str:
    """The row's ``placeless_label``: text, never blank, never beside a ``day_gpx``."""
    if "placeless_label" not in r:
        return ""
    label = r["placeless_label"]
    if not isinstance(label, str):
        raise ValueError(f"{where}: placeless_label must be text, got {label!r}")
    if not label.strip():
        raise ValueError(f"{where}: placeless_label is blank")
    if r.get("day_gpx"):
        raise ValueError(
            f"{where}: placeless_label and day_gpx are exclusive; a tracked drive "
            "names its places"
        )
    return label.strip()


def next_row(
    rows: list[BacklogRow], region: str, store: ScheduleStore
) -> BacklogRow | None:
    """First row of ``region`` not yet past its audit: no ``ready_drives`` entry, or one still
    ``auditing`` (resume it). A ready, claimed, complete or failed row is never returned."""
    region = canon_region(region)
    known = {d.project for d in store.all_drives(region) if d.status != "auditing"}
    return next(
        (r for r in rows if r.region == region and r.project not in known), None
    )


def shortfall(store: ScheduleStore, cadence: Cadence, region: str) -> int:
    """How many audited drives ``region`` is short of ``cadence.ready_depth``."""
    cadence.region(region)
    return max(0, cadence.ready_depth - store.ready_depth(region))
