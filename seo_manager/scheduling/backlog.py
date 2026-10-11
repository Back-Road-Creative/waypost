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

_FIELDS = {"project", "region", "sources", "ingest_mode", "lf_mode", "notes"}
_REQUIRED = _FIELDS - {"notes"}


@dataclass(frozen=True)
class BacklogRow:
    project: str
    region: str
    sources: tuple[str, ...]
    ingest_mode: str
    lf_mode: str
    notes: str = ""


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
            )
        )
    names = [r.project for r in rows]
    if dupes := sorted({n for n in names if names.count(n) > 1}):
        raise ValueError(f"{path}: duplicate project(s) {dupes}")
    return rows


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
