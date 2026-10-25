"""The nightly volume_decide runner."""

import json
from datetime import datetime, time, timedelta
from pathlib import Path

import pytest

from seo_manager.analytics import cadence_decisions as cd
from seo_manager.analytics import volume_decide as vd
from seo_manager.analytics import volume_policy as policy
from seo_manager.core.profile import load_cadence
from seo_manager.scheduling import lf_days
from seo_manager.scheduling.store import ScheduleStore

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "evidence_bundle_v1.json"
TODAY = "2026-10-09"


def bundle(tmp_path, status="ok", shorts=None, **health):
    raw = json.loads(FIXTURE.read_text())
    raw["health"]["analytics_status"] = status
    raw["health"].update(health)
    if shorts is not None:
        raw["shorts"] = shorts
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(raw))
    return path


def run(capsys, tmp_path, evidence, db=None, *extra):
    db = db or tmp_path / "cadence_decisions.sqlite3"
    code = vd.main(
        ["--evidence", str(evidence), "--db", str(db), "--today", TODAY, *extra]
    )
    out = capsys.readouterr().out
    return code, [x for x in out.splitlines() if x.startswith("VOLUME_DECISION")], out


def short_rows(n, views, region="us"):
    start = datetime(2026, 8, 1, 12)
    return [
        {
            "video_id": f"s{i}",
            "project": "examplebrand",
            "region": region,
            "platform": "youtube",
            "posted_at": (start + timedelta(hours=6 * i)).isoformat(),
            "weekday": 0,
            "hour_local": 9,
            "day7_views": views,
            "source": "analytics_daily",
        }
        for i in range(n)
    ]


def test_decide_prints_one_VOLUME_DECISION_line_per_knob_including_holds(
    capsys, tmp_path
):
    code, lines, _ = run(capsys, tmp_path, bundle(tmp_path))
    assert code == 0
    knobs = [ln.split()[1] for ln in lines]
    assert knobs == ["knob=shorts_per_lf", "knob=lf_days", "knob=shorts_times"]
    for ln in lines:
        assert " action=hold " in ln and "reason=INSUFFICIENT_EVIDENCE" in ln
        assert f"policy_rev={policy.POLICY_REV}" in ln
        assert all(f in ln for f in ("region=", "value=", "reason="))
    # holds are persisted too: three rows, and a second night adds three more
    assert len(cd.history(tmp_path / "cadence_decisions.sqlite3")) == 3
    run(capsys, tmp_path, bundle(tmp_path))
    assert len(cd.history(tmp_path / "cadence_decisions.sqlite3")) == 6


def test_decide_steps_one_knob_and_persists_state(capsys, tmp_path):
    ev = bundle(tmp_path, shorts=short_rows(policy.UP_MIN_N, 1000, region=None))
    # a second night on the same evidence: the first held CONFIRMING
    c1, l1, _ = run(capsys, tmp_path, ev)
    assert c1 == 0 and any("reason=CONFIRMING" in ln for ln in l1)
    db = tmp_path / "cadence_decisions.sqlite3"
    st = cd.current(db, "shorts_per_lf").state
    assert st["pending_confirm"] is not None


def test_unreadable_state_prints_STATE_UNREADABLE_exit_nonzero(capsys, tmp_path):
    db = tmp_path / "cadence_decisions.sqlite3"
    db.write_bytes(b"garbage" * 100)
    before = db.read_bytes()
    code, lines, out = run(capsys, tmp_path, bundle(tmp_path), db)
    assert code != 0 and lines == []
    assert out.startswith("STATE_UNREADABLE")
    assert db.read_bytes() == before


def test_analytics_status_scope_missing_writes_blind_row_naming_the_human_act(
    capsys, tmp_path
):
    code, lines, _ = run(capsys, tmp_path, bundle(tmp_path, "scope_missing"))
    assert code == 0 and len(lines) == 3
    for ln in lines:
        assert " action=blind " in ln and "reason=ANALYTICS_SCOPE_MISSING" in ln
        assert "re-run the YouTube authorisation once" in ln
    rows = cd.history(tmp_path / "cadence_decisions.sqlite3")
    assert [r.status for r in rows] == ["blind"] * 3


