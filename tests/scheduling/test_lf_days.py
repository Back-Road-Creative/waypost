"""Which weekdays the long-form slots use: the seed, the decided set, the chooser."""

from datetime import datetime, time

import pytest

from seo_manager.analytics import cadence_decisions as cd
from seo_manager.analytics.evidence import LongformRecord
from seo_manager.core.profile import load_cadence
from seo_manager.scheduling import lf_days, slots

from .conftest import ANCHOR, EST

NOW = datetime(2026, 10, 9, 12, 0)


def decided(db, region, dow, value=None, **kw):
    """A decision row through the public append API (the only way rows are made)."""
    fields = dict(
        knob="lf_days", region=region, value=len(dow) if value is None else value,
        prev=len(dow), status="ok", source="policy", reason_code="TAIL_UP",
        basis="test", applied_at=NOW, evidence={"dow": sorted(dow)},
    )  # fmt: skip
    fields.update(kw)
    return cd.append(db, **fields)


# --- effective_dow --------------------------------------------------------


def test_no_decision_returns_yaml_dow_as_seed(tmp_path):
    db = tmp_path / "d.sqlite3"
    got = lf_days.effective_dow(db, "south", (1, 4))
    assert (got.dow, got.source, got.applied_at, got.row_id) == ((1, 4), "seed", None, None)  # fmt: skip
    assert not db.exists()  # a read never creates the log


def test_pre_l1_row_without_dow_reads_as_seed(tmp_path):
    db = tmp_path / "d.sqlite3"
    decided(db, "south", (1, 4), evidence={"median": 5})
    got = lf_days.effective_dow(db, "south", (1, 4))
    assert got.source == "seed" and got.dow == (1, 4)


def test_decided_row_wins_over_seed(tmp_path):
    db = tmp_path / "d.sqlite3"
    row = decided(db, "south", (2, 5))
    got = lf_days.effective_dow(db, "south", (1, 4))
    assert (got.dow, got.source, got.row_id) == ((2, 5), "decision", row)
    assert got.applied_at == NOW.isoformat()


@pytest.mark.parametrize(
    "dow",
    [[1, 4, 6], [1, 1], [1, 9], [1, "x"], [1, True], "tue", None],
    ids=["length", "dupes", "range", "type", "bool", "str", "none"],
)
def test_malformed_dow_raises_naming_region_and_row(tmp_path, dow):
    db = tmp_path / "d.sqlite3"
    rid = decided(db, "south", (1, 4), evidence={"dow": dow})
    with pytest.raises(ValueError, match=rf"south.*{rid}|{rid}.*south"):
        lf_days.effective_dow(db, "south", (1, 4))


def test_overlay_outside_floor_ceiling_raises(tmp_path):
    db = tmp_path / "d.sqlite3"
    with pytest.raises(ValueError, match="south"):
        lf_days.effective_dow(db, "south", (0, 1, 2, 3))  # seed above the ceiling
    with pytest.raises(ValueError, match="south"):
        lf_days.effective_dow(db, "south", ())  # seed below the floor


def test_unreadable_store_propagates(tmp_path):
    db = tmp_path / "d.sqlite3"
    db.write_bytes(b"garbage" * 100)
    with pytest.raises(cd.DecisionStoreUnreadable):
        lf_days.effective_dow(db, "south", (1, 4))


# --- the loader overlay ---------------------------------------------------


def test_decisions_db_none_leaves_the_loader_output_unchanged(brand_yaml):
    c = load_cadence(brand_yaml, None)
    assert c == load_cadence(brand_yaml)
    assert dict(c.dow_source) == {}


def test_decided_dow_overrides_yaml_dow_via_load_cadence_overlay(
    tmp_path, brand_yaml, store
):
    db = tmp_path / "d.sqlite3"
    decided(db, "south", (2, 3))  # wed, thu in place of the yaml's tue, fri
    c = load_cadence(brand_yaml, decisions_db=db)
    assert c.region("south").dow == (2, 3)
    assert c.days("north") == frozenset({6})  # no row: the yaml seed
    assert c.dow_source["south"].source == "decision"
    assert c.dow_source["north"].source == "seed"
    # booking follows the decided weekday: the yaml cadence would have said Tuesday
    slot = slots.next_free_slot(store, c, "south", after=ANCHOR)
    assert slot.weekday() == 2 and slot.tzinfo is not None
    assert (
        slots.next_free_slot(
            store, load_cadence(brand_yaml), "south", after=ANCHOR
        ).weekday()
        == 1
    )


def test_overlay_drops_day_times_of_a_removed_day_and_defaults_a_new_one(
    tmp_path, brand_yaml
):
    db = tmp_path / "d.sqlite3"
    decided(db, "south", (1, 3))  # fri (15:00) is gone, thu is new
    c = load_cadence(brand_yaml, decisions_db=db)
    assert c.region("south").day_times == {}  # fri's 15:00 is gone
    assert c.time_for("south", 3) == time(16, 0)  # the region default
    assert c.time_for("south", 1) == time(16, 0)


def test_overlay_keeps_day_times_of_a_kept_day(tmp_path, brand_yaml):
    db = tmp_path / "d.sqlite3"
    decided(db, "south", (4,), value=1)
    c = load_cadence(brand_yaml, decisions_db=db)
    assert c.time_for("south", 4) == time(15, 0)


