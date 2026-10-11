"""``claim`` -- one row, idempotent, atomic, refuses a booked slot or a stale sha; dry-run writes nothing."""

from datetime import timedelta

import pytest

from seo_manager.scheduling import claim as claims
from seo_manager.scheduling import slots
from seo_manager.scheduling.store import SlotTaken, StaleDrive

from .conftest import ANCHOR, at


def _ready(store, project="routea", region="north", lf_count=1, now=ANCHOR):
    store.register_ready(
        project,
        region,
        lf_count,
        output_dir="/o",
        state_path="/s",
        mirror_sha="abc",
        now=now,
    )


def test_claim_books_the_next_free_slot_inside_the_lead_window(store, cadence):
    _ready(store)
    now = at("2026-06-12", "09:00")  # Sun 06-14 14:00 is 53 h away
    c = claims.claim(store, cadence, "north", now=now, lead_hours=72)
    assert (
        c.project == "routea"
        and c.slots == [at("2026-06-14")]
        and not c.already_claimed
    )
    assert [b.slot_at for b in store.bookings("north")] == [at("2026-06-14")]
    assert store.drive("routea").status == "claimed"
    assert store.drive("routea").claim_id == c.claim_id


def test_claim_outside_lead_window_returns_none(store, cadence):
    _ready(store)
    assert (
        claims.claim(store, cadence, "north", now=ANCHOR, lead_hours=72) is None
    )  # 5 d away
    assert store.drive("routea").status == "ready" and store.bookings() == []


def test_claim_is_idempotent_one_row(store, cadence):
    _ready(store)
    now = at("2026-06-12", "09:00")
    first = claims.claim(store, cadence, "north", now=now, lead_hours=72)
    again = claims.claim(
        store, cadence, "north", now=now + timedelta(hours=1), lead_hours=72
    )
    assert again.claim_id == first.claim_id and again.already_claimed
    assert len(store.bookings()) == 1


def test_claim_skips_a_booked_slot(store, cadence):
    _ready(store)
    store.book("other", 1, "north", at("2026-06-14"))
    now = at("2026-06-12", "09:00")
    assert (
        claims.claim(store, cadence, "north", now=now, lead_hours=72) is None
    )  # 06-21 is 9 d out
    assert [b.project for b in store.bookings()] == ["other"]
    c = claims.claim(store, cadence, "north", now=now, lead_hours=24 * 10)
    assert c.slots == [at("2026-06-21")]


def test_multi_lf_drive_books_consecutive_cadence_slots(store, cadence):
    _ready(store, lf_count=2)
    now = at("2026-06-12", "09:00")
    c = claims.claim(store, cadence, "north", now=now, lead_hours=72)
    assert c.slots == [at("2026-06-14"), at("2026-06-21")]
    assert [(b.lf_index, b.video_id) for b in store.bookings()] == [
        (1, None),
        (2, None),
    ]


def test_dry_run_writes_nothing(store, cadence):
    _ready(store)
    now = at("2026-06-12", "09:00")
    c = claims.claim(store, cadence, "north", now=now, lead_hours=72, dry_run=True)
    assert c.slots == [at("2026-06-14")] and c.claim_id is None
    assert store.bookings() == [] and store.drive("routea").status == "ready"


def test_no_ready_drive_returns_none(store, cadence):
    assert (
        claims.claim(store, cadence, "north", now=at("2026-06-12"), lead_hours=72)
        is None
    )


def test_oldest_ready_drive_wins(store, cadence):
    _ready(store, "new", now=ANCHOR + timedelta(hours=2))
    _ready(store, "old", now=ANCHOR)
    c = claims.claim(store, cadence, "north", now=at("2026-06-12"), lead_hours=72)
    assert c.project == "old"


def test_complete_marks_done_and_keeps_bookings(store, cadence):
    _ready(store)
    c = claims.claim(store, cadence, "north", now=at("2026-06-12"), lead_hours=72)
    claims.complete(store, c.claim_id)
    claims.complete(store, c.claim_id)  # idempotent
    assert store.drive("routea").status == "complete" and len(store.bookings()) == 1


def test_fail_releases_unconfirmed_bookings_and_frees_the_slot(store, cadence):
    _ready(store)
    c = claims.claim(store, cadence, "north", now=at("2026-06-12"), lead_hours=72)
    claims.fail(store, c.claim_id)
    assert store.drive("routea").status == "failed" and store.bookings() == []
    assert slots.next_free_slot(store, cadence, "north", after=at("2026-06-12")) == at(
        "2026-06-14"
    )


