"""``slots`` — parity with the video pipeline's ``next_free_slot``, its weekday check and
its ``_assert_lf_dates_free`` guard."""

from datetime import datetime, timedelta

import pytest

from seo_manager.core.profile import load_cadence
from seo_manager.scheduling import slots

from .conftest import ANCHOR, BRAND_YAML, EST, at
from .test_cadence import _write


def nfs(store, cad, region, **kw):
    kw.setdefault("after", ANCHOR)
    return slots.next_free_slot(store, cad, region, **kw)


def test_north_sunday_cadence_from_config(store, cadence):
    assert nfs(store, cadence, "north") == at("2026-06-14")


@pytest.mark.parametrize(
    "region,hhmm,expected",
    [
        ("north", "14:00", "2026-06-21"),  # occupied -> next week
        ("north", "11:00", "2026-06-21"),  # date-granular, not time-keyed
        (None, "14:00", "2026-06-21"),  # anonymous booking blocks every region
        ("south", "14:00", "2026-06-14"),  # another region does not block ...
    ],
)
def test_occupancy_rules(store, cadence, region, hhmm, expected):
    store.book("booked", 1, region, at("2026-06-14", hhmm))
    assert nfs(store, cadence, "north") == at(expected)


def test_cross_region_flag_blocks_other_region(store, cadence):
    store.book("booked", 1, "south", at("2026-06-14"))
    blocked = nfs(store, cadence, "north", block_cross_region=True)
    assert blocked == at("2026-06-21")


def test_region_none_caller_gets_the_conservative_view(store, cadence):
    store.book("booked", 1, "north", at("2026-06-14"))
    assert nfs(store, cadence, None, publish_day="sun") == at("2026-06-21")


def test_publish_day_narrows_candidate_days(store, cadence):
    slot = nfs(store, cadence, "south", publish_day="fri")
    assert slot == at("2026-06-12", "15:00")  # Friday uses its own designed time


def test_per_day_designed_time_is_validated_not_patched(store, cadence):
    # Tue 09:00 anchor, SOUTH Tue slot is 16:00 the same day -> still ahead of `after`.
    assert nfs(store, cadence, "south") == at("2026-06-09", "16:00")


def test_include_today_false_skips_today(store, cadence):
    slot = nfs(store, cadence, "south", include_today=False)
    assert slot == at("2026-06-12", "15:00")


def test_slot_time_override_and_min_lead(store, cadence):
    from datetime import time

    s = nfs(store, cadence, "south", slot_time=time(8, 0))
    assert s == at("2026-06-12", "08:00")  # 08:00 today is before `after`
    lead = nfs(store, cadence, "south", min_lead=timedelta(hours=8))
    assert lead == at("2026-06-12", "15:00")  # 16:00 today is only 7h away


def test_any_day_searches_every_weekday(store, cadence):
    s = nfs(store, cadence, "north", any_day=True)
    assert s == at("2026-06-09", "14:00")


def test_horizon_unknown_region_and_bad_day_raise(store, cadence):
    with pytest.raises(RuntimeError, match="no free slot"):
        nfs(store, cadence, "north", horizon_days=3)
    with pytest.raises(ValueError, match="mars"):
        nfs(store, cadence, "mars")
    with pytest.raises(ValueError, match="invalid weekday"):
        nfs(store, cadence, "south", publish_day="funday")


class TestValidateAnchor:
    def test_weekday_in_region_days_is_valid(self, cadence):
        assert slots.validate_anchor(cadence, "north", ["2026-06-14"]) == []

    def test_off_cadence_anchor_is_rejected(self, cadence):
        # A Tuesday date persisted while the run's configured day was Friday.
        msgs = slots.validate_anchor(cadence, "north", ["2026-06-09"])
        assert msgs == [
            "lf_slot dates fall outside LONG_FORM_DAYS=[6]: 2026-06-09 (weekday=1)"
        ]

    def test_unparseable_date_is_reported(self, cadence):
        msgs = slots.validate_anchor(cadence, "south", ["nope"])
        assert "nope (unparseable)" in msgs[0]

    def test_explicit_day_set_overrides_region_days(self, cadence):
        assert (
            slots.validate_anchor(cadence, "south", ["2026-10-09"], days=frozenset({4}))
            == []
        )