def test_refused_bundle_writes_nothing(capsys, tmp_path):
    ev = bundle(tmp_path)
    raw = json.loads(ev.read_text())
    raw["schema_version"] = 9
    ev.write_text(json.dumps(raw))
    code, lines, out = run(capsys, tmp_path, ev)
    assert code != 0 and lines == [] and "EVIDENCE_REFUSED" in out
    assert not (tmp_path / "cadence_decisions.sqlite3").exists()


def test_evidence_from_stdin(capsys, tmp_path, monkeypatch):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(FIXTURE.read_text()))
    db = tmp_path / "d.sqlite3"
    code = vd.main(["--evidence", "-", "--db", str(db), "--today", TODAY])
    assert code == 0 and capsys.readouterr().out.count("VOLUME_DECISION") == 3


def test_decisions_db_environment_persists_rows(tmp_path, monkeypatch):
    db = tmp_path / "from-env.sqlite3"
    monkeypatch.setenv("SEO_DECISIONS_DB", str(db))
    evidence = bundle(tmp_path)

    assert vd.main(["--evidence", str(evidence), "--today", TODAY]) == 0
    assert len(cd.history(db)) == 3


def test_explicit_decisions_db_overrides_environment(tmp_path, monkeypatch):
    env_db = tmp_path / "from-env.sqlite3"
    explicit_db = tmp_path / "explicit.sqlite3"
    monkeypatch.setenv("SEO_DECISIONS_DB", str(env_db))

    assert (
        vd.main(
            [
                "--evidence",
                str(bundle(tmp_path)),
                "--db",
                str(explicit_db),
                "--today",
                TODAY,
            ]
        )
        == 0
    )
    assert len(cd.history(explicit_db)) == 3
    assert not env_db.exists()


@pytest.mark.parametrize("value", [None, ""])
def test_missing_decisions_db_is_an_argument_error(
    tmp_path, monkeypatch, capsys, value
):
    monkeypatch.chdir(tmp_path)
    if value is None:
        monkeypatch.delenv("SEO_DECISIONS_DB", raising=False)
    else:
        monkeypatch.setenv("SEO_DECISIONS_DB", value)

    with pytest.raises(SystemExit) as exc:
        vd.main(["--evidence", "unread-evidence.json"])
    assert exc.value.code == 2
    assert "SEO_DECISIONS_DB" in capsys.readouterr().err
    assert not list(tmp_path.iterdir())


def test_help_names_decisions_db_environment(monkeypatch, capsys):
    monkeypatch.delenv("SEO_DECISIONS_DB", raising=False)
    with pytest.raises(SystemExit) as exc:
        vd.main(["--help"])
    assert exc.value.code == 0
    assert "SEO_DECISIONS_DB" in capsys.readouterr().out


# --- the decider chooses and persists the LF weekdays ----------------------

BRAND = """\
seo:
  cadence:
    superlong_day: sat
    regions:
      north: {dow: [sun], time: "14:00"}
      south: {dow: [tue, fri], time: "16:00"}
"""
GOLDEN_LF = (
    "VOLUME_DECISION knob=lf_days region=us action=hold value=1 "
    "reason=INSUFFICIENT_EVIDENCE policy_rev=1 n=1 need=9 next_eval=2026-10-10"
)


@pytest.fixture
def brand(tmp_path):
    p = tmp_path / "brand.yaml"
    p.write_text(BRAND)
    return p


@pytest.fixture
def sched(tmp_path, brand):
    """A schedule db with 2 ready drives in south (deep) and 1 in north (thin)."""
    c = load_cadence(brand)
    st = ScheduleStore(tmp_path / "schedule.sqlite3", c.tz, c.regions)
    st.init()
    for project, region in (("a", "south"), ("b", "south"), ("c", "north")):
        st.register_ready(project, region, 1, mirror_sha="x")
    return st.path


