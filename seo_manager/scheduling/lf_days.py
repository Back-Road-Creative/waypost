"""Which weekdays the long-form slots use, decided by Waypost, not a person.

``analytics.volume_decide`` decides HOW MANY LF days a region runs; this module
decides WHICH (``choose_dow``, deterministic), measures each weekday
(``lf_weekday_medians``) and reads the answer back (``effective_dow``: the latest
decision row's ``evidence["dow"]``, else the ``brand.yaml`` ``dow`` as the seed).
``core.profile.load_cadence`` imports this lazily to avoid an import cycle.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from seo_manager.analytics import cadence_decisions
from seo_manager.analytics import volume_policy as policy
from seo_manager.analytics.evidence import LongformRecord, parse_ts

_WEEK = 7


def _gap(a: int, b: int) -> int:
    d = abs(a - b) % _WEEK
    return min(d, _WEEK - d)


def _min_gap(days: Iterable[int]) -> int:
    """Smallest circular distance between any two of ``days`` (a week if fewer than two)."""
    ds = sorted(set(days))
    pairs = [_gap(a, b) for i, a in enumerate(ds) for b in ds[i + 1 :]]
    return min(pairs, default=_WEEK)


def _nearest(day: int, others: Iterable[int]) -> int:
    return min((_gap(day, o) for o in others), default=_WEEK)


def choose_dow(
    current: Sequence[int],
    target_count: int,
    *,
    taken: Iterable[int] = frozenset(),
    weekday_medians: Mapping[int, float] | None = None,
) -> tuple[int, ...]:
    """The weekday set after moving ``current`` to ``target_count`` days (one step).

    Up: of the free weekdays take the one farthest (circular) from every used day;
    tie -> higher measured median (unmeasured ranks lowest), then lower weekday.
    Down: drop the weakest measured day, else the one leaving the widest spacing
    (tie -> higher weekday). ``ValueError`` for a jump other than one, or no free day.
    """
    cur = tuple(sorted(set(current)))
    taken = frozenset(taken)
    medians = weekday_medians or {}
    if target_count == len(cur):
        return cur
    if abs(target_count - len(cur)) != 1:
        raise ValueError(
            f"lf_days moves one step at a time: {len(cur)} -> {target_count}"
        )
    if target_count > len(cur):
        free = [d for d in range(_WEEK) if d not in cur and d not in taken]
        if not free:
            raise ValueError(f"no free weekday: {sorted(set(cur) | taken)} are used")
        used = set(cur) | taken
        best = min(
            free,
            key=lambda d: (-_nearest(d, used), -medians.get(d, float("-inf")), d),
        )
        return tuple(sorted((*cur, best)))
    measured = [d for d in cur if d in medians]
    if measured:
        gone = min(measured, key=lambda d: (medians[d], d))
    else:
        gone = min(
            cur,
            key=lambda d: (-_min_gap({x for x in cur if x != d} | taken), -d),
        )
    return tuple(d for d in cur if d != gone)


def lf_weekday_medians(
    longforms: Iterable[LongformRecord],
    region: str | None,
    tz: ZoneInfo,
    as_of: datetime,
) -> dict[int, float]:
    """Median day-7 views per LOCAL weekday of the region's settled long-forms.

    Measures the LF slot itself, not shorts. Settled = ``SETTLE_DAYS`` old; records
    with no day-7 number are skipped. ``as_of`` and ``posted_at`` are UTC.
    """
    want = None if region is None else str(region).strip().lower()
    cutoff = as_of - timedelta(days=policy.SETTLE_DAYS)
    by_day: dict[int, list[float]] = {}
    for rec in longforms:
        rec_region = None if rec.region is None else str(rec.region).strip().lower()
        if rec_region != want or rec.day7_views is None:
            continue
        posted = parse_ts(rec.posted_at)
        if posted > cutoff:
            continue
        local = posted.replace(tzinfo=UTC).astimezone(tz)
        by_day.setdefault(local.weekday(), []).append(float(rec.day7_views))
    return {d: float(statistics.median(v)) for d, v in by_day.items()}


@dataclass(frozen=True)
class EffectiveDow:
    """The LF weekdays in force for a region, and where they came from."""

    dow: tuple[int, ...]
    source: str  # "seed" (brand.yaml) or "decision" (a row in the log)
    applied_at: str | None = None
    row_id: int | None = None


def _is_weekday(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and 0 <= v < _WEEK


def effective_dow(db: Path | str, region: str, seed_dow: Sequence[int]) -> EffectiveDow:
    """The weekdays ``region`` runs: its latest decision's ``dow``, else ``seed_dow``.

    A row with no ``dow`` reads as the seed; a malformed ``dow`` raises ``ValueError``
    (fail closed: stop the cron rather than book the wrong slot).
    """
    seed = tuple(sorted(set(seed_dow)))
    lo, hi = policy.LF_DAYS_FLOOR, policy.LF_DAYS_CEILING_PER_REGION
    stored = cadence_decisions.current(Path(db), "lf_days", region, seed=len(seed))
    if stored.id is None or "dow" not in stored.evidence:
        if not lo <= len(seed) <= hi:
            raise ValueError(
                f"region {region!r}: brand.yaml dow {list(seed)} has {len(seed)} "
                f"days, outside the floor/ceiling [{lo}, {hi}]"
            )
        return EffectiveDow(seed, "seed")
    dow = stored.evidence["dow"]
    where = f"region {region!r}, decision row {stored.id}"
    if not (
        isinstance(dow, list)
        and all(_is_weekday(d) for d in dow)
        and len(set(dow)) == len(dow)
    ):
        raise ValueError(f"{where}: dow {dow!r} is not distinct weekdays 0..6")
    if len(dow) != stored.value or not lo <= len(dow) <= hi:
        raise ValueError(
            f"{where}: dow {dow!r} has {len(dow)} days but the row's value is "
            f"{stored.value!r} (floor/ceiling [{lo}, {hi}])"
        )
    return EffectiveDow(tuple(sorted(dow)), "decision", stored.applied_at, stored.id)