class TestAssertFree:
    def test_free_dates_pass(self, store, cadence):
        slots.assert_free(store, cadence, "north", ["2026-06-14", "2026-06-21"])

    def test_booked_date_raises_and_names_it(self, store, cadence):
        store.book("other", 1, "north", at("2026-06-21"))
        with pytest.raises(slots.SlotTaken, match="2026-06-21"):
            slots.assert_free(store, cadence, "north", ["2026-06-14", "2026-06-21"])

    def test_other_region_does_not_block(self, store, cadence):
        store.book("other", 1, "south", at("2026-06-21"))
        slots.assert_free(store, cadence, "north", ["2026-06-21"])

    @pytest.mark.parametrize("region", ["North", " NORTH ", "north"])
    def test_region_case_cannot_slip_past_a_booking(self, store, cadence, region):
        store.book("other", 1, "north", at("2026-06-21"))
        with pytest.raises(slots.SlotTaken, match="2026-06-21"):
            slots.assert_free(store, cadence, region, ["2026-06-21"])

    def test_unknown_region_raises(self, store, cadence):
        with pytest.raises(ValueError, match="mars"):
            slots.assert_free(store, cadence, "mars", ["2026-06-21"])


def test_naive_after_is_treated_as_cadence_timezone(store, cadence):
    naive = datetime(2026, 6, 9, 9, 0)
    assert nfs(store, cadence, "north", after=naive) == at("2026-06-14")
    assert at("2026-06-14").tzinfo == EST


class TestOwnProject:
    """A project's own bookings never count against it (same rule as ``store.book``)."""

    def test_assert_free_ignores_own_project(self, store, cadence):
        store.book("p", 2, "north", at("2026-06-21"))
        slots.assert_free(store, cadence, "north", ["2026-06-21"], project="p")
        with pytest.raises(slots.SlotTaken):
            slots.assert_free(store, cadence, "north", ["2026-06-21"], project="q")
        with pytest.raises(slots.SlotTaken):
            slots.assert_free(store, cadence, "north", ["2026-06-21"])

    def test_next_free_slot_ignores_own_project(self, store, cadence):
        store.book("p", 1, "north", at("2026-06-14"))
        assert nfs(store, cadence, "north", project="p") == at("2026-06-14")
        assert nfs(store, cadence, "north", project="q") == at("2026-06-21")

    def test_lf_index_keys_the_exemption_like_book(self, store, cadence):
        store.book("p", 1, "north", at("2026-06-14"))
        nxt = nfs(store, cadence, "north", project="p", lf_index=2)
        assert nxt == at("2026-06-21")
        store.book("p", 2, "north", nxt)
        assert nfs(store, cadence, "north", project="p", lf_index=1) == at("2026-06-14")
        free = ["2026-06-14"]
        args = (store, cadence, "north", free)
        slots.assert_free(*args, project="p", lf_index={free[0]: 1})
        with pytest.raises(slots.SlotTaken):
            slots.assert_free(*args, project="p", lf_index={free[0]: 2})


@pytest.mark.parametrize("bad", ["sunburn", "tue fri"])
def test_publish_day_is_matched_whole(store, cadence, bad):
    with pytest.raises(ValueError):
        nfs(store, cadence, "north", publish_day=bad)