def lf_bundle(tmp_path, shorts):
    raw = json.loads(FIXTURE.read_text())
    raw["shorts"], raw["longforms"] = shorts, []
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(raw))
    return path


def rows_at(start, n, views, region="south", hours=6):
    first = datetime.fromisoformat(start)
    return [
        {
            "video_id": f"{start}-{i}", "project": "examplebrand", "region": region,
            "platform": "youtube", "weekday": 0, "hour_local": 9,
            "posted_at": (first + timedelta(hours=hours * i)).isoformat(),
            "day7_views": views(i), "source": "analytics_daily",
        }
        for i in range(n)
    ]  # fmt: skip


def south_shorts(n=45):
    return rows_at("2026-08-10T12:00:00", n, lambda i: 1000)


def lf_row(db, region, dow, state=None, **kw):
    fields = dict(
        knob="lf_days", region=region, value=len(dow), prev=len(dow), status="hold",
        source="policy", reason_code="X", basis="t", applied_at=datetime(2026, 8, 1),
        evidence={"dow": list(dow)}, state=state or {},
    )  # fmt: skip
    return cd.append(db, **{**fields, **kw})


def seed_south_confirmed(db, dow=(1, 4), region="south"):
    """A row one cohort into an up signal, so the next good cohort steps."""
    st = {"pending_confirm": "2026-08-01T00:00:00", "last_validated": len(dow)}
    return lf_row(db, region, dow, st)


def lf_lines(lines, region=None):
    return [ln for ln in lines if " knob=lf_days " in ln and (
        region is None or f" region={region} " in ln)]  # fmt: skip


def yrun(capsys, tmp_path, evidence, brand, *extra, db=None):
    return run(capsys, tmp_path, evidence, db, "--brand-yaml", str(brand), *extra)


def test_without_brand_yaml_output_is_unchanged(capsys, tmp_path):
    code, lines, _ = run(capsys, tmp_path, bundle(tmp_path))
    assert code == 0 and GOLDEN_LF in lines
    assert not any("dow=" in ln for ln in lines)
    rows = cd.history(tmp_path / "cadence_decisions.sqlite3")
    assert all("dow" not in r.evidence for r in rows)


def test_yaml_regions_get_an_lf_days_key_and_every_row_carries_dow(
    capsys, tmp_path, brand, sched
):
    ev = lf_bundle(tmp_path, [])  # no shorts at all
    code, lines, _ = yrun(capsys, tmp_path, ev, brand, "--schedule-db", str(sched))
    assert code == 0
    got = {ln.split()[2]: ln for ln in lf_lines(lines)}
    assert set(got) == {"region=north", "region=south"}
    assert got["region=south"].endswith(" dow=tue,fri")
    assert got["region=north"].endswith(" dow=sun")
    assert " value=2 " in got["region=south"] and " value=1 " in got["region=north"]
    db = tmp_path / "cadence_decisions.sqlite3"
    for r in cd.history(db, "lf_days"):
        assert r.evidence["dow"] == {"south": [1, 4], "north": [6]}[r.region]
    for ln in lines:  # the shorts knobs carry no dow token
        assert ("dow=" in ln) == (" knob=lf_days " in ln)


def test_hold_copies_the_decided_set_forward(capsys, tmp_path, brand, sched):
    db = tmp_path / "cadence_decisions.sqlite3"
    seed_south_confirmed(db, dow=(2, 3))
    ev = lf_bundle(tmp_path, [])
    yrun(capsys, tmp_path, ev, brand, "--schedule-db", str(sched))
    last = cd.current(db, "lf_days", "south")
    assert last.id and last.evidence["dow"] == [2, 3] and last.value == 2
    assert lf_days.effective_dow(db, "south", (1, 4)).dow == (2, 3)