def test_fail_keeps_confirmed_bookings(store, cadence):
    _ready(store, lf_count=2)
    c = claims.claim(store, cadence, "north", now=at("2026-06-12"), lead_hours=72)
    store.book("routea", 1, "north", at("2026-06-14"), video_id="vid1")  # LF1 uploaded
    claims.fail(store, c.claim_id)
    assert [(b.lf_index, b.video_id) for b in store.bookings()] == [(1, "vid1")]


def test_unknown_claim_raises(store):
    with pytest.raises(KeyError, match="nope"):
        claims.complete(store, "nope")
    with pytest.raises(KeyError, match="nope"):
        claims.fail(store, "nope")


def test_complete_after_fail_is_refused(store, cadence):
    _ready(store)
    c = claims.claim(store, cadence, "north", now=at("2026-06-12"), lead_hours=72)
    claims.fail(store, c.claim_id)
    with pytest.raises(ValueError, match="failed"):
        claims.complete(store, c.claim_id)


def test_claim_refuses_a_stale_sha_and_books_nothing(store, cadence):
    _ready(store)
    now = at("2026-06-12", "09:00")
    with pytest.raises(StaleDrive, match="abc"):
        claims.claim(store, cadence, "north", now=now, expect_sha="zzz")
    assert store.bookings() == [] and store.drive("routea").status == "ready"
    c = claims.claim(store, cadence, "north", now=now, expect_sha="abc")
    assert c.mirror_sha == "abc"


def test_open_claim_with_another_sha_is_refused_too(store, cadence):
    _ready(store)
    now = at("2026-06-12", "09:00")
    claims.claim(store, cadence, "north", now=now)
    with pytest.raises(StaleDrive):
        claims.claim(store, cadence, "north", now=now, expect_sha="zzz")


def test_open_claim_sha_refusal_names_the_claim_id(store, cadence):
    """The cron has no run for an open claim: it needs the id to fail it, not a hourly exit 2."""
    _ready(store)
    now = at("2026-06-12", "09:00")
    c = claims.claim(store, cadence, "north", now=now)
    with pytest.raises(StaleDrive, match=f"open claim {c.claim_id}"):
        claims.claim(store, cadence, "north", now=now, expect_sha="zzz")


def test_claim_drive_is_all_or_nothing_when_a_later_slot_is_taken(store):
    _ready(store, lf_count=2)
    store.book("other", 1, "north", at("2026-06-21"))
    with pytest.raises(SlotTaken, match="2026-06-21"):
        store.claim_drive("routea", [at("2026-06-14"), at("2026-06-21")], "c1")
    assert [b.project for b in store.bookings()] == ["other"]
    assert (
        store.drive("routea").status == "ready"
        and store.drive("routea").claim_id is None
    )


def test_claim_drive_refuses_a_drive_that_is_no_longer_ready(store):
    _ready(store)
    store.claim_drive("routea", [at("2026-06-14")], "c1")
    with pytest.raises(ValueError, match="claimed, not ready"):
        store.claim_drive("routea", [at("2026-06-21")], "c2")
    assert [b.slot_at for b in store.bookings()] == [at("2026-06-14")]


def test_claim_drive_refuses_wrong_slot_count_and_unknown_drive(store):
    _ready(store, lf_count=2)
    with pytest.raises(ValueError, match="needs 2"):
        store.claim_drive("routea", [at("2026-06-14")], "c1")
    with pytest.raises(KeyError):
        store.claim_drive("nope", [], "c1")


def test_a_second_claim_call_in_one_process_returns_the_open_claim(store, cadence):
    """Sequential, one process: the second drive waits behind the open claim."""
    _ready(store, "a")
    store.register_ready("b", "north", 1, now=ANCHOR + timedelta(hours=1))
    now = at("2026-06-12", "09:00")
    first = claims.claim(store, cadence, "north", now=now)
    again = claims.claim(store, cadence, "north", now=now)
    assert first.project == again.project == "a" and again.already_claimed
    claims.complete(store, first.claim_id)
    second = claims.claim(store, cadence, "north", now=now, lead_hours=24 * 10)
    assert second.project == "b" and second.slots == [at("2026-06-21")]


def test_fail_on_a_complete_claim_is_refused(store, cadence):
    _ready(store)
    c = claims.claim(store, cadence, "north", now=at("2026-06-12"), lead_hours=72)
    claims.complete(store, c.claim_id)
    with pytest.raises(ValueError, match="complete"):
        claims.fail(store, c.claim_id)


