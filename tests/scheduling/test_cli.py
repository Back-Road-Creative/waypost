"""``python -m seo_manager.cli schedule ...`` end to end (claim protocol)."""

import json
from pathlib import Path

import pytest

from seo_manager.cli import main

from .conftest import at


@pytest.fixture
def run(tmp_path, brand_yaml, capsys):
    db = tmp_path / "s.sqlite3"
    base = ["schedule", "--brand-yaml", str(brand_yaml), "--db", str(db)]
    assert main([*base, "--init", "ready-depth", "--region", "north"]) == 0
    capsys.readouterr()

    def _run(*args, expect=0):
        rc = main(["schedule", "--brand-yaml", str(brand_yaml), "--db", str(db), *args])
        cap = capsys.readouterr()
        assert rc == expect, cap.err + cap.out
        return json.loads(cap.out) if cap.out.strip() else None

    return _run


NOW = "2026-06-12T09:00:00-04:00"
REG = (
    "register", "--project", "p", "--region", "north", "--lf-count", "1",
    "--mirror-sha", "abc",
)  # fmt: skip


def test_ready_depth_and_register(run):
    assert run("ready-depth", "--region", "north") == {
        "region": "north",
        "depth": 0,
        "target": 2,
        "shortfall": 2,
    }
    run(*REG)
    assert run("ready-depth", "--region", "north")["depth"] == 1


def test_register_unknown_region_exits_2(run):
    assert run("register", *REG[1:3], "--region", "mars", *REG[5:], expect=2) is None


def test_claim_complete_cycle(run):
    run(*REG)
    dry = run("claim", "--region", "north", "--now", NOW, "--dry-run")
    assert dry["confirmed_lfs"] == []
    assert dry["claim_id"] is None and dry["slots"] == [at("2026-06-14").isoformat()]
    got = run("claim", "--region", "north", "--now", NOW)  # lead from cadence
    assert got["project"] == "p" and got["claim_id"] and got["mirror_sha"] == "abc"
    again = run("claim", "--region", "north", "--now", NOW, "--lead-hours", "72")
    assert again["claim_id"] == got["claim_id"] and again["already_claimed"] is True
    assert run("complete", got["claim_id"]) == {
        "claim_id": got["claim_id"],
        "status": "complete",
    }


def test_dry_run_writes_nothing(run, tmp_path):
    run(*REG)
    run("claim", "--region", "north", "--now", NOW, "--dry-run")
    assert run("ready-depth", "--region", "north")["depth"] == 1


def test_claim_nothing_ready_prints_null_claim(run):
    assert run("claim", "--region", "north", "--now", NOW) == {
        "claim": None,
        "reason": "no_ready_drive",
    }


def test_claim_stale_sha_exits_2(run, capsys):
    run(*REG)
    run("claim", "--region", "north", "--now", NOW, "--expect-sha", "zzz", expect=2)
    assert run("ready-depth", "--region", "north")["depth"] == 1


def test_fail_and_unknown_claim(run):
    run(*REG)
    got = run("claim", "--region", "north", "--now", NOW)
    assert run("fail", got["claim_id"])["status"] == "failed"
    assert run("fail", got["claim_id"])["status"] == "failed"  # repeat is a no-op
    assert run("complete", got["claim_id"], expect=2) is None
    assert run("complete", "nope", expect=2) is None


def test_next_prints_the_slot(run):
    out = run("next", "--region", "north", "--after", "2026-06-09T09:00:00-04:00")
    assert out == {"region": "north", "slot": at("2026-06-14").isoformat()}


def test_next_unknown_region_exits_2(run):
    assert run("next", "--region", "mars", expect=2) is None


def test_report_json(run):
    out = run("report", "--now", "2026-08-10T09:00:00-04:00")
    assert out["designed_days"] == ["tue", "fri", "sun"]


def test_report_after_a_claim_lists_the_reserved_slot(run):
    run(*REG)
    got = run("claim", "--region", "north", "--now", NOW)
    out = run("report", "--now", NOW)
    assert [(e["project"], e["video_id"]) for e in out["scheduled_longforms"]] == [
        ("p", None)
    ]
    assert out["scheduled_longforms"][0]["date"] == got["slots"][0][:10]


def _entry(d, t, kind, region, project):
    return {
        "date": d,
        "time": t,
        "content_type": kind,
        "title": project,
        "project_name": project,
        "region": region,
        "video_id": "v" + project,
    }


