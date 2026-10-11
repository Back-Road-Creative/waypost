"""``ScheduleStore`` — lf_bookings, ready_drives, alerts_sent."""

import os
import stat
from datetime import timedelta

import pytest

from seo_manager.scheduling.store import ScheduleStore, SlotTaken

from .conftest import ANCHOR, EST, at


def test_book_roundtrip_and_idempotent_rebook(store):
    store.book("p", 1, "north", at("2026-06-14"), video_id="vid1")
    store.book("p", 1, "north", at("2026-06-14"), video_id="vid1")  # same row
    rows = store.bookings()
    assert len(rows) == 1
    r = rows[0]
    assert (r.project, r.lf_index, r.region, r.video_id) == ("p", 1, "north", "vid1")
    assert r.slot_at == at("2026-06-14")


def test_book_refuses_a_date_another_project_holds(store):
    store.book("p", 1, "north", at("2026-06-14"))
    with pytest.raises(SlotTaken, match="2026-06-14"):
        store.book("q", 1, "north", at("2026-06-14", "11:00"))
    assert len(store.bookings()) == 1


def test_book_allows_other_region_but_not_anonymous_clash(store):
    store.book("p", 1, "north", at("2026-06-14"))
    store.book("q", 1, "south", at("2026-06-14"))
    with pytest.raises(SlotTaken):
        store.book("r", 1, None, at("2026-06-14"))
    assert [b.project for b in store.bookings("south")] == ["q"]  # region filter


def test_db_file_is_group_writable(tmp_path):
    ScheduleStore(tmp_path / "s.sqlite3", EST, ["north"]).book(
        "p", 1, None, at("2026-06-14")
    )
    mode = stat.S_IMODE(os.stat(tmp_path / "s.sqlite3").st_mode)
    assert mode & 0o060 == 0o060


def test_same_project_cannot_hold_one_date_with_two_lfs(store):
    store.book("proj", 2, "north", at("2026-06-21"))
    with pytest.raises(SlotTaken):
        store.book("proj", 3, "north", at("2026-06-21"))
    store.book("proj", 2, "north", at("2026-06-21"))  # exact row: idempotent


def test_read_creates_nothing(tmp_path):
    path = tmp_path / "sub" / "s.sqlite3"
    assert ScheduleStore(path, EST, ["north"]).bookings() == []
    assert not path.parent.exists()


def test_store_requires_an_explicit_zone(tmp_path):
    with pytest.raises(TypeError):
        ScheduleStore(tmp_path / "s.sqlite3")


def test_book_refuses_an_unknown_region(store):
    with pytest.raises(ValueError, match="mars"):
        store.book("p", 1, "mars", at("2026-06-14"))
    assert store.bookings() == []


def test_book_canonicalises_region_case_and_whitespace(store):
    store.book("p", 1, " North ", at("2026-06-14"))
    assert [b.region for b in store.bookings()] == ["north"]
    with pytest.raises(SlotTaken):
        store.book("q", 1, "NORTH", at("2026-06-14"))
    assert [b.project for b in store.bookings("North")] == ["p"]


def test_store_requires_the_known_regions(tmp_path):
    with pytest.raises(TypeError):
        ScheduleStore(tmp_path / "s.sqlite3", EST)  # type: ignore[call-arg]


def _ready(store, project="d1", region="north", lf_count=1, now=ANCHOR):
    store.register_ready(
        project,
        region,
        lf_count,
        output_dir="/o",
        state_path="/s.json",
        mirror_sha="abc",
        audit_log="/a.log",
        now=now,
    )


def test_register_ready_and_depth(store):
    _ready(store, "a")
    _ready(store, "b")
    _ready(store, "c", region="south")
    assert store.ready_depth("north") == 2 and store.ready_depth("south") == 1
    assert store.ready_depth("North ") == 2  # canonical region


def test_register_ready_is_idempotent_and_refreshes_sha(store):
    _ready(store, "a")
    store.register_ready("a", "north", 1, mirror_sha="def", now=ANCHOR)
    assert store.ready_depth("north") == 1
    d = store.drive("a")
    assert (
        d.mirror_sha == "def" and d.output_dir == "/o"
    )  # unset fields keep their value


def test_register_ready_refuses_unknown_region_status_and_count(store):
    with pytest.raises(ValueError, match="unknown region"):
        _ready(store, region="mars")
    with pytest.raises(ValueError, match="cannot register"):
        store.register_ready("a", "north", 1, status="complete")
    with pytest.raises(ValueError, match="lf_count"):
        _ready(store, lf_count=0)
    assert store.all_drives() == []


def test_register_ready_does_not_resurrect_a_claimed_drive(store):
    _ready(store, "a")
    store.claim_drive("a", [at("2026-06-14")], "c1")
    with pytest.raises(ValueError, match="claimed"):
        _ready(store, "a")


def test_a_failed_drive_can_be_registered_again_with_a_fresh_claim_slot(store):
    _ready(store, "a")
    store.claim_drive("a", [at("2026-06-14")], "c1")
    store.finish_claim("c1", ok=False)
    _ready(store, "a")
    assert store.drive("a").status == "ready" and store.drive("a").claim_id is None
    store.claim_drive("a", [at("2026-06-14")], "c2")
    assert store.drive_by_claim("c2").project == "a"


def test_set_status_validates_and_unknown_project_raises(store):
    _ready(store, "a")
    store.set_status("a", "auditing")
    assert store.ready_depth("north") == 0
    with pytest.raises(ValueError, match="unknown drive status"):
        store.set_status("a", "bogus")
    with pytest.raises(KeyError):
        store.set_status("nope", "ready")


