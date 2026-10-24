"""Slot decisions: next free slot, anchor validation, free-date assertion.

The slot logic the video pipeline used to carry: ``next_free_slot``, the
weekday check and ``_assert_lf_dates_free``. Occupancy is DATE-granular (a legacy-time booking still holds
the date) and region-aware; an anonymous booking or ``region=None`` caller sees all regions.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta
from datetime import time as dt_time

from seo_manager.core.profile import DAY_ABBR, Cadence, parse_weekday
from seo_manager.scheduling.store import (
    Booking,
    ScheduleStore,
    SlotTaken,
    regions_clash,
)

# A superlong part upload runs to a 4 h cap and moves tens of GB; 6 h clears it.
SUPERLONG_MIN_PUBLISH_LEAD = timedelta(hours=6)

# The LF series rule: LF n publishes LF1 + 7 * (n - 1) days, the same weekday as LF1.
LF_PUBLISH_INTERVAL_DAYS = 7


def lf_series_dates(lf1: date | str, count: int) -> list[date]:
    """The publish dates of a drive's first ``count`` long-forms, from LF1's date.

    The one decider of the series: the video pipeline (drive schedule, its LF 2..N guard)
    asks here and the claim books exactly these dates. ``lf1`` is a ``date`` or ISO text;
    ``count`` 0 gives an empty list, a negative count or unparseable text raises
    ``ValueError``.
    """
    if count < 0:
        raise ValueError(f"LF count must be >= 0, got {count}")
    base = (
        lf1 if isinstance(lf1, date) else datetime.strptime(str(lf1), "%Y-%m-%d").date()
    )
    return [base + timedelta(days=LF_PUBLISH_INTERVAL_DAYS * i) for i in range(count)]


def _aware(cadence: Cadence, dt: datetime | None) -> datetime:
    dt = dt or datetime.now(cadence.tz)
    return (
        dt.replace(tzinfo=cadence.tz)
        if dt.tzinfo is None
        else dt.astimezone(cadence.tz)
    )


def _occupied(
    booked: Sequence[Booking],
    cadence: Cadence,
    day: date,
    region: str | None,
    block_cross_region: bool,
    project: str | None,
    lf_index: int | None = None,
) -> bool:
    """Is ``day`` (a cadence-zone date) held by a clashing booking other than the caller's
    own row? Own row = ``(project, lf_index)`` as in ``ScheduleStore.book``; without an
    ``lf_index`` every row of ``project`` is exempt."""
    return any(
        not (b.project == project and lf_index in (None, b.lf_index))
        and b.slot_at.astimezone(cadence.tz).date() == day
        and regions_clash(b.region, region, block_cross_region)
        for b in booked
    )


def _booked(store: ScheduleStore, cadence: Cadence) -> list[Booking]:
    if store.tz.key != cadence.tz.key:
        raise ValueError(
            f"store zone {store.tz.key} != cadence zone {cadence.tz.key}; "
            "build ScheduleStore(path, cadence.tz)"
        )
    return store.bookings()


def next_free_slot(
    store: ScheduleStore,
    cadence: Cadence,
    region: str | None,
    *,
    after: datetime | None = None,
    publish_day: str | None = None,
    include_today: bool = True,
    block_cross_region: bool = False,
    horizon_days: int = 120,
    slot_time: dt_time | None = None,
    min_lead: timedelta | None = None,
    any_day: bool = False,
    project: str | None = None,
    lf_index: int | None = None,
) -> datetime:
    """The next FREE long-form slot for ``region`` -- "publish to the next free North Sunday".

    Candidate weekdays come from the region's cadence; ``publish_day`` narrows them and
    ``any_day`` searches all seven. Each candidate uses its own designed time
    (``day_times``) unless ``slot_time`` overrides, so the search validates the real
    datetime it returns. ``include_today=False`` is the drive scheduler's never-today
    base-date rule; ``min_lead`` rejects a candidate closer than that runway to now.
    ``project`` (+ ``lf_index``) exempts that project's own booking.
    """
    now = _aware(cadence, after)
    if any_day:
        days = frozenset(range(7))
    elif publish_day:
        dow = parse_weekday("publish_day", publish_day)
        days = frozenset({dow})
        cadence.days(region)  # unknown region still raises
    else:
        days = cadence.days(region)
    if region is not None:
        cadence.region(region)
    lead = min_lead or timedelta(0)
    booked = _booked(store, cadence)
    for ahead in range(horizon_days):
        candidate = (now + timedelta(days=ahead)).date()
        wd = candidate.weekday()
        if wd not in days or (not include_today and candidate == now.date()):
            continue
        t = slot_time or _designed_time(cadence, region, wd)
        slot = datetime.combine(candidate, t, tzinfo=cadence.tz)
        if slot <= now + lead or _occupied(
            booked, cadence, candidate, region, block_cross_region, project, lf_index
        ):
            continue
        return slot
    raise RuntimeError(
        f"next_free_slot: no free slot for region={region!r} within {horizon_days} days"
    )


def next_free_superlong_slot(
    store: ScheduleStore,
    cadence: Cadence,
    region: str | None,
    *,
    after: datetime | None = None,
    include_today: bool = True,
    block_cross_region: bool = True,
    horizon_days: int = 120,
    project: str | None = None,
    lf_index: int | None = None,
) -> datetime:
    """Next free superlong-part slot: ``cadence.superlong_day`` (else any weekday), one shared
    weekly queue across regions (``block_cross_region`` defaults True), 6 h minimum lead."""
    day = None if cadence.superlong_day is None else DAY_ABBR[cadence.superlong_day]
    return next_free_slot(
        store,
        cadence,
        region,
        after=after,
        publish_day=day,
        any_day=day is None,
        include_today=include_today,
        block_cross_region=block_cross_region,
        horizon_days=horizon_days,
        min_lead=SUPERLONG_MIN_PUBLISH_LEAD,
        project=project,
        lf_index=lf_index,
    )


def _designed_time(cadence: Cadence, region: str | None, weekday: int) -> dt_time:
    """Designed time of ``weekday``; a weekday outside the cadence (any-day search) uses the
    region's default time (all-regions view: the first region's)."""
    try:
        return cadence.time_for(region, weekday)
    except (StopIteration, ValueError):
        name = region if region is not None else sorted(cadence.regions)[0]
        return cadence.region(name).time


def anchor_problems(
    cadence: Cadence,
    region: str | None,
    dates: Iterable[str],
    *,
    days: frozenset | None = None,
) -> list[tuple[str, int | None]]:
    """The long-form weekday check as data: ``(date, weekday)`` for every date outside the region's long-form
    days (or the explicit ``days``); ``weekday`` is ``None`` for text that is not a date."""
    allowed = days if days is not None else cadence.days(region)
    bad: list[tuple[str, int | None]] = []
    for d in dates:
        try:
            wd = datetime.strptime(str(d), "%Y-%m-%d").weekday()
        except ValueError:
            bad.append((str(d), None))
            continue
        if wd not in allowed:
            bad.append((str(d), wd))
    return bad


def validate_anchor(
    cadence: Cadence,
    region: str | None,
    dates: Iterable[str],
    *,
    days: frozenset | None = None,
) -> list[str]:
    """The long-form weekday check as an upstream gate: messages for every date whose weekday is outside the
    region's long-form days (or the explicit ``days``). Empty list = valid."""
    allowed = days if days is not None else cadence.days(region)
    bad = [
        f"{d} (unparseable)" if wd is None else f"{d} (weekday={wd})"
        for d, wd in anchor_problems(cadence, region, dates, days=allowed)
    ]
    if not bad:
        return []
    return [
        f"lf_slot dates fall outside LONG_FORM_DAYS={sorted(allowed)}: {', '.join(bad)}"
    ]


def assert_free(
    store: ScheduleStore,
    cadence: Cadence,
    region: str | None,
    dates: Iterable[str],
    *,
    block_cross_region: bool = False,
    project: str | None = None,
    lf_index: Mapping[str, int] | None = None,
) -> None:
    """Raise ``SlotTaken`` if any of ``dates`` is booked for ``region`` by another row
    (``project`` exempts its own rows, or just ``{date: lf_index}`` rows). Never shifts."""
    if region is not None:
        cadence.region(region)  # unknown region raises
    booked = _booked(store, cadence)
    taken = [
        d
        for d in dates
        if _occupied(
            booked,
            cadence,
            datetime.strptime(d, "%Y-%m-%d").date(),
            region,
            block_cross_region,
            project,
            (lf_index or {}).get(d),
        )
    ]
    if taken:
        raise SlotTaken(
            f"long-form date(s) already booked for region={region!r}: {', '.join(taken)}"
        )
