"""``ScheduleStore.reopen_for_replace`` -- reopen a complete drive to replace unpublished LFs."""

import sqlite3
from datetime import datetime

import pytest

from seo_manager.scheduling import claim as claims
from seo_manager.scheduling.store import ScheduleStore

from .conftest import EST, at

NOW = at("2026-06-10", "09:00")
SLOTS = {1: at("2026-06-07"), 2: at("2026-06-14"), 3: at("2026-06-21")}
IDS = {1: "v1", 2: "v2", 3: "v3"}


def _complete(store, project="drv", region="north", lf=3):
    store.register_ready(project, region, lf, output_dir="/old", now=NOW)
    for i in range(1, lf + 1):
        store.book(project, i, region, SLOTS[i], video_id=IDS[i], booked_at=NOW)
    store.retire_published(
        project, {i: (IDS[i], SLOTS[i]) for i in range(1, lf + 1)}, now=NOW
    )


def _reopen(store, project="drv", **kw):
    args = dict(
        expected=dict(IDS),
        replace={2, 3},
        plan_digest="d1",
        reason="re-render",
        output_dir="/new",
        now=NOW,
    )
    args.update(kw)
    return store.reopen_for_replace(project, **args)


def _rows(store):
    with sqlite3.connect(store.path) as c:
        return c.execute("SELECT * FROM lf_bookings ORDER BY 1,2").fetchall()


def _reopens(store):
    with sqlite3.connect(store.path) as c:
        return c.execute("SELECT * FROM drive_reopens").fetchall()


def test_success_reopens_without_touching_bookings(store):
    _complete(store)
    before = _rows(store)
    assert _reopen(store) is None
    d = store.drive("drv")
    assert (d.status, d.claim_id, d.claimed_at, d.output_dir, d.lf_count) == (
        "auditing",
        None,
        None,
        "/new",
        3,
    )
    assert _rows(store) == before
    (r,) = _reopens(store)
    assert r[0] == "drv" and r[2] == "d1" and r[3] == "re-render"
    assert r[5] == '{"1": "v1", "2": "v2", "3": "v3"}'


def test_prior_claim_id_is_recorded(store, cadence):
    store.register_ready("drv", "north", 1, now=NOW)
    c = claims.claim(store, cadence, "north", now=at("2026-06-12", "09:00"))
    store.book("drv", 1, "north", c.slots[0], video_id="v1")
    claims.complete(store, c.claim_id)
    assert (
        _reopen(store, expected={1: "v1"}, replace={1}, now=at("2026-06-12", "09:00"))
        is None
    )
    assert _reopens(store)[0][4] == c.claim_id


@pytest.mark.parametrize("status", ["auditing", "ready", "claimed", "failed"])
def test_refuses_every_non_complete_status(store, status):
    store.register_ready("drv", "north", 3, now=NOW, status="auditing")
    for i in (1, 2, 3):
        store.book("drv", i, "north", SLOTS[i], video_id=IDS[i])
    with store._conn(write=True) as c:
        c.execute(
            "UPDATE ready_drives SET status=?, claim_id=? WHERE project='drv'",
            (status, "cl" if status == "claimed" else None),
        )
    before = _rows(store)
    msg = _reopen(store)
    assert msg and status in msg
    assert store.drive("drv").status == status
    assert _rows(store) == before and _reopens(store) == []


def test_unknown_project_refused(store):
    store.init()
    assert _reopen(store, project="nope")


def test_expected_mismatch_refused(store):
    _complete(store)
    assert _reopen(store, expected={1: "v1", 2: "vX", 3: "v3"})
    assert _reopen(store, expected={1: "v1", 2: "v2"})
    assert store.drive("drv").status == "complete" and _reopens(store) == []


def test_missing_booking_refused(store):
    _complete(store)
    with store._conn(write=True) as c:
        c.execute("DELETE FROM lf_bookings WHERE lf_index = 3")
    assert _reopen(store, expected={1: "v1", 2: "v2"})
    assert store.drive("drv").status == "complete"


