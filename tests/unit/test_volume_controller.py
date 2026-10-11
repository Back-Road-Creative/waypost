"""The pure volume and cadence controller."""

import ast
import random
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from seo_manager.analytics import volume_controller as vc
from seo_manager.analytics import volume_policy as policy
from seo_manager.analytics.anomaly_stats import Refusal, min_references_for
from seo_manager.analytics.volume_controller import (
    Decision,
    Evidence,
    KnobState,
    ShortOutcome,
    apply_decision,
    decide,
)

NOW = datetime(2026, 10, 9, 12, 0)
MOVES = {vc.STEP, vc.ROLLBACK, vc.BLIND_REVERT}


def shorts(n, views, start, region=None, hours=6):
    return [
        ShortOutcome(
            f"{region}-{start:%m%d%H}-{i}",
            start + timedelta(hours=hours * i),
            region,
            0,
            12,
            float(views(i)),
            "analytics_daily",
        )
        for i in range(n)
    ]


def ev(rows, status="ok", ready_depth=None, by_region=None):
    return Evidence(list(rows), status, NOW, ready_depth, by_region or {})


def days_ago(d):
    return NOW - timedelta(days=d)


def sp(value=8, **kw):
    kw.setdefault("last_validated", value)
    return KnobState("shorts_per_lf", None, value, **kw)


def lf(region="south", value=2, **kw):
    kw.setdefault("last_validated", value)
    return KnobState("lf_days", region, value, **kw)


def base_states(shorts_value=7, **kw):
    return [sp(shorts_value, **kw), lf("south", 2), lf("north", 1)]


def down_rows(start=None, region=None):
    # 9 shorts: median 1000, bottom 2 at 10 (< 0.25 x median).
    return shorts(9, lambda i: 10 if i < 2 else 1000, start or days_ago(30), region)


def up_rows(start, region=None, n=None):
    n = n or policy.UP_MIN_N
    return shorts(n, lambda i: 1000 + (i % 5) * 10, start, region)


def one(decisions, knob="shorts_per_lf", region=None):
    (d,) = [d for d in decisions if d.knob == knob and d.region == region]
    return d


def test_cold_start_holds_at_floor():
    d = one(decide(ev([]), [sp(7)], NOW))
    assert (d.action, d.reason_code) == (vc.HOLD, vc.INSUFFICIENT_EVIDENCE)
    assert (d.value, d.prev, d.floor, d.ceiling) == (7, 7, 7, 10)
    assert (d.evidence_n, d.need_n) == (0, min_references_for(0.20))


def test_down_requires_n_ge_9_up_requires_n_ge_39():
    assert policy.DOWN_MIN_N == min_references_for(0.20) == 9
    assert policy.UP_MIN_N == min_references_for(0.05) == 39
    short = decide(ev(down_rows()[:-1]), [sp(8)], NOW)
    d = one(short)
    assert d.reason_code == vc.INSUFFICIENT_EVIDENCE
    assert (d.evidence_n, d.need_n) == (8, 9)
    assert one(decide(ev(down_rows()), [sp(8)], NOW)).action == vc.STEP
    # An up signal on fewer than 39 is not enough, and says what it needs.
    d = one(decide(ev(up_rows(days_ago(40), n=20)), base_states(), NOW))
    assert d.reason_code == vc.INSUFFICIENT_EVIDENCE
    assert (d.evidence_n, d.need_n) == (20, 39)


def test_up_needs_two_distinct_cohorts_and_deadband():
    states = base_states()
    first = ev(up_rows(days_ago(70)))
    d1 = one(decide(first, states, NOW))
    assert (d1.action, d1.reason_code) == (vc.HOLD, vc.CONFIRMING)
    s1 = apply_decision(states[0], d1)
    assert s1.pending_confirm is not None and s1.value == 7
    # The same cohort cannot confirm itself.
    again = one(decide(first, [s1, *states[1:]], NOW))
    assert again.reason_code == vc.INSUFFICIENT_EVIDENCE
    # A second, distinct cohort steps up one.
    both = ev(up_rows(days_ago(70)) + up_rows(days_ago(45)))
    d2 = one(decide(both, [s1, *states[1:]], NOW))
    assert (d2.action, d2.value, d2.prev) == (vc.STEP, 8, 7)
    s2 = apply_decision(s1, d2)
    assert s2.probation_open_since == NOW and s2.pending_confirm is None
    # Bottom-2 between 0.25 and 0.75 of median: dead band, pending cleared.
    mid = shorts(9, lambda i: 500 if i < 2 else 1000, days_ago(30))
    d = one(decide(ev(mid), [replace(states[0], pending_confirm=days_ago(60))], NOW))
    assert d.reason_code == vc.IN_DEADBAND
    assert apply_decision(states[0], d).pending_confirm is None