def test_up_step_persists_the_chosen_set_and_load_cadence_returns_it(
    capsys, tmp_path, brand, sched
):
    db = tmp_path / "cadence_decisions.sqlite3"
    seed_south_confirmed(db)
    ev = lf_bundle(tmp_path, south_shorts())
    code, lines, _ = yrun(capsys, tmp_path, ev, brand, "--schedule-db", str(sched))
    assert code == 0
    (line,) = lf_lines(lines, "south")
    # every free weekday is one day from a used one; the lowest weekday wins
    assert " action=step value=3 " in line and line.endswith(" dow=mon,tue,fri")
    row = cd.current(db, "lf_days", "south")
    assert row.evidence["dow"] == [0, 1, 4] and row.value == 3
    assert lf_days.effective_dow(db, "south", (1, 4)).dow == (0, 1, 4)
    assert load_cadence(brand, decisions_db=db).days("south") == {0, 1, 4}
    assert load_cadence(brand, decisions_db=db).days("north") == {6}


def test_rollback_restores_the_earlier_set(capsys, tmp_path, brand, sched):
    db = tmp_path / "cadence_decisions.sqlite3"
    change = "2026-08-30T00:00:00"
    lf_row(db, "south", (2, 3), {"last_validated": 2}, status="ok")
    st = {"last_validated": 2, "last_change_at": change, "probation_open_since": change}
    lf_row(db, "south", (0, 2, 3), st, status="ok", prev=2)
    shorts = rows_at("2026-08-01T00:00:00", 12, lambda i: 900 + i * 10)
    shorts += rows_at("2026-09-15T00:00:00", 12, lambda i: 200 + i, hours=12)
    code, lines, _ = yrun(
        capsys, tmp_path, lf_bundle(tmp_path, shorts), brand,
        "--schedule-db", str(sched),
    )  # fmt: skip
    assert code == 0
    (line,) = lf_lines(lines, "south")
    assert " action=rollback value=2 " in line and line.endswith(" dow=wed,thu")
    row = cd.current(db, "lf_days", "south")
    assert row.evidence["dow"] == [2, 3] and row.source == "rollback"


def test_no_free_weekday_holds_joint_ceiling(
    capsys, tmp_path, brand, sched, monkeypatch
):
    # The policy rails (4 LF days + 1 superlong) leave a weekday free, so a real
    # exhaustion is unreachable; the guard still has to hold, not crash.
    def boom(*a, **k):
        raise ValueError("no free weekday: [0, 1, 2, 3, 4, 5, 6] are used")

    monkeypatch.setattr(lf_days, "choose_dow", boom)
    db = tmp_path / "cadence_decisions.sqlite3"
    seed_south_confirmed(db)
    ev = lf_bundle(tmp_path, south_shorts())
    code, lines, _ = yrun(capsys, tmp_path, ev, brand, "--schedule-db", str(sched))
    assert code == 0
    (line,) = lf_lines(lines, "south")
    assert " action=hold value=2 reason=JOINT_CEILING " in line
    assert line.endswith(" dow=tue,fri")
    row = cd.current(db, "lf_days", "south")
    assert "south" in row.basis and "no free weekday" in row.basis
    assert row.value == 2 and row.evidence["dow"] == [1, 4]
    assert row.state.get("probation_open_since") is None


def test_brand_without_a_superlong_day_still_decides_weekdays(capsys, tmp_path):
    p = tmp_path / "brand.yaml"
    p.write_text(BRAND.replace("    superlong_day: sat\n", ""))
    assert load_cadence(p).superlong_day is None
    code, lines, _ = yrun(capsys, tmp_path, lf_bundle(tmp_path, []), p)
    assert code == 0
    assert {ln.split()[-1] for ln in lf_lines(lines)} == {"dow=tue,fri", "dow=sun"}


def test_revert_never_restores_a_weekday_another_region_took():
    # south ran mon/wed/thu, stepped down to wed/thu, north then took mon: going
    # back up to three days must pick a free day, not the logged mon/wed/thu.
    from types import SimpleNamespace as NS

    earlier = [NS(evidence={"dow": [0, 2, 3]}, value=3)]
    for action in (vd.ROLLBACK, vd.BLIND_REVERT):
        d = NS(action=action, value=3)
        assert vd._dow_for(d, (2, 3), frozenset({0}), {}, earlier) == (2, 3, 5)
        assert vd._dow_for(d, (2, 3), frozenset(), {}, earlier) == (0, 2, 3)