def test_unknown_region_raises(store, cadence):
    with pytest.raises(ValueError, match="unknown region"):
        claims.claim(store, cadence, "mars", now=at("2026-06-12"))


def test_reclaim_keeps_a_confirmed_booking_and_only_replans_the_rest(store, cadence):
    """claim, upload LF1, fail, re-register, claim: LF1 keeps its video id and date."""
    _ready(store, lf_count=2)
    now = at("2026-06-12", "09:00")
    c = claims.claim(store, cadence, "north", now=now, lead_hours=24 * 10)
    assert c.slots == [at("2026-06-14"), at("2026-06-21")]
    store.book("routea", 1, "north", at("2026-06-14"), video_id="vid1")
    claims.fail(store, c.claim_id)
    _ready(store, lf_count=2)  # re-registered: a failed drive is claimable again
    again = claims.claim(store, cadence, "north", now=now, lead_hours=24 * 10)
    assert again.slots == [at("2026-06-14"), at("2026-06-21")]
    assert again.confirmed_lfs == (1,)  # the cron must not re-anchor on a published LF1
    assert [(b.lf_index, b.slot_at, b.video_id) for b in store.bookings()] == [
        (1, at("2026-06-14"), "vid1"),
        (2, at("2026-06-21"), None),
    ]
    # the confirmed Sunday stays held against every other drive
    store.register_ready("other", "north", 1, now=ANCHOR + timedelta(hours=3))
    claims.complete(store, again.claim_id)
    o = claims.claim(store, cadence, "north", now=now, lead_hours=24 * 20)
    assert o.slots == [at("2026-06-28")]


def test_claim_drive_refuses_to_replace_a_confirmed_booking(store):
    _ready(store, lf_count=2)
    store.book("routea", 1, "north", at("2026-06-14"), video_id="vid1")
    with pytest.raises(ValueError, match="vid1"):
        store.claim_drive("routea", [at("2026-06-21"), at("2026-06-28")], "c1")
    assert [(b.lf_index, b.slot_at, b.video_id) for b in store.bookings()] == [
        (1, at("2026-06-14"), "vid1")
    ]
    assert store.drive("routea").status == "ready"


def test_dry_run_on_a_missing_database_creates_nothing(tmp_path, cadence):
    from seo_manager.scheduling.store import ScheduleStore

    s = ScheduleStore(tmp_path / "new" / "s.sqlite3", cadence.tz, cadence.regions)
    assert claims.claim(s, cadence, "north", now=at("2026-06-12"), dry_run=True) is None
    assert not (tmp_path / "new").exists()


def test_dry_run_leaves_an_older_database_schema_alone(tmp_path, cadence):
    import sqlite3

    from seo_manager.scheduling.store import ScheduleStore

    db = tmp_path / "old.sqlite3"
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE lf_bookings (project TEXT NOT NULL, lf_index INTEGER NOT NULL,"
        " region TEXT, slot_at TEXT NOT NULL, video_id TEXT, booked_at TEXT NOT NULL,"
        " PRIMARY KEY (project, lf_index));"
        "CREATE TABLE ready_drives (project TEXT PRIMARY KEY, region TEXT NOT NULL,"
        " lf_count INTEGER NOT NULL, output_dir TEXT, state_path TEXT, mirror_sha TEXT,"
        " audit_log TEXT, created_at TEXT NOT NULL, status TEXT NOT NULL,"
        " claim_id TEXT UNIQUE);"
        "INSERT INTO ready_drives (project, region, lf_count, created_at, status)"
        " VALUES ('routea','north',1,'2026-06-09T09:00:00-04:00','ready');"
    )
    con.close()
    s = ScheduleStore(db, cadence.tz, cadence.regions)
    now = at("2026-06-12", "09:00")
    claims.claim(s, cadence, "north", now=now, dry_run=True)

    def cols():
        return [
            r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(ready_drives)")
        ]

    assert "claimed_at" not in cols() and "alerts_sent" not in [
        r[0] for r in sqlite3.connect(db).execute("SELECT name FROM sqlite_master")
    ]
    c = claims.claim(s, cadence, "north", now=now)  # a real claim migrates and works
    assert "claimed_at" in cols() and s.drive("routea").claimed_at == now
    assert c.slots == [at("2026-06-14")]


