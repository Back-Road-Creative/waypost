"""Which hours a region's shorts post at: ranking, candidates, the swap rule, the gates."""

import random
from datetime import datetime, timedelta

import pytest

from seo_manager.analytics import volume_controller as vc
from seo_manager.analytics import volume_policy as policy
from seo_manager.analytics.volume_controller import (
    Evidence,
    KnobState,
    ShortOutcome,
    decide,
)
from seo_manager.scheduling import shorts_times as st

NOW = datetime(2026, 10, 9, 12, 0)
N = policy.PROBATION_MIN_N


def at(hour, views, start=None, weekday=0, region="south"):
    """One settled short per entry of ``views`` posted at local ``hour``."""
    start = start or NOW - timedelta(days=60)
    return [
        ShortOutcome(
            f"{region}-{hour}-{weekday}-{start:%m%d}-{i}",
            start + timedelta(hours=6 * i),
            region,
            weekday,
            hour,
            float(v),
            "analytics_daily",
        )
        for i, v in enumerate(views)
    ]


def spread(base, n=N):
    return [base + i for i in range(n)]


def ev(rows, status="ok", fenced=None):
    return Evidence(list(rows), status, NOW, None, {}, fenced or {})


def times_state(value=("10:00", "15:00"), **kw):
    kw.setdefault("last_validated", list(value))
    return KnobState("shorts_times", "south", list(value), **kw)


def one(decisions):
    (d,) = [d for d in decisions if d.knob == "shorts_times"]
    return d


def rows_10_15_12():
    # weakest current hour 10, healthy current 15, strong candidate 12
    return at(10, spread(100)) + at(15, spread(2000)) + at(12, spread(5000))


# --- ranking -------------------------------------------------------------


def test_ranks_candidate_windows_by_weekday_hour_day7_means():
    rows = (
        at(10, [100] * N, weekday=0)
        + at(10, [300] * N, weekday=1)  # hour 10: cell means 100, 300 -> 200
        + at(12, [1000] * N, weekday=2)
        + at(9, [9999] * (N - 1))  # one short under the floor: not measured
    )
    got = st.rank_hours(rows)
    assert [(r.hour, r.n, r.score) for r in got] == [
        (12, N, 1000.0),
        (10, 2 * N, 200.0),
    ]


def test_weekday_mix_does_not_bias_an_hour():
    lopsided = at(10, [100] * 30, weekday=0) + at(10, [300] * N, weekday=1)
    even = at(11, [200] * N, weekday=0) + at(11, [200] * N, weekday=1)
    scores = {r.hour: r.score for r in st.rank_hours(lopsided + even)}
    assert scores[10] == scores[11] == 200.0


def test_ties_rank_the_lower_hour_first():
    rows = at(14, [500] * N) + at(11, [500] * N)
    assert [r.hour for r in st.rank_hours(rows)] == [11, 14]


def test_candidate_hours_are_the_rail_minus_the_fence():
    got = st.candidate_hours({9, 14})
    assert got == [h for h in range(8, 19) if h not in (9, 14)]
    assert (policy.SHORTS_HOUR_FLOOR, policy.SHORTS_HOUR_CEILING) == (8, 18)


# --- the swap rule ---------------------------------------------------------


def test_swaps_weakest_current_for_best_measured_candidate():
    d = one(decide(ev(rows_10_15_12()), [times_state(last_validated=None)], NOW))
    assert (d.action, d.reason_code) == (vc.STEP, vc.HOUR_SWAP)
    assert d.value == ["12:00", "15:00"] and d.prev == ["10:00", "15:00"]
    assert d.state_patch["probation_open_since"] == NOW
    assert d.state_patch["last_validated"] == ["10:00", "15:00"]
    assert [r["hour"] for r in d.evidence_json["ranked"]] == [12, 15, 10]


def test_never_emits_hour_outside_candidate_set():
    # 20:00 is above the rail, 11:00 is fenced: both are the best by far.
    rows = rows_10_15_12() + at(20, spread(90000)) + at(11, spread(80000))
    d = one(decide(ev(rows, fenced={"south": [11]}), [times_state()], NOW))
    assert d.value == ["12:00", "15:00"]
    # only an out-of-set hour is measured: nothing to swap to, so it holds
    only_bad = at(10, spread(100)) + at(15, spread(2000)) + at(20, spread(90000))
    d = one(decide(ev(only_bad), [times_state()], NOW))
    assert (d.action, d.value) == (vc.HOLD, ["10:00", "15:00"])