def test_thin_region_holds_supply_bound_while_a_deep_one_is_not_blocked(
    capsys, tmp_path, brand, sched
):
    db = tmp_path / "cadence_decisions.sqlite3"
    seed_south_confirmed(db)
    seed_south_confirmed(db, (6,), "north")
    shorts = south_shorts() + [
        {**r, "region": "north", "video_id": "i" + r["video_id"]}
        for r in south_shorts()
    ]
    code, lines, _ = yrun(
        capsys, tmp_path, lf_bundle(tmp_path, shorts), brand,
        "--schedule-db", str(sched),
    )  # fmt: skip
    assert code == 0
    # north has 1 ready drive (< 2): SUPPLY_BOUND; south has 2 and steps
    assert "reason=SUPPLY_BOUND" in lf_lines(lines, "north")[0]
    assert " action=step " in lf_lines(lines, "south")[0]


def test_unreadable_or_missing_schedule_db_reads_depth_zero_and_creates_nothing(
    capsys, tmp_path, brand
):
    db = tmp_path / "cadence_decisions.sqlite3"
    ev = lf_bundle(tmp_path, south_shorts())
    for extra in ((), ("--schedule-db", str(tmp_path / "nope.sqlite3"))):
        db.unlink(missing_ok=True)
        seed_south_confirmed(db)
        code, lines, _ = yrun(capsys, tmp_path, ev, brand, *extra)
        assert code == 0
        assert "reason=SUPPLY_BOUND" in lf_lines(lines, "south")[0]
        assert "could not be read" in cd.current(db, "lf_days", "south").basis
    assert not (tmp_path / "nope.sqlite3").exists()
    assert sorted(p.name for p in tmp_path.iterdir() if "nope" in p.name) == []
    bad = tmp_path / "bad.sqlite3"
    bad.write_bytes(b"garbage" * 100)
    db.unlink()
    seed_south_confirmed(db)
    code, lines, _ = yrun(capsys, tmp_path, ev, brand, "--schedule-db", str(bad))
    assert code == 0 and "reason=SUPPLY_BOUND" in lf_lines(lines, "south")[0]
    assert bad.read_bytes() == b"garbage" * 100


def test_malformed_stored_dow_is_state_unreadable(capsys, tmp_path, brand, sched):
    db = tmp_path / "cadence_decisions.sqlite3"
    seed_south_confirmed(db)
    lf_row(db, "south", (1, 4), evidence={"dow": [1, 2, 3]})
    before = len(cd.history(db))
    code, lines, out = yrun(
        capsys, tmp_path, lf_bundle(tmp_path, []), brand, "--schedule-db", str(sched)
    )
    assert code == 3 and lines == [] and out.startswith("STATE_UNREADABLE")
    assert len(cd.history(db)) == before


def test_bad_brand_yaml_is_refused_and_writes_nothing(capsys, tmp_path):
    bad = tmp_path / "b.yaml"
    bad.write_text("seo: {}\n")
    code, lines, out = yrun(capsys, tmp_path, bundle(tmp_path), bad)
    assert code == 2 and lines == [] and "CADENCE_REFUSED" in out
    assert not (tmp_path / "cadence_decisions.sqlite3").exists()


# --- the decider chooses and persists each region's shorts hours -----------

BRAND_T = """\
seo:
  cadence:
    regions:
      north: {dow: [sun], time: "14:00", shorts_times: ["18:00"]}
      south: {dow: [tue, fri], time: "16:00", shorts_times: ["10:00", "15:00"]}
"""


@pytest.fixture
def brand_t(tmp_path):
    p = tmp_path / "brand_t.yaml"
    p.write_text(BRAND_T)
    return p


