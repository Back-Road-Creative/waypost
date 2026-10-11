"""Tenant SEO profile -- today only the ``seo: cadence:`` block of ``brand.yaml``.

This is the single loader for the cadence block (``seo_manager.scheduling.cadence``
re-exports it); A4's ``BrandSEOProfile`` extends this module rather than adding a
second reader. The reader fails closed: an unknown key, a bad weekday or time, or a
missing block raises ``ValueError`` -- a typo must stop the cron, not silently book
the wrong day. The yaml shape is documented in ``docs/scheduling.md``.

A region's yaml ``dow`` and ``shorts_times`` are only the SEED: with
``load_cadence(path, decisions_db=...)`` the latest decision rows replace them
(``scheduling/lf_days.py``, ``scheduling/shorts_times.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import time as dt_time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

import yaml
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from seo_manager.scheduling.lf_days import EffectiveDow

DAY_NAMES = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
DAY_ABBR = {v: k for k, v in DAY_NAMES.items()}
_FULL_DAY_NAMES = {
    n: i
    for i, n in enumerate(
        ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    )
}
DEFAULT_TZ = "America/New_York"


@dataclass(frozen=True)
class RegionCadence:
    dow: tuple[int, ...]
    time: dt_time
    day_times: Mapping[int, dt_time] = field(default_factory=dict)
    shorts_times: tuple[dt_time, ...] = ()


def canon_region(name: str | None) -> str | None:
    """The one region form: stripped, lower-case (``None`` = anonymous, unchanged)."""
    return None if name is None else str(name).strip().lower()


@dataclass(frozen=True)
class Cadence:
    regions: Mapping[str, RegionCadence]
    lead_hours: int = 72
    ready_depth: int = 2
    superlong_day: int | None = None
    tz: ZoneInfo = field(default_factory=lambda: ZoneInfo(DEFAULT_TZ))
    # region -> where its ``dow`` came from; empty unless a decisions db was supplied.
    dow_source: Mapping[str, EffectiveDow] = field(default_factory=dict)

    def region(self, name: str) -> RegionCadence:
        try:
            return self.regions[canon_region(name)]
        except KeyError:
            raise ValueError(
                f"unknown region {name!r}; cadence has {sorted(self.regions)}"
            ) from None

    def days(self, region: str | None) -> frozenset[int]:
        """Weekdays a long-form may publish; ``None`` is the union over all regions."""
        if region is None:
            return frozenset(d for r in self.regions.values() for d in r.dow)
        return frozenset(self.region(region).dow)

    def time_for(self, region: str | None, weekday: int) -> dt_time:
        """Designed publish time of ``weekday`` -- the day's own entry, else the region default."""
        if region is None:
            region = next(
                n for n, r in sorted(self.regions.items()) if weekday in r.dow
            )
        r = self.region(region)
        return r.day_times.get(weekday, r.time)


def _check_keys(where: str, data: Mapping[str, Any], allowed: set[str]) -> None:
    if not isinstance(data, Mapping):
        raise ValueError(f"{where}: expected a mapping, got {type(data).__name__}")
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(
            f"{where}: unknown key(s) {unknown}; allowed {sorted(allowed)}"
        )


def parse_weekday(where: str, value: Any) -> int:
    token = str(value).strip().lower()
    day = DAY_NAMES.get(token, _FULL_DAY_NAMES.get(token))
    if day is None:
        raise ValueError(f"{where}: invalid weekday {value!r} (use mon..sun)")
    return day


def _hhmm(where: str, value: Any) -> dt_time:
    try:
        h, m = str(value).split(":")
        return dt_time(int(h), int(m))
    except ValueError:
        raise ValueError(f"{where}: invalid time {value!r} (use HH:MM)") from None


