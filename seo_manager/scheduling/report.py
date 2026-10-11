"""Read-only publishing-schedule report over ``lf_bookings`` (port of the
publishing pipeline's schedule report): what is booked, when the queue ends, the next
free slot per (region, designed day), and the next free superlong slot per region.

Composes ``slots`` and mutates nothing. Differences from that report's dict, by design: the
cadence now binds each region to its own weekdays, so ``next_available`` has one row per
(region, ITS day) rather than region x every day; shorts counts and booked titles/kinds
live in the pipeline's content ledger and social queue, not in this store, so they are not here.
"""

from __future__ import annotations

from datetime import datetime

from seo_manager.core.profile import DAY_ABBR, Cadence
from seo_manager.scheduling import slots
from seo_manager.scheduling.store import ScheduleStore


def _matrix(store: ScheduleStore, cadence: Cadence, now: datetime) -> list[dict]:
    out: list[dict] = []
    for name in sorted(cadence.regions):
        rows = []
        for wd in sorted(cadence.days(name)):
            try:
                slot = slots.next_free_slot(
                    store, cadence, name, after=now, publish_day=DAY_ABBR[wd]
                )
            except RuntimeError:
                continue  # no free slot for that day inside the horizon
            rows.append(
                {
                    "region": name,
                    "day": DAY_ABBR[wd],
                    "slot": slot.isoformat(),
                    "shorts_windows": [
                        t.strftime("%H:%M") for t in cadence.region(name).shorts_times
                    ],
                    "earliest_for_region": False,
                }
            )
        if rows:
            min(rows, key=lambda r: r["slot"])["earliest_for_region"] = True
        out.extend(sorted(rows, key=lambda r: r["slot"]))
    return out


def _superlong(store: ScheduleStore, cadence: Cadence, now: datetime) -> list[dict]:
    rows = []
    for name in sorted(cadence.regions):
        try:
            slot = slots.next_free_superlong_slot(store, cadence, name, after=now)
        except RuntimeError:
            continue
        rows.append(
            {"region": name, "day": DAY_ABBR[slot.weekday()], "slot": slot.isoformat()}
        )
    return rows


def _lf_days(cadence: Cadence) -> dict[str, dict]:
    """Per region: LF weekdays in force, and seed or decided."""
    out = {}
    for name in sorted(cadence.regions):
        eff = cadence.dow_source.get(name)
        out[name] = {
            "days": [DAY_ABBR[d] for d in sorted(cadence.days(name))],
            "source": eff.source if eff else "seed",
            "decided_at": eff.applied_at if eff else None,
        }
    return out


def build(
    store: ScheduleStore, cadence: Cadence, *, now: datetime | None = None
) -> dict:
    """The schedule report as a plain dict (the CLI only renders it)."""
    now = slots._aware(cadence, now)
    days = sorted(cadence.days(None))
    upcoming = [b for b in store.bookings() if b.slot_at >= now]
    return {
        "as_of": now.isoformat(),
        "weekly_longform_capacity": len(days),
        "designed_days": [DAY_ABBR[d] for d in days],
        "lf_days": _lf_days(cadence),
        "scheduled_longforms": [
            {
                "date": b.slot_at.strftime("%Y-%m-%d"),
                "time": b.slot_at.strftime("%H:%M:%S"),
                "region": b.region,
                "project": b.project,
                "lf_index": b.lf_index,
                "video_id": b.video_id,
            }
            for b in upcoming
        ],
        "queue_ends": upcoming[-1].slot_at.strftime("%Y-%m-%d") if upcoming else None,
        "next_available": _matrix(store, cadence, now),
        "next_available_superlong": _superlong(store, cadence, now),
    }
