"""The claim protocol: the publishing pipeline asks for a slot, seo_manager decides and books it.

``claim`` reserves one weekly series (LF1, LF1 + 7 d, ...) for the oldest ``ready`` drive
of a region, LF1 being the first slot whose whole series is free;
``complete`` / ``fail`` close it. A re-claim keeps published LFs untouched. Slot choice happens under the store's file lock and the
bookings land in one ``BEGIN IMMEDIATE`` transaction (``ScheduleStore.claim_drive``), so two
crons can never hold one slot, a stale mirror sha is refused, and a lost race raises
``SlotTaken`` with nothing written.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

from seo_manager.core.profile import Cadence, canon_region
from seo_manager.scheduling.slots import (
    _aware,
    _occupied,
    lf_series_dates,
    next_free_slot,
)
from seo_manager.scheduling.store import Drive, ScheduleStore, SlotTaken, StaleDrive


@dataclass(frozen=True)
class Claim:
    claim_id: str | None  # None on a dry run
    project: str
    region: str
    slots: list
    lf_count: int
    output_dir: str | None
    state_path: str | None
    mirror_sha: str | None
    audit_log: str | None
    already_claimed: bool = False
    stale: bool = False  # an open claim older than one lead window
    lf_mode: str | None = None
    ingest_mode: str | None = None
    # LF indexes (1-based) already published for this drive: their dates are history, so a
    # caller must not re-anchor the drive on them (the pipeline refuses a past --lf-publish-date).
    confirmed_lfs: tuple[int, ...] = ()


def _confirmed_lfs(store: ScheduleStore, project: str) -> tuple[int, ...]:
    return tuple(
        sorted(
            b.lf_index
            for b in store.bookings()
            if b.project == project and b.video_id is not None
        )
    )


def _claim_of(
    drive: Drive,
    slots: list,
    *,
    already: bool = False,
    stale: bool = False,
    confirmed: tuple[int, ...] = (),
) -> Claim:
    return Claim(
        drive.claim_id,
        drive.project,
        drive.region,
        slots,
        drive.lf_count,
        drive.output_dir,
        drive.state_path,
        drive.mirror_sha,
        drive.audit_log,
        already,
        stale,
        drive.lf_mode,
        drive.ingest_mode,
        confirmed,
    )


def _check_sha(drive: Drive, expect_sha: str | None) -> None:
    if expect_sha is not None and drive.mirror_sha != expect_sha:
        held = f" (open claim {drive.claim_id})" if drive.status == "claimed" else ""
        raise StaleDrive(
            f"{drive.project} mirror sha is {drive.mirror_sha!r}, expected {expect_sha!r}{held}"
        )


_EPOCH = date(2000, 1, 3)


def _series_offset(n: int) -> timedelta:
    """How far LF ``n`` sits after LF1, as ``lf_series_dates`` (the one series rule) says."""
    return lf_series_dates(_EPOCH, n)[-1] - _EPOCH


def _plan_series(
    store: ScheduleStore, cadence: Cadence, region: str, drive: Drive, now: datetime
) -> list[datetime]:
    """One slot per LF: the first LF1 whose whole weekly series is free, or, on a re-claim,
    the series a published LF already fixed (a taken date there raises ``SlotTaken``)."""
    booked = store.bookings()
    confirmed = {
        b.lf_index: b.slot_at
        for b in booked
        if b.project == drive.project and b.video_id is not None
    }

    def week(first: datetime, i: int) -> datetime:
        return first + _series_offset(i)

    def taken(slot: datetime, i: int) -> bool:
        return _occupied(booked, cadence, slot.date(), region, False, drive.project, i)

    if confirmed:
        k = min(confirmed)
        first = confirmed[k] - _series_offset(k)
        series = [
            confirmed.get(i) or week(first, i) for i in range(1, drive.lf_count + 1)
        ]
        clash = [
            s.date().isoformat()
            for i, s in enumerate(series, 1)
            if i not in confirmed and taken(s, i)
        ]
        if clash:
            raise SlotTaken(
                f"{drive.project}: LF date(s) {', '.join(clash)} taken; LF{k} is published"
            )
        return series
    after = now
    while True:
        first = next_free_slot(
            store,
            cadence,
            region,
            after=after,
            project=drive.project,
            lf_index=1,
            include_today=False,  # the pipeline's never-today rule; store.claim_drive agrees
        )
        series = [week(first, i) for i in range(1, drive.lf_count + 1)]
        if not any(taken(s, i) for i, s in enumerate(series, 1) if i > 1):
            return series
        after = first  # a later LF's weekday date is held: try the next LF1 slot


def claim(
    store: ScheduleStore,
    cadence: Cadence,
    region: str,
    *,
    now: datetime | None = None,
    lead_hours: int | None = None,
    expect_sha: str | None = None,
    dry_run: bool = False,
) -> Claim | None:
    """Reserve the next free slot(s) for the oldest ready drive of ``region``.

    One row, idempotent: a region with an open claim returns that claim again. Returns
    ``None`` when nothing is ready or the first free slot (never today) is further than ``lead_hours``
    from ``now`` (nothing is booked). ``expect_sha`` refuses (``StaleDrive``) a drive whose
    registered mirror sha differs. ``dry_run`` decides and answers, writes nothing.
    """
    now = _aware(cadence, now)
    horizon = timedelta(hours=cadence.lead_hours if lead_hours is None else lead_hours)
    cadence.region(region)  # unknown region raises
    region = canon_region(region)
    if dry_run and not store.path.exists():
        return None  # nothing to decide from; take no lock, create no directory
    with store.exclusive():
        if open_claims := store.drives(region, "claimed"):
            d = open_claims[0]
            _check_sha(d, expect_sha)
            mine = [b.slot_at for b in store.bookings(region) if b.project == d.project]
            stale = (
                d.claimed_at is not None
                and now - _aware(cadence, d.claimed_at) > horizon
            )
            if (
                stale and not dry_run
            ):  # a claim nobody completed or failed blocks the region
                store.mark_alert("stale_claim", d.claim_id, now)
            return _claim_of(
                d,
                mine,
                already=True,
                stale=stale,
                confirmed=_confirmed_lfs(store, d.project),
            )
        ready = store.drives(region, "ready")
        if not ready:
            return None
        # A drive registered at another revision is skipped so it cannot block the
        # region. Each skip raises a once-a-day alert.
        fresh_drives = [
            d for d in ready if expect_sha is None or d.mirror_sha == expect_sha
        ]
        for d in ready:
            if d not in fresh_drives and not dry_run:
                store.mark_alert("stale_sha", d.project, now)
        if not fresh_drives:
            _check_sha(ready[0], expect_sha)  # raises StaleDrive
        drive = fresh_drives[0]
        confirmed = {
            b.lf_index: b.slot_at
            for b in store.bookings()
            if b.project == drive.project and b.video_id is not None
        }
        chosen = _plan_series(store, cadence, region, drive, now)
        fresh = [s for i, s in enumerate(chosen, 1) if i not in confirmed]
        if fresh and fresh[0] - now > horizon:
            return None
        if dry_run:
            return _claim_of(drive, chosen, confirmed=tuple(sorted(confirmed)))
        claim_id = uuid.uuid4().hex
        store.claim_drive(
            drive.project, chosen, claim_id, expect_sha=expect_sha, now=now
        )
        return _claim_of(
            replace(drive, claim_id=claim_id, status="claimed"),
            chosen,
            confirmed=tuple(sorted(confirmed)),
        )


def complete(store: ScheduleStore, claim_id: str) -> None:
    """The publish pass succeeded: the drive is done, its bookings stay."""
    store.finish_claim(claim_id, ok=True)


def fail(store: ScheduleStore, claim_id: str) -> None:
    """The publish pass failed: free every slot that never got a video id."""
    store.finish_claim(claim_id, ok=False)