def _positive(where: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{where}: must be a non-negative integer, got {value!r}")
    return value


def _region(name: str, data: Mapping[str, Any]) -> RegionCadence:
    where = f"seo.cadence.regions.{name}"
    _check_keys(where, data, {"dow", "time", "day_times", "shorts_times"})
    if not data.get("dow") or "time" not in data:
        raise ValueError(f"{where}: `dow` and `time` are required")
    dow = tuple(sorted({parse_weekday(f"{where}.dow", d) for d in data["dow"]}))
    day_times = {
        parse_weekday(f"{where}.day_times", d): _hhmm(f"{where}.day_times.{d}", t)
        for d, t in (data.get("day_times") or {}).items()
    }
    stray = sorted(DAY_ABBR[d] for d in day_times if d not in dow)
    if stray:
        raise ValueError(f"{where}.day_times: {stray} not in dow")
    return RegionCadence(
        dow=dow,
        time=_hhmm(f"{where}.time", data["time"]),
        day_times=day_times,
        shorts_times=tuple(
            _hhmm(f"{where}.shorts_times", t) for t in data.get("shorts_times") or ()
        ),
    )


def _canonical_regions(regions: Mapping[str, Any]) -> dict[str, RegionCadence]:
    out: dict[str, RegionCadence] = {}
    for n, r in regions.items():
        key = canon_region(n)
        if key in out:
            raise ValueError(f"seo.cadence.regions: {n!r} repeats region {key!r}")
        out[key] = _region(n, r)
    return out


def _overlay(
    regions: Mapping[str, RegionCadence], decisions_db: Path | str
) -> tuple[dict[str, RegionCadence], dict[str, EffectiveDow]]:
    """Replace each region's seed ``dow`` and ``shorts_times`` by the decided ones; drop stale ``day_times``."""
    from seo_manager.scheduling.lf_days import effective_dow
    from seo_manager.scheduling.shorts_times import effective_times

    out: dict[str, RegionCadence] = {}
    source: dict[str, EffectiveDow] = {}
    for name, r in regions.items():
        eff = effective_dow(decisions_db, name, r.dow)
        source[name] = eff
        times = r.shorts_times
        if times:  # no seed windows, nothing to decide
            seed = [t.strftime("%H:%M") for t in times]
            decided = effective_times(decisions_db, name, seed).times
            times = tuple(_hhmm(f"decided shorts_times {name}", t) for t in decided)
        out[name] = replace(
            r,
            dow=eff.dow,
            day_times={d: t for d, t in r.day_times.items() if d in eff.dow},
            shorts_times=times,
        )
    return out, source


def load_cadence(
    brand_yaml_path: Path | str, decisions_db: Path | str | None = None
) -> Cadence:
    """Read ``seo.cadence`` from a tenant ``brand.yaml``; raise ``ValueError`` on any defect.

    With ``decisions_db`` the decided weekday set overlays the yaml ``dow`` seed.
    """
    raw = yaml.safe_load(Path(brand_yaml_path).read_text(encoding="utf-8")) or {}
    seo = raw.get("seo") if isinstance(raw, Mapping) else None
    block = seo.get("cadence") if isinstance(seo, Mapping) else None
    if not block:
        raise ValueError(f"{brand_yaml_path}: no `seo: cadence:` block")
    _check_keys(
        "seo.cadence",
        block,
        {"lead_hours", "ready_depth", "superlong_day", "timezone", "regions"},
    )
    regions = block.get("regions")
    if not regions:
        raise ValueError("seo.cadence.regions: at least one region is required")
    if not isinstance(regions, Mapping):
        raise ValueError("seo.cadence.regions: expected a mapping")
    tz_name = block.get("timezone", DEFAULT_TZ)
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        raise ValueError(f"seo.cadence.timezone: unknown zone {tz_name!r}") from None
    sl = block.get("superlong_day")
    parsed = _canonical_regions(regions)
    dow_source: dict[str, EffectiveDow] = {}
    if decisions_db is not None:
        parsed, dow_source = _overlay(parsed, decisions_db)
    return Cadence(
        regions=parsed,
        lead_hours=_positive("seo.cadence.lead_hours", block.get("lead_hours", 72)),
        ready_depth=_positive("seo.cadence.ready_depth", block.get("ready_depth", 2)),
        superlong_day=None
        if sl is None
        else parse_weekday("seo.cadence.superlong_day", sl),
        tz=tz,
        dow_source=dow_source,
    )