def test_dublin_midnight_agrees_across_store_and_slots(tmp_path, store):
    from zoneinfo import ZoneInfo

    from seo_manager.scheduling.store import ScheduleStore

    yml = _write(tmp_path, BRAND_YAML + "    timezone: Europe/Dublin\n")
    cad, dub = load_cadence(yml), ZoneInfo("Europe/Dublin")
    with pytest.raises(ValueError, match="zone"):  # EST store vs Dublin cadence
        nfs(store, cad, "north")
    st = ScheduleStore(tmp_path / "d.sqlite3", cad.tz, cad.regions)
    st.book("o", 1, "north", datetime(2026, 6, 22, 0, 30, tzinfo=dub))
    with pytest.raises(slots.SlotTaken):
        slots.assert_free(st, cad, "north", ["2026-06-22"])
    with pytest.raises(slots.SlotTaken):
        st.book("q", 1, "north", datetime(2026, 6, 22, 10, 0, tzinfo=dub))


def test_booking_in_another_zone_counts_on_the_cadence_date(store, cadence):
    from zoneinfo import ZoneInfo

    # 2026-06-15 02:00 UTC is still Sunday 06-14 22:00 in New York.
    store.book("o", 1, "north", datetime(2026, 6, 15, 2, 0, tzinfo=ZoneInfo("UTC")))
    assert nfs(store, cadence, "north") == at("2026-06-21")


class TestSuperlong:
    def test_default_day_is_saturday(self, store, cadence):
        assert (
            slots.next_free_superlong_slot(
                store, cadence, "south", after=ANCHOR
            ).weekday()
            == 5
        )

    def test_same_region_booking_pushes_a_week(self, store, cadence):
        store.book("lf", 1, "south", at("2026-06-13"))  # a Saturday
        s = slots.next_free_superlong_slot(store, cadence, "south", after=ANCHOR)
        assert s.date().isoformat() == "2026-06-20"

    def test_cross_region_blocks_by_default(self, store, cadence):
        store.book("lf", 1, "north", at("2026-06-13"))
        s = slots.next_free_superlong_slot(store, cadence, "south", after=ANCHOR)
        assert s.date().isoformat() == "2026-06-20"

    def test_min_lead_six_hours(self, store, cadence):
        sat = at("2026-06-13", "16:00")
        s = slots.next_free_superlong_slot(
            store, cadence, "south", after=sat - timedelta(hours=5)
        )
        assert s.date().isoformat() == "2026-06-20"

    def test_no_superlong_day_searches_any_day(self, store, cadence):
        object.__setattr__(cadence, "superlong_day", None)
        s = slots.next_free_superlong_slot(store, cadence, "south", after=ANCHOR)
        assert s > ANCHOR + timedelta(hours=6)


# -- the LF series rule: LF n = LF1 + 7 * (n - 1), same weekday -------------------------


def test_lf_series_dates_is_weekly_from_lf1():
    from datetime import date

    got = slots.lf_series_dates(date(2026, 10, 9), 3)
    assert got == [date(2026, 10, 9), date(2026, 10, 16), date(2026, 10, 23)]
    assert {d.weekday() for d in got} == {date(2026, 10, 9).weekday()}


def test_lf_series_dates_accepts_iso_text_and_zero_count():
    from datetime import date

    assert slots.lf_series_dates("2026-10-13", 2) == [
        date(2026, 10, 13),
        date(2026, 10, 20),
    ]
    assert slots.lf_series_dates("2026-10-13", 0) == []


def test_lf_series_dates_refuses_a_negative_count_and_bad_text():
    with pytest.raises(ValueError):
        slots.lf_series_dates("2026-10-13", -1)
    with pytest.raises(ValueError):
        slots.lf_series_dates("not-a-date", 2)


def test_lf_series_interval_is_seven_days():
    assert slots.LF_PUBLISH_INTERVAL_DAYS == 7


def test_anchor_problems_are_structured(cadence):
    got = slots.anchor_problems(
        cadence, None, ["2026-10-13", "nope", "2026-10-16"], days=frozenset({4})
    )
    assert got == [("2026-10-13", 1), ("nope", None)]
    assert (
        slots.anchor_problems(cadence, None, ["2026-10-16"], days=frozenset({4})) == []
    )