def test_marginal_tail_steps_down_one_never_below_floor():
    d = one(decide(ev(down_rows()), [sp(9)], NOW))
    assert (d.action, d.prev, d.value, d.reason_code) == (vc.STEP, 9, 8, "TAIL_DOWN")
    assert d.state_patch["probation_open_since"] == NOW
    d = one(decide(ev(down_rows()), [sp(7)], NOW))
    assert (d.action, d.value, d.reason_code) == (vc.HOLD, 7, vc.AT_FLOOR)
    # Only one low short is not a tail: no move.
    one_low = shorts(9, lambda i: 10 if i == 0 else 1000, days_ago(30))
    assert one(decide(ev(one_low), [sp(9)], NOW)).action == vc.HOLD


def test_cooldown_blocks_second_step():
    d = one(decide(ev(down_rows()), [sp(9, last_change_at=days_ago(13))], NOW))
    assert d.reason_code == vc.COOLDOWN and d.next_eval == days_ago(13) + timedelta(14)
    assert (
        one(decide(ev(down_rows()), [sp(9, last_change_at=days_ago(15))], NOW)).action
        == vc.STEP
    )
    rows = down_rows(region="south")
    held = decide(ev(rows), [lf("south", 2, last_change_at=days_ago(27))], NOW)
    assert one(held, "lf_days", "south").reason_code == vc.COOLDOWN
    free = decide(ev(rows), [lf("south", 2, last_change_at=days_ago(29))], NOW)
    assert one(free, "lf_days", "south").action == vc.STEP


def test_each_ceiling_refuses_with_own_code():
    rows = up_rows(days_ago(40))
    at = one(decide(ev(rows), [sp(10), *base_states()[1:]], NOW))
    assert at.reason_code == vc.AT_CEILING and at.value == 10
    # 4 LF days/week x 8 shorts = 32 > 30.
    four = [sp(7), lf("south", 2), lf("north", 2)]
    assert one(decide(ev(rows), four, NOW)).reason_code == vc.JOINT_CEILING
    # No LF-day states at all assumes the per-week ceiling, the safe side.
    assert one(decide(ev(rows), [sp(7)], NOW)).reason_code == vc.JOINT_CEILING
    learned = sp(8, learned_ceiling=9, learned_ceiling_until=NOW + timedelta(10))
    d = one(decide(ev(rows), [learned, *base_states()[1:]], NOW))
    assert d.reason_code == vc.LEARNED_CEILING
    expired = replace(learned, learned_ceiling_until=NOW - timedelta(1))
    d = one(decide(ev(rows), [expired, *base_states()[1:]], NOW))
    assert d.reason_code == vc.CONFIRMING
    locked = sp(8, lockout_until=NOW + timedelta(5))
    d = one(decide(ev(rows), [locked, *base_states()[1:]], NOW))
    assert d.reason_code == vc.ROLLBACK_LOCKOUT and d.next_eval == locked.lockout_until
    south_rows = up_rows(days_ago(40), region="south")
    states = [lf("south", 1)]
    thin = decide(ev(south_rows, ready_depth=1), states, NOW)
    assert one(thin, "lf_days", "south").reason_code == vc.SUPPLY_BOUND
    # Unmeasured depth never raises SUPPLY_BOUND.
    unknown = decide(ev(south_rows, ready_depth=None), states, NOW)
    assert one(unknown, "lf_days", "south").reason_code == vc.CONFIRMING
    full = [lf("south", 3)]
    d = one(decide(ev(south_rows), full, NOW), "lf_days", "south")
    assert d.reason_code == vc.AT_CEILING
    wide = [lf("south", 3), lf("north", 1)]
    irl = up_rows(days_ago(40), region="north")
    d = one(decide(ev(irl), wide, NOW), "lf_days", "north")
    assert d.reason_code == vc.JOINT_CEILING


