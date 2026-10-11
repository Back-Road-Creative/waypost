"""``report.build`` — port of the video pipeline's ``build_schedule_report`` semantics."""

from datetime import datetime

from seo_manager.scheduling import report

from .conftest import EST, at

MON = datetime(2026, 8, 10, 9, 0, tzinfo=EST)  # next tue/fri/sun fall in one week


def test_capacity_and_days_follow_cadence(store, cadence):
    r = report.build(store, cadence, now=MON)
    assert r["weekly_longform_capacity"] == 3
    assert r["designed_days"] == ["tue", "fri", "sun"]  # Python weekday order


def test_scheduled_longforms_and_queue_end(store, cadence):
    store.book("a", 1, "south", at("2026-08-11", "16:00"))
    store.book("b", 1, "north", at("2026-09-01"))
    store.book("old", 1, "south", at("2026-08-04", "16:00"))  # past: not "scheduled"
    r = report.build(store, cadence, now=MON)
    assert [(e["date"], e["region"]) for e in r["scheduled_longforms"]] == [
        ("2026-08-11", "south"),
        ("2026-09-01", "north"),
    ]
    assert r["queue_ends"] == "2026-09-01"


def test_nothing_booked(store, cadence):
    r = report.build(store, cadence, now=MON)
    assert r["queue_ends"] is None and r["scheduled_longforms"] == []


def test_next_available_matrix_marks_earliest_per_region(store, cadence):
    r = report.build(store, cadence, now=MON)
    rows = {(m["region"], m["day"]): m for m in r["next_available"]}
    assert set(rows) == {("north", "sun"), ("south", "tue"), ("south", "fri")}
    assert rows[("south", "tue")]["slot"] == at("2026-08-11", "16:00").isoformat()
    assert (
        rows[("south", "tue")]["earliest_for_region"]
        and not rows[("south", "fri")]["earliest_for_region"]
    )
    assert rows[("north", "sun")]["earliest_for_region"]
    assert rows[("south", "fri")]["shorts_windows"] == ["15:00", "18:00"]


def test_booked_slot_moves_the_matrix_forward(store, cadence):
    store.book("a", 1, "south", at("2026-08-11", "16:00"))
    rows = {
        (m["region"], m["day"]): m
        for m in report.build(store, cadence, now=MON)["next_available"]
    }
    assert rows[("south", "tue")]["slot"] == at("2026-08-18", "16:00").isoformat()


def test_superlong_sibling_row_per_region_and_not_in_matrix(store, cadence):
    r = report.build(store, cadence, now=MON)
    sl = {row["region"]: row for row in r["next_available_superlong"]}
    assert set(sl) == {"north", "south"} and sl["south"]["day"] == "sat"
    assert all(m["day"] != "sat" for m in r["next_available"])


def test_report_does_not_write(store, cadence):
    report.build(store, cadence, now=MON)
    assert store.bookings() == []