def test_release_unconfirmed_keeps_confirmed_rows(store):
    store.book("p", 1, "north", at("2026-06-14"), video_id="v1")
    store.book("p", 2, "north", at("2026-06-21"))
    assert store.release_unconfirmed("p") == 1
    assert [b.lf_index for b in store.bookings()] == [1]


def test_reads_on_a_missing_database_create_nothing(tmp_path, cadence):
    s = ScheduleStore(tmp_path / "none.sqlite3", EST, cadence.regions)
    assert s.ready_depth("north") == 0 and s.all_drives() == [] and s.drive("x") is None
    assert not (tmp_path / "none.sqlite3").exists()


def test_alert_dedupe_within_window(store):
    assert store.mark_alert("lf_slot_empty", "south:2026-06-12", ANCHOR) is True
    assert (
        store.mark_alert(
            "lf_slot_empty", "south:2026-06-12", ANCHOR + timedelta(hours=23)
        )
        is False
    )
    assert (
        store.mark_alert(
            "lf_slot_empty", "south:2026-06-12", ANCHOR + timedelta(hours=25)
        )
        is True
    )
    assert store.mark_alert("lf_slot_empty", "north:2026-06-14", ANCHOR) is True


@pytest.mark.parametrize(
    "start,target",
    [
        ("ready", "claimed"),
        ("ready", "complete"),
        ("auditing", "claimed"),
        ("auditing", "complete"),
    ],
)
def test_set_status_refuses_to_move_into_claimed_or_complete(store, start, target):
    _ready(store, "a")
    store.set_status("a", start)
    with pytest.raises(ValueError, match="claim_drive"):
        store.set_status("a", target)
    d = store.drive("a")
    assert d.status == start and d.claim_id is None and d.claimed_at is None


@pytest.mark.parametrize("target", ["ready", "auditing", "failed", "complete"])
def test_set_status_refuses_to_leave_claimed_and_leaves_bookings(store, target):
    _ready(store, "a")
    store.claim_drive("a", [at("2026-06-14")], "c1")
    with pytest.raises(ValueError, match="finish_claim"):
        store.set_status("a", target)
    assert store.drive("a").status == "claimed"
    assert len(store.bookings()) == 1
    assert (
        store.finish_claim("c1", ok=False) == "failed"
    )  # the real exit still frees it
    assert store.bookings() == []


@pytest.mark.parametrize("target", ["ready", "auditing", "failed", "claimed"])
def test_set_status_refuses_to_leave_complete(store, target):
    _ready(store, "a")
    store.claim_drive("a", [at("2026-06-14")], "c1")
    store.finish_claim("c1", ok=True)
    with pytest.raises(ValueError, match="complete"):
        store.set_status("a", target)
    assert store.drive("a").status == "complete"


def test_set_status_allows_auditing_ready_and_failed_moves(store):
    _ready(store, "a")
    for to in ("auditing", "ready", "failed"):
        store.set_status("a", to)
        assert store.drive("a").status == to
    with pytest.raises(
        ValueError, match="register"
    ):  # failed -> ready is a re-register
        store.set_status("a", "ready")


def test_init_creates_the_schema_once_and_reads_then_work(tmp_path, cadence):
    s = ScheduleStore(tmp_path / "new.sqlite3", EST, cadence.regions)
    assert not s.path.exists()
    s.init()
    assert s.path.exists() and s.all_drives() == [] and s.bookings() == []
    s.init()  # idempotent


def test_register_ready_carries_lf_and_ingest_mode(store):
    store.register_ready(
        "m", "north", 1, lf_mode="full_drive", ingest_mode="gopro", now=ANCHOR
    )
    d = store.drive("m")
    assert (d.lf_mode, d.ingest_mode) == ("full_drive", "gopro")
    # a refresh without modes keeps what the audit registered
    store.register_ready("m", "north", 1, now=ANCHOR)
    d = store.drive("m")
    assert (d.lf_mode, d.ingest_mode) == ("full_drive", "gopro")


def test_register_ready_refuses_unknown_modes(store):
    with pytest.raises(ValueError, match="lf_mode"):
        store.register_ready("m", "north", 1, lf_mode="whole", now=ANCHOR)
    with pytest.raises(ValueError, match="ingest_mode"):
        store.register_ready("m", "north", 1, ingest_mode="vhs", now=ANCHOR)


def test_modes_column_is_added_to_an_older_database(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE lf_bookings (project TEXT NOT NULL, lf_index INTEGER NOT NULL,"
        " region TEXT, slot_at TEXT NOT NULL, video_id TEXT, booked_at TEXT NOT NULL,"
        " PRIMARY KEY (project, lf_index));"
        "CREATE TABLE ready_drives (project TEXT PRIMARY KEY, region TEXT NOT NULL,"
        " lf_count INTEGER NOT NULL, output_dir TEXT, state_path TEXT, mirror_sha TEXT,"
        " audit_log TEXT, created_at TEXT NOT NULL, status TEXT NOT NULL,"
        " claim_id TEXT UNIQUE, claimed_at TEXT);"
    )
    conn.close()
    s = ScheduleStore(path, EST, ["north", "south"])
    s.register_ready("m", "north", 1, lf_mode="full_drive", now=ANCHOR)
    assert s.drive("m").lf_mode == "full_drive"