def probation_fixture(post_views, base_views=lambda i: 900 + i * 10, **kw):
    change = days_ago(40)
    rows = shorts(12, base_views, days_ago(80)) + shorts(
        12, post_views, days_ago(25), hours=12
    )
    state = sp(
        8, last_validated=7, last_change_at=change, probation_open_since=change, **kw
    )
    return ev(rows), [state, *base_states()[1:]]


def test_probation_fail_rolls_back_and_doubles_lockout():
    e, states = probation_fixture(lambda i: 200 + i)
    d = one(decide(e, states, NOW))
    assert (d.action, d.prev, d.value) == (vc.ROLLBACK, 8, 7)
    patch = d.state_patch
    assert patch["rollback_count"] == 1
    assert patch["lockout_until"] == NOW + timedelta(days=28)  # 14 x 2^1
    assert patch["learned_ceiling"] == 8
    assert patch["learned_ceiling_until"] == NOW + timedelta(days=90)
    assert patch["probation_open_since"] is None
    for count, days in ((1, 56), (2, 112), (3, 112), (9, 112)):
        e, states = probation_fixture(lambda i: 200 + i, rollback_count=count)
        d = one(decide(e, states, NOW))
        assert d.state_patch["lockout_until"] == NOW + timedelta(days=days)
    after = apply_decision(states[0], d)
    assert vc.decide(e, [after, *states[1:]], NOW)[0].reason_code == vc.ROLLBACK_LOCKOUT


def test_probation_pass_advances_last_validated():
    e, states = probation_fixture(lambda i: 880 + i * 10, rollback_count=2)
    d = one(decide(e, states, NOW))
    assert (d.action, d.value) == (vc.VALIDATED, 8)
    after = apply_decision(states[0], d)
    assert after.last_validated == 8 and after.probation_open_since is None
    assert after.rollback_count == 0 and after.value == 8
    # Too small a post cohort stays in probation.
    e, states = probation_fixture(lambda i: 880)
    short = replace(e, shorts=e.shorts[:12] + e.shorts[16:])
    d = one(decide(short, states, NOW))
    assert d.reason_code == vc.INSUFFICIENT_EVIDENCE and (d.evidence_n, d.need_n) == (
        8,
        9,
    )


def test_blind_21d_reverts_toward_last_validated_never_above():
    rows = down_rows(days_ago(30))
    high = [sp(9, last_validated=8), *base_states()[1:]]
    d = one(decide(ev(rows, status="error"), high, NOW))
    assert (d.action, d.value, d.reason_code) == (vc.BLIND_REVERT, 8, "ANALYTICS_ERROR")
    low = [sp(7, last_validated=9), *base_states()[1:]]
    d = one(decide(ev(rows, status="scope_missing"), low, NOW))
    assert (d.action, d.value) == (vc.BLIND_REVERT, 8)
    at = [sp(8, last_validated=8), *base_states()[1:]]
    d = one(decide(ev(rows, status="error"), at, NOW))
    assert (d.action, d.reason_code, d.value) == (vc.HOLD, "ANALYTICS_ERROR", 8)
    # Status ok but no settled short for more than 21 days.
    stale = shorts(9, lambda i: 1000, days_ago(60))
    d = one(decide(ev(stale), high, NOW))
    assert (d.action, d.value, d.reason_code) == (vc.BLIND_REVERT, 8, "ANALYTICS_STALE")
    fresh = shorts(9, lambda i: 1000, days_ago(12))
    assert one(decide(ev(fresh), high, NOW)).action != vc.BLIND_REVERT