def test_import_ledger_backfills_and_is_idempotent(run, tmp_path):
    ledger = tmp_path / ".content_ledger.json"
    ledger.write_text(
        json.dumps(
            {
                "entries": [
                    _entry("2026-08-11", "16:00:00", "longform", "south", "A"),
                    _entry("2026-08-16", "14:00:00", "longform", "north", "B"),
                    _entry("2026-08-16", "18:00:00", "shorts", "north", "S"),
                ]
            }
        )
    )
    assert run("import-ledger", str(ledger)) == {
        "imported": 2,
        "skipped_conflicts": 0,
        "skipped": [],
        "ignored_non_longform": 1,
    }
    assert run("import-ledger", str(ledger))["imported"] == 2
    report = run("report", "--now", "2026-08-10T09:00:00-04:00")
    assert [e["date"] for e in report["scheduled_longforms"]] == [
        "2026-08-11",
        "2026-08-16",
    ]
    nxt = {(m["region"], m["day"]): m["slot"] for m in report["next_available"]}
    assert nxt[("south", "tue")].startswith("2026-08-18")


def test_import_ledger_counts_same_region_date_conflicts(run, tmp_path):
    ledger = tmp_path / "l.json"
    ledger.write_text(
        json.dumps(
            {
                "entries": [
                    _entry("2026-08-11", "14:00:00", "longform", "south", "a"),
                    _entry("2026-08-11", "14:00:00", "longform", "south", "b"),
                ]
            }
        )
    )
    assert run("import-ledger", str(ledger)) == {
        "imported": 1,
        "skipped_conflicts": 1,
        "skipped": [{"project": "b", "date": "2026-08-11", "video_id": "vb"}],
        "ignored_non_longform": 0,
    }


def test_missing_brand_yaml_exits_2(tmp_path, capsys):
    rc = main(
        [
            "schedule",
            "--brand-yaml",
            str(tmp_path / "none.yaml"),
            "ready-depth",
            "--region",
            "south",
        ]
    )
    assert rc == 2 and "none.yaml" in capsys.readouterr().err


FIXTURES = Path(__file__).parent / "fixtures"


def test_import_ledger_then_report_matches_reference_schedule_report(run):
    """Parity: ``fixtures/reference_schedule_report.json`` is the video pipeline's
    ``build_schedule_report`` over ``fixtures/content_ledger.json`` (anchor 2026-08-10 09:00
    EST, the conftest cadence). Compare every field both sides define."""
    ref = json.loads((FIXTURES / "reference_schedule_report.json").read_text())
    run("import-ledger", str(FIXTURES / "content_ledger.json"))
    ours = run("report", "--now", ref["as_of"])
    assert ours["as_of"] == ref["as_of"]
    assert ours["designed_days"] == ref["designed_days"]
    assert ours["weekly_longform_capacity"] == ref["weekly_longform_capacity"]
    pick = lambda rows: [(r["date"], r["time"], r["region"]) for r in rows]  # noqa: E731
    assert pick(ours["scheduled_longforms"]) == pick(ref["scheduled_longforms"])
    assert ours["queue_ends"] == ref["queue_ends"]
    # The pipeline lists region x every designed day; we list each region's own days. Every row
    # we have must be the same slot the pipeline computed, and we must cover each region's days.
    theirs = {(m["region"], m["day"]): m for m in ref["next_available"]}
    mine = {(m["region"], m["day"]): m for m in ours["next_available"]}
    assert set(mine) == {
        ("north", "sun"), ("south", "tue"), ("south", "fri")
    }  # fmt: skip
    for key, row in mine.items():
        assert row["slot"] == theirs[key]["slot"], key
        assert row["earliest_for_region"] == (
            row["slot"] == min(m["slot"] for k, m in mine.items() if k[0] == key[0])
        )
    # Same region, day and date; the time differs by design (docs/scheduling.md): the pipeline stamps
    # its flat 11:00, we use the region's own slot time.
    assert [
        (r["region"], r["day"], r["slot"][:10])
        for r in ours["next_available_superlong"]
    ] == [
        (r["region"], r["day"], r["slot"][:10]) for r in ref["next_available_superlong"]
    ]