def hour_rows(hour, base, region="south", n=9):
    first = datetime(2026, 8, 1, 12) + timedelta(minutes=hour)
    return [
        {
            "video_id": f"{region}-{hour}-{i}", "project": "examplebrand",
            "region": region, "platform": "youtube",
            "posted_at": (first + timedelta(hours=6 * i)).isoformat(),
            "weekday": 0, "hour_local": hour, "day7_views": base + i,
            "source": "analytics_daily",
        }
        for i in range(n)
    ]  # fmt: skip


def south_hours(region="south"):
    rows = []
    # 10 weakest current, 15 mid; 14 the best legal candidate; 18 is North's
    # shorts hour (fenced) and 20 is above the rail: both stronger, neither legal.
    for hour, base in ((10, 100), (15, 2000), (14, 5000), (18, 9000), (20, 20000)):
        rows += hour_rows(hour, base, region)
    return rows


def cool(db, last="2026-10-05T00:00:00"):
    """Put the other knobs in cooldown so only the shorts hours can move."""
    state = {"last_change_at": last, "last_validated": 7}
    lf_row(db, "south", (1, 4), {**state, "last_validated": 2}, status="ok")
    lf_row(db, "north", (6,), {**state, "last_validated": 1}, status="ok")
    cd.append(
        db, knob="shorts_per_lf", region=None, value=7, prev=7, status="ok", source="policy",
        reason_code="X", basis="t", applied_at=datetime(2026, 10, 5), state=state,
    )  # fmt: skip


def times_lines(lines):
    return {ln.split()[2]: ln for ln in lines if " knob=shorts_times " in ln}


def test_brand_yaml_swaps_a_regions_hour_inside_the_candidate_set(
    capsys, tmp_path, brand_t
):
    db = tmp_path / "cadence_decisions.sqlite3"
    cool(db)
    ev = lf_bundle(tmp_path, south_hours())
    code, lines, _ = yrun(capsys, tmp_path, ev, brand_t, db=db)
    assert code == 0
    got = times_lines(lines)
    assert set(got) == {"region=-", "region=south", "region=north"}
    # 14:00 is North's long-form hour: only shorts hours are fenced
    assert (
        ' action=step value=["14:00","15:00"] reason=HOUR_SWAP ' in got["region=south"]
    )
    assert ' action=hold value=["18:00"] ' in got["region=north"]
    row = cd.current(db, "shorts_times", "south")
    assert row.value == ["14:00", "15:00"] and row.prev == ["10:00", "15:00"]
    assert [r["hour"] for r in row.evidence["ranked"]] == [20, 18, 14, 15, 10]
    north = cd.current(db, "shorts_times", "north")
    assert north.value == ["18:00"] and north.evidence["ranked"] == []
    assert load_cadence(brand_t, decisions_db=db).regions["south"].shorts_times == (
        time(14), time(15),
    )  # fmt: skip


def test_region_names_match_through_canon_region(capsys, tmp_path, brand_t):
    db = tmp_path / "cadence_decisions.sqlite3"
    cool(db)
    ev = lf_bundle(tmp_path, south_hours(region=" SOUTH "))
    code, lines, _ = yrun(capsys, tmp_path, ev, brand_t, db=db)
    assert code == 0
    assert ' value=["14:00","15:00"] ' in times_lines(lines)["region=south"]


def test_held_rows_still_carry_ranked_and_the_seed(capsys, tmp_path, brand_t):
    db = tmp_path / "cadence_decisions.sqlite3"
    ev = lf_bundle(
        tmp_path, south_hours()
    )  # nothing cooled: another knob may move first
    code, _lines, _ = yrun(capsys, tmp_path, ev, brand_t, db=db)
    assert code == 0
    for region in ("south", "north"):
        row = cd.current(db, "shorts_times", region)
        assert isinstance(row.evidence["ranked"], list)
    assert cd.current(db, "shorts_times", "north").value == ["18:00"]


def test_blind_analytics_labels_every_shorts_times_line(capsys, tmp_path, brand_t):
    ev = lf_bundle(tmp_path, [])
    raw = json.loads(ev.read_text())
    raw["health"]["analytics_status"] = "scope_missing"
    ev.write_text(json.dumps(raw))
    code, lines, _ = yrun(capsys, tmp_path, ev, brand_t)
    assert code == 0
    for ln in times_lines(lines).values():
        assert " action=blind " in ln and "reason=ANALYTICS_SCOPE_MISSING" in ln