def test_refusal_passthrough(monkeypatch):
    flat = probation_fixture(lambda i: 200, base_views=lambda i: 1000)
    d = one(decide(*flat, NOW))
    assert (d.action, d.reason_code) == (vc.HOLD, vc.NO_BASELINE_VARIATION)
    zero = shorts(9, lambda i: 0, days_ago(30))
    assert one(decide(ev(zero), [sp(9)], NOW)).reason_code == vc.NO_BASELINE_VARIATION
    monkeypatch.setattr(
        vc, "robust_deviation", lambda *a, **k: Refusal("insufficient_resolution", "x")
    )
    e, states = probation_fixture(lambda i: 200 + i)
    d = one(decide(e, states, NOW))
    assert (d.action, d.reason_code, d.value) == (vc.HOLD, "INSUFFICIENT_RESOLUTION", 8)


def test_state_unreadable_fails_closed():
    out = decide(ev([]), None, NOW)
    assert {d.knob for d in out} == set(policy.KNOBS)
    assert all(
        d.reason_code == vc.STATE_UNREADABLE and d.action == vc.HOLD for d in out
    )
    out = decide(ev(down_rows()), [sp(99)], NOW)
    assert out[0].reason_code == vc.STATE_UNREADABLE and out[0].value == 99
    out = decide(ev(down_rows()), [sp(9), sp(9)], NOW)
    assert [d.reason_code for d in out][1] == vc.STATE_UNREADABLE
    assert out[0].action == vc.STEP
    broken = sp(9, probation_open_since=NOW)  # no last_change_at
    assert decide(ev([]), [broken], NOW)[0].reason_code == vc.STATE_UNREADABLE


def test_one_knob_moves_per_evaluation():
    rows = down_rows(region=None) + down_rows(start=days_ago(31), region="south")
    states = [sp(9), lf("south", 2)]
    out = decide(ev(rows), states, NOW)
    assert [d.action for d in out] == [vc.STEP, vc.HOLD]
    assert out[1].reason_code == vc.COOLDOWN and out[1].state_patch == {}


def test_shorts_times_holds_with_a_reason():
    s = KnobState("shorts_times", "south", ["14:00", "18:00"])
    d = one(decide(ev(down_rows(region="south")), [s], NOW), "shorts_times", "south")
    assert (d.action, d.reason_code, d.value) == (
        vc.HOLD,
        vc.INSUFFICIENT_EVIDENCE,
        ["14:00", "18:00"],
    )


