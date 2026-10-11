"""Which clock hours each region posts its shorts at, decided by Waypost.

``analytics.volume_controller`` decides WHETHER the hours move (one swap, cooldown,
probation, rollback); this module is the pure part it leans on:

* ``parse_windows`` / ``check_coupling``: a window set is distinct ``"HH:MM"``
  strings, and ``shorts_per_lf`` must fit one drive's share of them times
  ``LF_PUBLISH_INTERVAL_DAYS``.
* ``drive_share`` / ``share_size``: a region's list is every hour its shorts post at.
  The drive of each LF day takes an equal share of it, so two LFs of one region whose
  shorts windows overlap never want the same hour.
* ``rank_hours``: a region's settled shorts, scored per top-of-hour. An hour's score
  is the mean of its weekday-cell means, so an uneven weekday mix does not bias it.
  An hour is *measured* only with ``PROBATION_MIN_N`` settled shorts.
* ``effective_times``: the fail-closed reader (latest decision row, else the yaml
  seed), the shape of ``lf_days.effective_dow``.
* ``candidate_hours``: the hours a swap may bring in: the policy rail minus the
  hours another region posts shorts at (``fence_for``). Long-form publish hours are
  not fenced.

Only top-of-hour windows are decided. A seed window that is not on the hour is left
alone: never a swap source, never a target. Hours are in the brand timezone, the same
zone as the evidence's ``hour_local`` and the yaml times.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from seo_manager.analytics import volume_policy as policy


def hhmm(hour: int, minute: int = 0) -> str:
    return f"{hour:02d}:{minute:02d}"


def parse_windows(windows: Any) -> list[tuple[int, int]]:
    """``(hour, minute)`` per window; ``ValueError`` on a non-list, a malformed or a repeated one."""
    if not isinstance(windows, list):
        raise ValueError(f"shorts_times must be a list of 'HH:MM', got {windows!r}")
    out: list[tuple[int, int]] = []
    for w in windows:
        ok = isinstance(w, str) and len(w) == 5 and w[2] == ":"
        h, m = (w[:2], w[3:]) if ok else ("", "")
        if not (h.isdigit() and m.isdigit() and int(h) < 24 and int(m) < 60):
            raise ValueError(f"shorts_times window {w!r} is not 'HH:MM'")
        out.append((int(h), int(m)))
    if len(set(out)) != len(out):
        raise ValueError(f"shorts_times {windows!r} repeats a window")
    return out


def share_size(n_windows: int, n_lf_days: int) -> int:
    """How many of a region's windows one LF's drive posts at each day."""
    return n_windows // max(n_lf_days, 1)


def drive_share(
    windows: Iterable[Any], lf_days: Iterable[int], lf_day: int | None
) -> list[Any]:
    """The windows the drive anchored on weekday ``lf_day`` posts its shorts at.

    The sorted windows are dealt round-robin over the region's sorted LF days, each
    drive keeping ``share_size`` of them; the last LF day of the week starts at the
    earliest window (any fixed order would do: this one reproduces the per-day table
    the publishing pipeline carried by hand). A day off the cadence, or ``None``, takes the first LF
    day's share. A window left over by an uneven split belongs to no drive.
    """
    ws = sorted(windows)
    days = sorted(set(lf_days))
    d = max(len(days), 1)
    i = days.index(lf_day) if lf_day in days else 0
    return ws[d - 1 - i :: d][: share_size(len(ws), d)]


def check_coupling(shorts_per_lf: int, windows: Any, lf_days: int = 1) -> None:
    """``ValueError`` unless the windows are well formed and can carry ``shorts_per_lf``.

    The invariant is ``shorts_per_lf <= share_size(windows, lf_days) *
    LF_PUBLISH_INTERVAL_DAYS``: one drive posts at its share of the region's windows
    on each day until the next LF. The interval lives in the store module, imported
    here to avoid an import cycle.
    """
    from seo_manager.analytics.cadence_decisions import LF_PUBLISH_INTERVAL_DAYS as cap

    n = len(parse_windows(windows))
    if shorts_per_lf > share_size(n, lf_days) * cap:
        raise ValueError(
            f"coupling: {n} window(s) over {max(lf_days, 1)} LF day(s) x {cap} "
            f"cannot carry shorts_per_lf {shorts_per_lf}"
        )


@dataclass(frozen=True)
class RankedHour:
    hour: int
    n: int
    score: float
    median: float


def rank_hours(shorts: Iterable[Any], need: int | None = None) -> list[RankedHour]:
    """Measured hours of ``shorts`` (``.weekday``, ``.hour_local``, ``.day7_views``), best first.

    Score = mean over the hour's weekday cells of each cell's mean day-7 views.
    Ties fall to the lower hour. Hours with fewer than ``need`` shorts (default
    ``PROBATION_MIN_N``) are not measured and are left out.
    """
    need = policy.PROBATION_MIN_N if need is None else need
    cells: dict[int, dict[int, list[float]]] = {}
    for s in shorts:
        by_day = cells.setdefault(s.hour_local, {})
        by_day.setdefault(s.weekday, []).append(float(s.day7_views))
    ranked = []
    for hour, by_day in cells.items():
        views = [v for cell in by_day.values() for v in cell]
        if len(views) < need:
            continue
        score = statistics.fmean(statistics.fmean(c) for c in by_day.values())
        ranked.append(RankedHour(hour, len(views), score, statistics.median(views)))
    return sorted(ranked, key=lambda r: (-r.score, r.hour))


def candidate_hours(fenced: Iterable[int] = ()) -> list[int]:
    """Hours inside the policy rail that no other region owns."""
    taken = set(fenced)
    lo, hi = policy.SHORTS_HOUR_FLOOR, policy.SHORTS_HOUR_CEILING
    return [h for h in range(lo, hi + 1) if h not in taken]


def ranked_json(ranked: Sequence[RankedHour]) -> list[dict[str, Any]]:
    """The measured hours in rank order, as a row's ``evidence["ranked"]``."""
    return [{"hour": r.hour, "n": r.n, "score": round(r.score, 3)} for r in ranked]


@dataclass(frozen=True)
class EffectiveTimes:
    """The shorts windows in force for a region, and where they came from."""

    times: tuple[str, ...]
    source: str  # "seed" (brand.yaml) or "decision" (a row in the log)
    applied_at: str | None = None
    row_id: int | None = None


def effective_times(db: Path | str, region: str, seed: Sequence[str]) -> EffectiveTimes:
    """The windows ``region`` posts at: its latest decision's value, else ``seed``.

    A malformed stored value raises ``ValueError`` (fail closed: stop the cron
    rather than book the wrong hour).
    """
    from seo_manager.analytics import cadence_decisions

    stored = cadence_decisions.current(Path(db), "shorts_times", region)
    if stored.id is None or stored.value is None:
        return EffectiveTimes(tuple(sorted(seed)), "seed")
    try:
        parse_windows(stored.value)
    except ValueError as exc:
        raise ValueError(
            f"region {region!r}, decision row {stored.id}: {exc}"
        ) from None
    return EffectiveTimes(
        tuple(sorted(stored.value)), "decision", stored.applied_at, stored.id
    )


def fence_for(region: str, others: Mapping[str, Iterable[int]]) -> frozenset[int]:
    """Hours every region other than ``region`` holds (``others``: region -> hours)."""
    return frozenset(h for r, hs in others.items() if r != region for h in hs)