def test_import_ledger_unknown_region_writes_nothing(run, tmp_path):
    ledger = tmp_path / "l.json"
    ledger.write_text(
        json.dumps(
            {
                "entries": [
                    _entry("2026-08-11", "16:00:00", "longform", "south", "a"),
                    _entry("2026-08-16", "14:00:00", "longform", "mars", "b"),
                ]
            }
        )
    )
    run("import-ledger", str(ledger), expect=2)
    assert (
        run("report", "--now", "2026-08-10T09:00:00-04:00")["scheduled_longforms"] == []
    )


def test_claim_json_reports_a_stale_open_claim(run):
    run(*REG)
    run("claim", "--region", "north", "--now", NOW)
    late = run("claim", "--region", "north", "--now", "2026-06-16T09:00:00-04:00")
    assert late["already_claimed"] and late["stale"] is True


def test_schedule_without_db_or_env_exits_2(brand_yaml, monkeypatch, capsys):
    monkeypatch.delenv("SEO_SCHEDULE_DB", raising=False)
    rc = main(
        ["schedule", "--brand-yaml", str(brand_yaml), "claim", "--region", "north"]
    )
    assert rc == 2 and "--db" in capsys.readouterr().err


def test_schedule_db_from_env(brand_yaml, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SEO_SCHEDULE_DB", str(tmp_path / "e.sqlite3"))
    rc = main(
        [
            "schedule",
            "--brand-yaml",
            str(brand_yaml),
            "--init",
            "ready-depth",
            "--region",
            "north",
        ]
    )
    assert rc == 0 and json.loads(capsys.readouterr().out)["depth"] == 0


def test_malformed_brand_yaml_exits_2(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("seo: [unclosed\n  - : :", encoding="utf-8")
    rc = main(["schedule", "--brand-yaml", str(bad), "--db", str(tmp_path / "s.db"),
               "ready-depth", "--region", "north"])  # fmt: skip
    assert rc == 2 and "Traceback" not in capsys.readouterr().err


def test_a_locked_database_exits_2(run, tmp_path, monkeypatch):
    import sqlite3

    from seo_manager.scheduling import store as st

    def boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(st.ScheduleStore, "register_ready", boom)
    run(*REG, expect=2)


def test_set_status_retires_a_stale_drive(run):
    run(*REG)
    assert run("set-status", "--project", "p", "--status", "failed") == {
        "project": "p",
        "status": "failed",
    }
    assert run("ready-depth", "--region", "north")["depth"] == 0
    run("set-status", "--project", "nope", "--status", "failed", expect=2)
    run("set-status", "--project", "p", "--status", "bogus", expect=2)


@pytest.mark.parametrize(
    "argv",
    [
        ["claim", "--region", "north"],
        ["claim", "--region", "north", "--dry-run"],
        ["ready-depth", "--region", "north"],
        ["complete", "c1"],
        ["fail", "c1"],
        ["set-status", "--project", "p", "--status", "failed"],
    ],
)
def test_a_missing_database_exits_2_and_is_not_created(
    brand_yaml, tmp_path, capsys, argv
):
    db = tmp_path / "typo.sqlite3"
    rc = main(["schedule", "--brand-yaml", str(brand_yaml), "--db", str(db), *argv])
    cap = capsys.readouterr()
    assert rc == 2 and "does not exist" in cap.err and "--init" in cap.err
    assert cap.out == "" and not db.exists()
    assert not db.with_name(db.name + ".lock").exists()


def test_init_creates_a_missing_database_for_any_command(brand_yaml, tmp_path, capsys):
    db = tmp_path / "new.sqlite3"
    rc = main(["schedule", "--brand-yaml", str(brand_yaml), "--db", str(db), "--init",
               "claim", "--region", "north"])  # fmt: skip
    assert rc == 0 and json.loads(capsys.readouterr().out) == {
        "claim": None,
        "reason": "no_ready_drive",
    }
    assert db.exists()


def test_register_creates_a_missing_database(brand_yaml, tmp_path, capsys):
    db = tmp_path / "reg.sqlite3"
    rc = main(["schedule", "--brand-yaml", str(brand_yaml), "--db", str(db), *REG])
    assert rc == 0 and db.exists()


def test_register_status_auditing_is_not_claimable(run):
    out = run(*REG, "--status", "auditing")
    assert out == {"project": "p", "status": "auditing"}
    assert run("ready-depth", "--region", "north")["depth"] == 0
    assert run("claim", "--region", "north", "--now", NOW) == {
        "claim": None,
        "reason": "no_ready_drive",
    }
    run("set-status", "--project", "p", "--status", "ready")
    assert run("ready-depth", "--region", "north")["depth"] == 1


def test_register_status_rejects_claimed(run):
    run(*REG, "--status", "claimed", expect=2)


def test_set_status_cannot_orphan_a_claim(run):
    run(*REG)
    got = run("claim", "--region", "north", "--now", NOW)
    run("set-status", "--project", "p", "--status", "failed", expect=2)
    assert run("fail", got["claim_id"])["status"] == "failed"


BACKLOG = """\
- project: routea
  region: south
  sources: ["D:/a"]
  ingest_mode: copy
  lf_mode: regular
- project: routeb
  region: north
  sources: ["D:/b"]
  ingest_mode: link
  lf_mode: regular
- project: routec
  region: south
  sources: ["D:/d"]
  ingest_mode: copy
  lf_mode: regular
"""


def test_backlog_next_names_the_first_row_not_ready_failed_or_complete(run, tmp_path):
    m = tmp_path / "drive_backlog.yaml"
    m.write_text(BACKLOG, encoding="utf-8")
    got = run("backlog-next", "--manifest", str(m), "--region", "south")
    assert got["project"] == "routea" and got["sources"] == ["D:/a"]
    assert got["region"] == "south" and got["ingest_mode"] == "copy"
    assert got["day_gpx"] == "" and got["camera_utc_offset_hours"] is None
    run("register", "--project", "routea", "--region", "south", "--lf-count", "1",
        "--mirror-sha", "s")  # fmt: skip
    assert run("backlog-next", "--manifest", str(m), "--region", "south")[
        "project"
    ] == ("routec")
    run("set-status", "--project", "routea", "--status", "failed")
    assert run("backlog-next", "--manifest", str(m), "--region", "south")[
        "project"
    ] == ("routec")
    run("register", "--project", "routec", "--region", "south", "--lf-count", "1",
        "--mirror-sha", "s", "--status", "auditing")  # fmt: skip
    assert run("backlog-next", "--manifest", str(m), "--region", "south")[
        "project"
    ] == ("routec")  # still auditing: not done
    run("set-status", "--project", "routec", "--status", "ready")
    assert run("backlog-next", "--manifest", str(m), "--region", "south") == {
        "row": None
    }
    assert (
        run("backlog-next", "--manifest", str(m), "--region", "mars", expect=2) is None
    )


def test_backlog_next_bad_manifest_exits_2(run, tmp_path):
    run("backlog-next", "--manifest", str(tmp_path / "none.yaml"), "--region", "south",
        expect=2)  # fmt: skip
    bad = tmp_path / "bad.yaml"
    bad.write_text("project: x\n", encoding="utf-8")
    run("backlog-next", "--manifest", str(bad), "--region", "south", expect=2)


@pytest.mark.parametrize(
    "argv",
    [
        ["report"],
        ["next", "--region", "north"],
        ["backlog-next", "--manifest", "m", "--region", "south"],
    ],
)
def test_reads_on_a_missing_database_exit_2(brand_yaml, tmp_path, capsys, argv):
    db = tmp_path / "typo.sqlite3"
    rc = main(["schedule", "--brand-yaml", str(brand_yaml), "--db", str(db), *argv])
    assert rc == 2 and "does not exist" in capsys.readouterr().err and not db.exists()


def test_import_ledger_may_create_the_database(brand_yaml, tmp_path, capsys):
    db = tmp_path / "led.sqlite3"
    rc = main(["schedule", "--brand-yaml", str(brand_yaml), "--db", str(db),
               "import-ledger", str(FIXTURES / "content_ledger.json")])  # fmt: skip
    assert rc == 0 and db.exists()


def test_register_carries_modes_into_the_claim(run):
    run(*REG, "--lf-mode", "full_drive", "--ingest-mode", "gopro")
    got = run("claim", "--region", "north", "--now", NOW)
    assert got["lf_mode"] == "full_drive" and got["ingest_mode"] == "gopro"


def test_claim_without_registered_modes_reports_null(run):
    run(*REG)
    got = run("claim", "--region", "north", "--now", NOW)
    assert got["lf_mode"] is None and got["ingest_mode"] is None


def test_register_unknown_lf_mode_exits_2(run):
    run(*REG, "--lf-mode", "whole", expect=2)