def test_a_claim_older_than_the_lead_window_is_flagged_and_alerted_once(store, cadence):
    _ready(store)
    now = at("2026-06-12", "09:00")
    c = claims.claim(store, cadence, "north", now=now, lead_hours=72)
    assert not c.stale
    soon = claims.claim(store, cadence, "north", now=now + timedelta(hours=71))
    assert soon.already_claimed and not soon.stale
    late = now + timedelta(hours=73)
    dry = claims.claim(store, cadence, "north", now=late, dry_run=True)
    assert dry.stale  # reported, but a dry run records no alert
    assert store.mark_alert("stale_claim", c.claim_id, late) is True
    assert store.mark_alert("stale_claim", c.claim_id, late) is False  # deduped 24 h


def test_a_real_claim_call_on_a_stale_claim_sends_the_alert_once(store, cadence):
    _ready(store)
    now = at("2026-06-12", "09:00")
    c = claims.claim(store, cadence, "north", now=now, lead_hours=72)
    late = now + timedelta(hours=73)
    assert claims.claim(store, cadence, "north", now=late).stale
    assert store.mark_alert("stale_claim", c.claim_id, late) is False  # already sent


def test_a_claim_never_books_today_even_when_the_slot_is_still_ahead(store, cadence):
    """The video pipeline's drive scheduler never uses the base date: a 14:00 slot 10 minutes away is
    not a slot a multi-GB upload can meet."""
    _ready(store)
    c = claims.claim(
        store, cadence, "north", now=at("2026-06-14", "13:50"), lead_hours=24 * 10
    )
    assert c.slots == [at("2026-06-21")]
    assert [b.slot_at for b in store.bookings()] == [at("2026-06-21")]


def test_claim_drive_refuses_a_slot_on_the_day_of_the_claim(store):
    _ready(store)
    with pytest.raises(ValueError, match="claim day"):
        store.claim_drive(
            "routea", [at("2026-06-14")], "c1", now=at("2026-06-14", "13:50")
        )
    assert store.bookings() == [] and store.drive("routea").status == "ready"


def test_a_stale_sha_drive_does_not_block_the_newer_drive_at_the_right_sha(
    store, cadence
):
    store.register_ready("old", "north", 1, mirror_sha="aaa", now=ANCHOR)
    store.register_ready(
        "new", "north", 1, mirror_sha="bbb", now=ANCHOR + timedelta(hours=1)
    )
    now = at("2026-06-12", "09:00")
    c = claims.claim(store, cadence, "north", now=now, expect_sha="bbb")
    assert c.project == "new" and c.slots == [at("2026-06-14")]
    assert store.drive("old").status == "ready"  # untouched, retire it explicitly
    assert store.mark_alert("stale_sha", "old", now) is False  # already recorded


def test_a_dry_run_skips_a_stale_sha_drive_without_recording_an_alert(store, cadence):
    store.register_ready("old", "north", 1, mirror_sha="aaa", now=ANCHOR)
    store.register_ready(
        "new", "north", 1, mirror_sha="bbb", now=ANCHOR + timedelta(hours=1)
    )
    c = claims.claim(
        store, cadence, "north", now=at("2026-06-12", "09:00"), expect_sha="bbb",
        dry_run=True,
    )  # fmt: skip
    assert c.project == "new"
    assert store.mark_alert("stale_sha", "old", at("2026-06-12", "09:00")) is True


def test_only_stale_sha_drives_still_refuse(store, cadence):
    store.register_ready("old", "north", 1, mirror_sha="aaa", now=ANCHOR)
    with pytest.raises(StaleDrive):
        claims.claim(
            store, cadence, "north", now=at("2026-06-12", "09:00"), expect_sha="bbb"
        )
    assert store.mark_alert("stale_sha", "old", at("2026-06-12", "09:00")) is False


def test_dry_run_on_a_database_with_only_bookings_changes_nothing(tmp_path, cadence):
    import sqlite3

    from seo_manager.scheduling.store import ScheduleStore

    db = tmp_path / "w1.sqlite3"
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE lf_bookings (project TEXT NOT NULL, lf_index INTEGER NOT NULL,"
        " region TEXT, slot_at TEXT NOT NULL, video_id TEXT, booked_at TEXT NOT NULL,"
        " PRIMARY KEY (project, lf_index));"
    )
    con.close()
    before = db.read_bytes()
    s = ScheduleStore(db, cadence.tz, cadence.regions)
    assert claims.claim(s, cadence, "north", now=at("2026-06-12"), dry_run=True) is None
    assert s.ready_depth("north") == 0 and s.all_drives() == []
    assert db.read_bytes() == before