@pytest.mark.parametrize("vid", [None, ""])
def test_booking_without_id_refused(store, vid):
    _complete(store)
    with store._conn(write=True) as c:
        c.execute("UPDATE lf_bookings SET video_id=? WHERE lf_index = 3", (vid,))
    assert _reopen(store, expected={1: "v1", 2: "v2", 3: vid or ""})
    assert store.drive("drv").status == "complete" and _reopens(store) == []


def test_extra_booking_refused(store):
    _complete(store)
    store.book("drv", 4, "north", at("2026-06-28"), video_id="v4")
    assert _reopen(store, expected={**IDS, 4: "v4"})


def test_replace_must_be_a_nonempty_booked_subset(store):
    _complete(store)
    assert _reopen(store, replace=set())
    assert _reopen(store, replace={2, 9})
    assert _reopens(store) == []


def test_slot_inside_margin_refused(store):
    _complete(store)
    now = at("2026-06-14", "09:00")  # LF2 is 5 h away; margin 6 h
    assert _reopen(store, now=now)
    assert _reopen(store, now=now, margin_h=4.0, replace={3}) is None


def test_lf1_inside_margin_does_not_matter_when_not_replaced(store):
    _complete(store)
    assert _reopen(store, now=at("2026-06-08")) is None  # LF1 slot is in the past


@pytest.mark.parametrize("kw", [{"reason": ""}, {"plan_digest": ""}])
def test_empty_reason_or_digest_refused(store, kw):
    _complete(store)
    assert _reopen(store, **kw)
    assert store.drive("drv").status == "complete" and _reopens(store) == []


def test_naive_now_refused(store):
    _complete(store)
    assert _reopen(store, now=datetime(2026, 6, 10, 9, 0))
    assert store.drive("drv").status == "complete"


def test_dry_run_writes_nothing(store):
    _complete(store)
    before = _rows(store)
    assert _reopen(store, dry_run=True) is None
    d = store.drive("drv")
    assert d.status == "complete" and d.output_dir == "/old"
    assert _rows(store) == before and _reopens(store) == []
    assert _reopen(store, reason="", dry_run=True)


def test_dry_run_on_missing_database_creates_nothing(tmp_path, cadence):
    s = ScheduleStore(tmp_path / "x" / "s.sqlite3", EST, cadence.regions)
    assert _reopen(s, dry_run=True)
    assert not (tmp_path / "x").exists()


def test_claim_after_reopen_returns_same_series_and_books_nothing_new(store, cadence):
    _complete(store)
    store.register_ready("other", "north", 1, now=NOW)
    store.book("other", 1, "north", at("2026-07-05"), video_id="o1")
    other = [r for r in _rows(store) if r[0] == "other"]
    other_drive = store.drive("other")
    assert _reopen(store) is None
    before = _rows(store)
    store.set_status("drv", "ready")
    c = claims.claim(store, cadence, "north", now=NOW, lead_hours=72 * 10)
    assert c.project == "drv"
    assert c.slots == [SLOTS[1], SLOTS[2], SLOTS[3]]
    assert c.confirmed_lfs == (1, 2, 3)
    assert _rows(store) == before
    assert [r for r in _rows(store) if r[0] == "other"] == other
    assert store.drive("other") == other_drive


def test_failed_claim_after_reopen_frees_nothing(store, cadence):
    _complete(store)
    assert _reopen(store) is None
    before = _rows(store)
    store.set_status("drv", "ready")
    c = claims.claim(store, cadence, "north", now=NOW, lead_hours=72 * 10)
    assert store.finish_claim(c.claim_id, ok=False) == "failed"
    assert _rows(store) == before


def test_book_replaces_the_id_in_place(store):
    _complete(store)
    assert _reopen(store) is None
    store.book("drv", 2, "north", SLOTS[2], video_id="v2-new", booked_at=NOW)
    rows = {r[1]: r for r in _rows(store)}
    assert len(rows) == 3
    assert rows[2][4] == "v2-new" and rows[2][3] == SLOTS[2].isoformat()
    assert rows[1][4] == "v1" and rows[3][4] == "v3"