def test_overlay_with_a_bad_stored_row_fails_closed(tmp_path, brand_yaml):
    db = tmp_path / "d.sqlite3"
    decided(db, "south", (2, 3), evidence={"dow": [2, 3, 5]})
    with pytest.raises(ValueError):
        load_cadence(brand_yaml, decisions_db=db)


# --- choose_dow -----------------------------------------------------------


def test_up_step_picks_weekday_maximizing_min_gap_tiebreak_by_measured_weekday_median():
    # Thu (3) is two days from Tue and Sat; every other free day is one away.
    assert lf_days.choose_dow((1,), 2, taken=frozenset({5, 6})) == (1, 3)
    # From Mon alone Thu and Fri tie at three days; the stronger measured day wins.
    assert lf_days.choose_dow((0,), 2) == (0, 3)  # no measurement: lowest weekday
    assert lf_days.choose_dow((0,), 2, weekday_medians={3: 50.0, 4: 100.0}) == (0, 4)
    assert lf_days.choose_dow((0,), 2, weekday_medians={3: 100.0, 4: 50.0}) == (0, 3)
    # An unmeasured weekday ranks below any measured one.
    assert lf_days.choose_dow((0,), 2, weekday_medians={4: 1.0}) == (0, 4)


def test_up_step_never_picks_a_taken_day():
    got = lf_days.choose_dow((0,), 2, taken=frozenset({3, 4}))
    assert 3 not in got and 4 not in got and len(got) == 2
    for taken in (frozenset({6}), frozenset({1, 2, 5})):
        assert not set(lf_days.choose_dow((0,), 2, taken=taken)) & taken


def test_up_step_with_no_free_weekday_raises():
    with pytest.raises(ValueError, match="free weekday"):
        lf_days.choose_dow((0, 1), 3, taken=frozenset({2, 3, 4, 5, 6}))


def test_down_step_removes_weakest_measured_slot():
    assert lf_days.choose_dow((1, 4), 1, weekday_medians={1: 500.0, 4: 100.0}) == (1,)
    assert lf_days.choose_dow((1, 4), 1, weekday_medians={1: 100.0, 4: 500.0}) == (4,)
    # an unmeasured day is never the weakest while a measured one exists
    assert lf_days.choose_dow((1, 4), 1, weekday_medians={4: 1.0}) == (1,)


def test_down_step_with_no_measurement_keeps_the_widest_spacing():
    # Removing Fri leaves Mon, Tue adjacent; removing Mon or Tue leaves a 3-day gap.
    assert lf_days.choose_dow((0, 1, 4), 2) == (0, 4)  # tie: highest weekday goes


def test_chooser_is_deterministic_and_sorted():
    a = lf_days.choose_dow((4, 1), 3, taken=frozenset({6}))
    assert a == lf_days.choose_dow((1, 4), 3, taken=frozenset({6}))
    assert list(a) == sorted(a)


def test_target_equal_to_current_returns_current_and_a_jump_raises():
    assert lf_days.choose_dow((1, 4), 2) == (1, 4)
    for current, target in (((1, 4), 4), ((1, 4), 0), ((1,), 3)):
        with pytest.raises(ValueError):
            lf_days.choose_dow(current, target)


# --- weekday medians ------------------------------------------------------


def lfr(posted, views, region="south"):
    return LongformRecord("v", "p", region, posted, 600.0, views, 40.0)


def test_weekday_medians_are_local_settled_and_per_region():
    as_of = datetime(2026, 10, 9, 12, 0)
    recs = [
        lfr("2026-08-02T02:00:00+00:00", 100),  # Sat 22:00 New York -> weekday 5
        lfr("2026-08-09T02:00:00+00:00", 300),  # also Saturday local
        lfr("2026-08-04T16:00:00+00:00", 50),  # Tuesday
        lfr("2026-08-04T16:00:00+00:00", 9, region="north"),  # other region
        lfr("2026-10-08T16:00:00+00:00", 7777),  # not settled by as_of
    ]
    got = lf_days.lf_weekday_medians(recs, "SOUTH", EST, as_of)
    assert got == {5: 200.0, 1: 50.0}


# --- the schedule command and its report ------------------------------------


def test_schedule_report_follows_decisions_db_flag_and_env(
    tmp_path, brand_yaml, capsys, monkeypatch
):
    import json

    from seo_manager.cli import main

    db, dec = tmp_path / "s.sqlite3", tmp_path / "d.sqlite3"
    base = ["schedule", "--brand-yaml", str(brand_yaml), "--db", str(db)]
    now = ["--now", "2026-08-10T09:00:00-04:00"]
    assert main([*base, "--init", "report", *now]) == 0
    seed = json.loads(capsys.readouterr().out)
    assert seed["designed_days"] == ["tue", "fri", "sun"]
    assert seed["lf_days"]["south"]["source"] == "seed"
    decided(dec, "south", (2, 3))
    assert main([*base, "--decisions-db", str(dec), "report", *now]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["designed_days"] == ["wed", "thu", "sun"]
    assert out["lf_days"]["south"]["days"] == ["wed", "thu"]
    assert out["lf_days"]["south"]["source"] == "decision"
    assert out["lf_days"]["south"]["decided_at"] == NOW.isoformat()
    assert out["lf_days"]["north"]["source"] == "seed"