def test_property_every_emitted_hour_is_current_or_in_the_candidate_set():
    rng = random.Random(7)
    fenced = {9, 13}
    allowed = set(st.candidate_hours(fenced))
    for _ in range(40):
        rows = []
        for h in range(6, 21):
            if rng.random() < 0.7:
                rows += at(h, [rng.randint(1, 9000) for _ in range(N)])
        d = one(
            decide(ev(rows, fenced={"south": sorted(fenced)}), [times_state()], NOW)
        )
        hours = {int(w[:2]) for w in d.value}
        assert hours <= allowed | {10, 15}
        assert len(d.value) == 2 and d.value == sorted(d.value)


def test_off_the_hour_seed_window_is_never_swapped():
    s = times_state(("09:30", "15:00"))
    rows = at(9, spread(1)) + at(15, spread(2000)) + at(12, spread(5000))
    d = one(decide(ev(rows), [s], NOW))
    assert d.action == vc.STEP and d.value == ["09:30", "12:00"]


def test_deadband_when_the_candidate_is_not_clearly_better():
    rows = at(10, spread(1000)) + at(15, spread(2000)) + at(12, spread(1004))
    d = one(decide(ev(rows), [times_state()], NOW))
    assert (d.action, d.reason_code) == (vc.HOLD, vc.IN_DEADBAND)


def test_thin_data_holds_with_reason():
    rows = at(10, spread(100)[:-1]) + at(15, spread(2000)) + at(12, spread(5000))
    d = one(decide(ev(rows), [times_state(("10:00",))], NOW))
    assert (d.action, d.reason_code) == (vc.HOLD, vc.INSUFFICIENT_EVIDENCE)
    assert (d.evidence_n, d.need_n) == (N - 1, N)
    rows = at(10, spread(100)) + at(12, spread(5000)[:-1])  # no measured candidate
    d = one(decide(ev(rows), [times_state(("10:00",))], NOW))
    assert (d.reason_code, d.evidence_n, d.need_n) == (
        vc.INSUFFICIENT_EVIDENCE,
        N - 1,
        N,
    )


def test_stats_refusal_becomes_the_hold_reason():
    rows = at(10, [100] * N) + at(15, spread(2000)) + at(12, spread(5000))
    d = one(decide(ev(rows), [times_state()], NOW))
    assert d.action == vc.HOLD and d.reason_code == "NO_BASELINE_VARIATION"


def test_no_region_or_non_list_value_keeps_the_placeholder_hold():
    for s in (
        KnobState("shorts_times", None, ["10:00"]),
        KnobState("shorts_times", "south", None),
    ):
        d = one(decide(ev(rows_10_15_12()), [s], NOW))
        assert (d.action, d.reason_code) == (vc.HOLD, vc.INSUFFICIENT_EVIDENCE)


def test_malformed_list_is_state_unreadable():
    for bad in (["10:00", "10:00"], ["25:00"], ["ten"]):
        d = one(decide(ev([]), [times_state(bad)], NOW))
        assert d.reason_code == vc.STATE_UNREADABLE


# --- the shared gates, list-safe ---------------------------------------------


def test_cooldown_and_lockout_hold_a_list_knob():
    s = times_state(last_change_at=NOW - timedelta(days=3))
    assert one(decide(ev(rows_10_15_12()), [s], NOW)).reason_code == vc.COOLDOWN
    s = times_state(lockout_until=NOW + timedelta(days=3))
    assert one(decide(ev(rows_10_15_12()), [s], NOW)).reason_code == vc.ROLLBACK_LOCKOUT


def probation_rows(post_views):
    change = NOW - timedelta(days=20)
    base = at(12, spread(1000), start=change - timedelta(days=30))
    return base + at(12, post_views, start=change + timedelta(hours=1))


def probation_state():
    return times_state(
        ("12:00", "15:00"),
        last_validated=["10:00", "15:00"],
        last_change_at=NOW - timedelta(days=20),
        probation_open_since=NOW - timedelta(days=20),
    )


def test_failed_probation_restores_last_validated_wholesale():
    d = one(decide(ev(probation_rows([5.0] * N)), [probation_state()], NOW))
    assert (d.action, d.reason_code) == (vc.ROLLBACK, "PROBATION_FAILED")
    assert d.value == ["10:00", "15:00"]
    assert (
        d.state_patch["rollback_count"] == 1 and "learned_ceiling" not in d.state_patch
    )


def test_passed_probation_validates_the_new_list():
    d = one(decide(ev(probation_rows(spread(1000))), [probation_state()], NOW))
    assert d.action == vc.VALIDATED
    assert d.state_patch["last_validated"] == ["12:00", "15:00"]