def _race(args):
    db, brand, region = args
    from seo_manager.core.profile import load_cadence
    from seo_manager.scheduling.store import ScheduleStore

    cad = load_cadence(brand)
    s = ScheduleStore(db, cad.tz, cad.regions)
    c = claims.claim(s, cad, region, now=at("2026-06-12", "09:00"), lead_hours=24 * 30)
    return c.claim_id if c else None


def test_parallel_processes_hold_exactly_one_claim_per_region(
    tmp_path, brand_yaml, cadence
):
    import multiprocessing

    from seo_manager.scheduling.store import ScheduleStore

    db = tmp_path / "race.sqlite3"
    s = ScheduleStore(db, cadence.tz, cadence.regions)
    for i, name in enumerate(("a", "b", "c")):
        s.register_ready(name, "north", 1, now=ANCHOR + timedelta(hours=i))
    with multiprocessing.Pool(8) as pool:
        got = pool.map(_race, [(db, brand_yaml, "north")] * 16)
    assert len(set(got)) == 1 and None not in got
    assert len(s.bookings("north")) == 1
    assert [d.status for d in s.all_drives()].count("claimed") == 1


def test_later_lf_slot_is_the_claims_own_slot(store, cadence):
    """The pipeline publishes LF n at LF1 + 7*(n-1) days; it re-asks for LF2 after LF1 and must
    land on the booking the claim made (own rows exempt), not on another cadence day."""
    _ready(store, project="us", region="south", lf_count=2)
    c = claims.claim(
        store, cadence, "south", now=at("2026-06-09", "09:00"), lead_hours=24 * 30
    )
    assert [s.date() for s in c.slots] == [
        c.slots[0].date(),
        c.slots[0].date() + timedelta(days=7),
    ]
    assert c.slots[0].weekday() == c.slots[1].weekday()
    again = slots.next_free_slot(
        store, cadence, "south", after=c.slots[0], include_today=False,
        publish_day="tue" if c.slots[0].weekday() == 1 else "fri",
        project="us", lf_index=2,
    )  # fmt: skip
    assert again == c.slots[1]


def test_claim_picks_an_lf1_whose_whole_weekly_series_is_free(store, cadence):
    """The pipeline puts LFn on LF1 + 7*(n-1) and refuses a taken date, so the claim must not
    book LF1 where LF1+7 is held by another project: it skips to a free series."""
    _ready(store, lf_count=2)
    store.book("other", 1, "north", at("2026-06-21"))  # LF1 06-14 would need 06-21
    now = at("2026-06-12", "09:00")
    c = claims.claim(store, cadence, "north", now=now, lead_hours=24 * 30)
    assert c.slots == [at("2026-06-28"), at("2026-07-05")]
    assert [(b.project, b.slot_at) for b in store.bookings()] == [
        ("other", at("2026-06-21")),
        ("routea", at("2026-06-28")),
        ("routea", at("2026-07-05")),
    ]
    for i, slot in enumerate(
        c.slots
    ):  # the pipeline's own free-date check passes for each LF
        slots.assert_free(
            store, cadence, "north", [slot.date().isoformat()],
            project="routea", lf_index={slot.date().isoformat(): i + 1},
        )  # fmt: skip


def test_reclaim_with_a_taken_series_date_is_refused_not_shifted(store, cadence):
    """LF1 is already published (confirmed); LF2's fixed date got taken meanwhile."""
    _ready(store, lf_count=2)
    now = at("2026-06-12", "09:00")
    c = claims.claim(store, cadence, "north", now=now, lead_hours=24 * 10)
    store.book("routea", 1, "north", at("2026-06-14"), video_id="vid1")
    claims.fail(store, c.claim_id)
    store.book("other", 1, "north", at("2026-06-21"))
    _ready(store, lf_count=2)
    with pytest.raises(SlotTaken):
        claims.claim(store, cadence, "north", now=now, lead_hours=24 * 10)


def test_a_first_claim_and_an_open_claim_report_the_confirmed_lfs(store, cadence):
    _ready(store, lf_count=2)
    now = at("2026-06-12", "09:00")
    c = claims.claim(store, cadence, "north", now=now, lead_hours=24 * 10)
    assert c.confirmed_lfs == ()
    store.book("routea", 1, "north", at("2026-06-14"), video_id="vid1")
    open_again = claims.claim(store, cadence, "north", now=now)
    assert open_again.already_claimed and open_again.confirmed_lfs == (1,)


def test_the_claim_series_offsets_come_from_the_one_series_rule():
    assert claims._series_offset(1) == timedelta(0)
    assert (
        claims._series_offset(3)
        == slots.lf_series_dates(ANCHOR.date(), 3)[-1] - ANCHOR.date()
    )