def test_property_never_leaves_bounds_one_knob_one_step():
    rng = random.Random(0)
    states = [sp(7), lf("south", 2), lf("north", 1)]
    rows = []
    now = NOW - timedelta(days=200)
    moves = 0
    kinds = set()
    for step in range(1200):
        now += timedelta(days=rng.choice((1, 1, 2, 3)))
        for _ in range(rng.randint(0, 5)):
            region = rng.choice((None, "south", "north"))
            weak = (step // 150) % 2 == 1 and rng.random() < 0.15
            views = rng.choice((5, 20, 60)) if weak else rng.lognormvariate(6.5, 0.08)
            rows.append(
                ShortOutcome(
                    f"v{step}-{len(rows)}",
                    now - timedelta(days=rng.randint(7, 12)),
                    region,
                    0,
                    12,
                    views,
                    "analytics_daily",
                )
            )
        status = "ok" if rng.random() < 0.97 else "error"
        e = Evidence(rows[-400:], status, now, rng.choice((None, 1, 5)))
        out = decide(e, states, now)
        assert len(out) == len(states)
        moved = [d for d in out if d.action in MOVES]
        assert len(moved) <= 1
        for s, d in zip(states, out):
            assert (d.knob, d.region) == (s.knob, s.region)
            assert d.prev == s.value
            assert abs(d.value - d.prev) <= 1
            if d.action not in MOVES:
                assert d.value == d.prev
            assert d.floor <= d.value <= d.ceiling
            kinds.add(d.action)
        states = [apply_decision(s, d) for s, d in zip(states, out)]
        moves += len(moved)
        shorts_v = states[0].value
        lf_total = states[1].value + states[2].value
        assert policy.SHORTS_FLOOR <= shorts_v <= policy.SHORTS_CEILING
        assert lf_total <= policy.LF_DAYS_CEILING_PER_WEEK
        assert all(
            1 <= s.value <= policy.LF_DAYS_CEILING_PER_REGION for s in states[1:]
        )
        assert shorts_v * lf_total <= policy.JOINT_WEEKLY_SHORTS_CEILING
        assert shorts_v < policy.ENFORCEMENT_SHORTS_PER_LF
    assert moves >= 5, "the property run must actually exercise moves"
    assert vc.STEP in kinds


def test_every_reachable_pair_below_14_per_lf():
    ctx = vc._Ctx(NOW, ev([]), 7, 0)
    for shorts_v in range(policy.SHORTS_FLOOR, policy.SHORTS_CEILING + 1):
        for total in range(policy.LF_DAYS_FLOOR, policy.LF_DAYS_CEILING_PER_WEEK + 1):
            c = replace(ctx, lf_total=total)
            s = sp(shorts_v)
            blocked = vc._raise_blocked(s, shorts_v + 1, c)
            reachable_next = blocked is None
            assert shorts_v < policy.ENFORCEMENT_SHORTS_PER_LF
            if reachable_next:
                assert shorts_v + 1 < policy.ENFORCEMENT_SHORTS_PER_LF
                assert (shorts_v + 1) * total <= policy.JOINT_WEEKLY_SHORTS_CEILING


@pytest.mark.parametrize("module", ["volume_policy", "volume_controller"])
def test_purity_no_io_imports(module):
    path = Path(vc.__file__).with_name(f"{module}.py")
    tree = ast.parse(path.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imported.add((node.module or "").split(".")[0])
    forbidden = {
        "os",
        "sqlite3",
        "pathlib",
        "json",
        "io",
        "subprocess",
        "socket",
        "shutil",
        "logging",
        "random",
        "time",
        "tempfile",
        "urllib",
        "requests",
        "sys",
    }
    assert not imported & forbidden
    assert ".now(" not in path.read_text() and "utcnow" not in path.read_text()


def test_decision_fields_are_complete():
    d = one(decide(ev([]), [sp(7)], NOW))
    assert isinstance(d, Decision)
    fields = ("knob", "region", "action", "prev", "value", "reason_code", "basis")
    fields += ("evidence_n", "need_n", "floor", "ceiling", "evidence_window")
    fields += ("evidence_json", "next_eval")
    for name in fields:
        assert hasattr(d, name)
    assert d.next_eval > NOW


def test_supply_bound_holds_when_ready_depth_short():
    rows = up_rows(days_ago(40), region="south") + up_rows(days_ago(40), "north")
    states = [lf("south", 1), lf("north", 1)]
    # Depth is per region: south is thin and holds; north is deep and is not
    # blocked by south's shortage (it reaches the confirm step instead).
    by_region = {"south": 1, "north": 5}
    out = decide(ev(rows, by_region=by_region), states, NOW)
    assert one(out, "lf_days", "south").reason_code == vc.SUPPLY_BOUND
    assert one(out, "lf_days", "north").reason_code == vc.CONFIRMING
    # A region missing from the map falls back to the one global depth, and an
    # unmeasured depth never blocks.
    out = decide(ev(rows, ready_depth=0, by_region={"south": 9}), states, NOW)
    assert one(out, "lf_days", "south").reason_code == vc.CONFIRMING
    assert one(out, "lf_days", "north").reason_code == vc.SUPPLY_BOUND
    out = decide(ev(rows), states, NOW)
    assert one(out, "lf_days", "south").reason_code == vc.CONFIRMING


def test_per_region_depth_does_not_touch_the_shorts_knobs():
    rows = up_rows(days_ago(40))
    a = decide(ev(rows, by_region={"south": 0}), [sp(7), lf("south", 1)], NOW)
    b = decide(ev(rows), [sp(7), lf("south", 1)], NOW)
    assert one(a) == one(b)


def test_joint_ceiling_blocks_lf_up_when_shorts_at_limit():
    rows = up_rows(days_ago(40), region="south")
    # 3 -> 4 LF days/week at 9 shorts/LF is 36 shorts/week, above the 30 ceiling,
    # although 4 days/week alone is within its own cap.
    states = [sp(9), lf("south", 2), lf("north", 1)]
    d = one(decide(ev(rows, by_region={"south": 9}), states, NOW), "lf_days", "south")
    assert d.reason_code == vc.JOINT_CEILING and d.action == vc.HOLD
    assert "36" in d.basis