def test_blind_analytics_reverts_to_last_validated():
    s = probation_state()
    d = one(decide(ev([], status="scope_missing"), [s], NOW))
    assert (d.action, d.value) == (vc.BLIND_REVERT, ["10:00", "15:00"])


def test_swap_then_apply_decision_chains():
    s = times_state()
    d = one(decide(ev(rows_10_15_12()), [s], NOW))
    s2 = vc.apply_decision(s, d)
    assert s2.value == ["12:00", "15:00"] and s2.probation_open_since == NOW


# --- the coupling invariant ----------------------------------------------------


def test_rejects_set_that_violates_coupling_invariant():
    st.check_coupling(7, ["09:00"])
    with pytest.raises(ValueError, match="coupling"):
        st.check_coupling(8, ["09:00"])
    st.check_coupling(10, ["09:00", "13:00"])
    for bad in (
        ["09:00", "09:00"],
        ["9:00"],
        ["24:00"],
        ["09:61"],
        ["09-00"],
        "09:00",
        [900],
    ):
        with pytest.raises(ValueError):
            st.check_coupling(7, bad)


# --- read-back ---------------------------------------------------------------


def _row(db, region, value, **kw):
    from seo_manager.analytics import cadence_decisions as cd

    fields = dict(
        knob="shorts_times", region=region, value=value, prev=value, status="ok",
        source="policy", reason_code="HOUR_SWAP", basis="t", applied_at=NOW,
    )  # fmt: skip
    return cd.append(db, **{**fields, **kw})


def test_effective_times_seed_then_decision(tmp_path):
    db = tmp_path / "d.sqlite3"
    got = st.effective_times(db, "south", ["18:00", "15:00"])
    assert (got.times, got.source, got.row_id) == (("15:00", "18:00"), "seed", None)
    assert not db.exists()  # a read never creates the log
    rid = _row(db, "south", ["12:00", "18:00"])
    got = st.effective_times(db, "south", ["15:00", "18:00"])
    assert (got.times, got.source, got.row_id) == (("12:00", "18:00"), "decision", rid)
    assert st.effective_times(db, "north", ["18:00"]).source == "seed"


@pytest.mark.parametrize("bad", [["9am"], ["10:00", "10:00"], "10:00", ["25:00"]])
def test_effective_times_malformed_row_fails_closed(tmp_path, monkeypatch, bad):
    from seo_manager.analytics import cadence_decisions as cd

    db = tmp_path / "d.sqlite3"
    monkeypatch.setattr(cd, "_check_coupling", lambda *a: None)
    monkeypatch.setattr(cd, "_check_bounds", lambda *a: None)
    rid = _row(db, "south", bad)
    with pytest.raises(ValueError, match=rf"south.*row {rid}"):
        st.effective_times(db, "south", ["15:00"])


def test_fence_is_the_other_regions_hours_only():
    assert st.fence_for("south", {"south": [10, 15], "north": [18], "uk": [9]}) == {
        18,
        9,
    }


# --- one LF's drive takes a share of the region's hours -------------------------


def test_drive_share_splits_region_windows_over_lf_days():
    south = ["18:00", "10:00", "16:00", "14:00"]
    # two LF days: every second window; the later LF day starts at the earliest hour
    assert st.drive_share(south, [1, 4], 1) == ["14:00", "18:00"]
    assert st.drive_share(south, [4, 1], 4) == ["10:00", "16:00"]
    # one LF day: the whole list, sorted
    assert st.drive_share(["12:00", "08:00"], [6], 6) == ["08:00", "12:00"]
    # a day off the cadence (or none) takes the first LF day's share
    assert st.drive_share(south, [1, 4], 5) == ["14:00", "18:00"]
    assert st.drive_share(south, [1, 4], None) == ["14:00", "18:00"]
    # an uneven split: every drive gets the same count, the spare hour stays unshared
    shares = [st.drive_share(south, [0, 2, 4], d) for d in (0, 2, 4)]
    assert shares == [["16:00"], ["14:00"], ["10:00"]]
    assert st.share_size(4, 3) == 1 and st.share_size(2, 3) == 0
    assert st.share_size(4, 0) == 4


def test_coupling_counts_one_drives_share():
    four = ["10:00", "14:00", "16:00", "18:00"]
    st.check_coupling(14, four, lf_days=2)
    with pytest.raises(ValueError, match="coupling"):
        st.check_coupling(15, four, lf_days=2)
    st.check_coupling(7, four, lf_days=3)
    with pytest.raises(ValueError, match="coupling"):
        st.check_coupling(8, four, lf_days=3)
    with pytest.raises(ValueError, match="coupling"):
        st.check_coupling(7, ["08:00", "12:00"], lf_days=3)  # no window per drive