def test_without_brand_yaml_only_the_global_shorts_times_key_exists(capsys, tmp_path):
    code, lines, _ = run(capsys, tmp_path, bundle(tmp_path))
    assert code == 0
    assert list(times_lines(lines)) == ["region=-"]
    keys = cd.keys(tmp_path / "cadence_decisions.sqlite3")
    assert [k for k in keys if k[0] == "shorts_times"] == [("shorts_times", None)]


def test_malformed_stored_hours_stop_the_run(capsys, tmp_path, brand_t, monkeypatch):
    db = tmp_path / "cadence_decisions.sqlite3"
    monkeypatch.setattr(cd, "_check_coupling", lambda *a: None)
    cd.append(
        db, knob="shorts_times", region="south", value=["noon"], prev=["10:00"],
        status="ok", source="policy", reason_code="X", basis="t",
        applied_at=datetime(2026, 10, 1),
    )  # fmt: skip
    monkeypatch.undo()
    code, lines, out = yrun(capsys, tmp_path, lf_bundle(tmp_path, []), brand_t, db=db)
    assert code == vd.EXIT_UNREADABLE and lines == [] and "STATE_UNREADABLE" in out


def test_a_shorts_per_lf_raise_the_windows_cannot_carry_becomes_a_hold():
    from seo_manager.analytics import volume_controller as vc

    def dec(knob, region, value, action):
        return vc.Decision(
            knob, region, action, value - 1 if action == vc.STEP else value, value,
            "TAIL_UP", "b", 0, 0, None, None, None, {}, datetime(2026, 10, 10),
            {"probation_open_since": datetime(2026, 10, 9)},
        )  # fmt: skip

    states = [vc.KnobState("shorts_times", "north", ["18:00"], last_validated=None)]
    up = dec("shorts_per_lf", None, 8, vc.STEP)
    (got,) = vd._carry_check([up], states)
    assert (got.action, got.value, got.reason_code) == (vc.HOLD, 7, vc.JOINT_CEILING)
    assert got.state_patch == {} and "north" in got.basis
    same = dec("shorts_per_lf", None, 7, vc.HOLD)
    assert vd._carry_check([same], states) == [same]


def test_carry_check_counts_the_share_each_lf_day_gets():
    from seo_manager.analytics import volume_controller as vc

    def dec(knob, region, prev, value):
        return vc.Decision(
            knob, region, vc.STEP, prev, value, "TAIL_UP", "b", 0, 0, None, None, None,
            {}, datetime(2026, 10, 10), {"probation_open_since": datetime(2026, 10, 9)},
        )  # fmt: skip

    four = ["10:00", "14:00", "16:00", "18:00"]
    states = [
        vc.KnobState("shorts_per_lf", None, 8, last_validated=None),
        vc.KnobState("shorts_times", "south", four, last_validated=None),
        vc.KnobState("lf_days", "south", 2, last_validated=None),
    ]
    # south: 4 windows over 2 LF days = 2 a day x 7 carries 10
    up = dec("shorts_per_lf", None, 8, 10)
    assert vd._carry_check([up], states) == [up]
    # a third south LF day leaves 1 a day x 7, under the live 8 per LF: held
    (got,) = vd._carry_check([dec("lf_days", "south", 2, 3)], states)
    assert (got.action, got.value, got.reason_code) == (vc.HOLD, 2, vc.JOINT_CEILING)
    assert got.state_patch == {} and "south" in got.basis
    # ...and with 3 LF days in force a raise past 7 is held
    states[2] = vc.KnobState("lf_days", "south", 3, last_validated=None)
    (got,) = vd._carry_check([dec("shorts_per_lf", None, 7, 8)], states)
    assert (got.action, got.value, got.reason_code) == (vc.HOLD, 7, vc.JOINT_CEILING)
