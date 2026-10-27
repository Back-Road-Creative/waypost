"""``seo-manager collect status``: one line per source, honest about unknowns."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from seo_manager import cli
from seo_manager.analytics.continuous_optimization_daemon import SyncOutcome
from seo_manager.collection.contracts import SourceHealth
from seo_manager.collection.store import CollectionStore

NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)


def run(capsys, *argv):
    rc = cli.main(["collect", "status", *argv])
    out = capsys.readouterr()
    return rc, out.out, out.err


def make_db(tmp_path, *health):
    db = tmp_path / "c.db"
    s = CollectionStore(db)
    s.init()
    for h in health:
        s.write_batch("gms", health=h)
    return db


def test_missing_db_exits_2_and_is_not_created(tmp_path, capsys):
    db = tmp_path / "nope" / "c.db"
    rc, out, err = run(capsys, "--tenant", "gms", "--db", str(db))
    assert rc == 2 and "does not exist" in err and out == ""
    assert not db.exists()


def test_never_ran_says_so(tmp_path, capsys):
    db = make_db(tmp_path)
    rc, out, _ = run(capsys, "--tenant", "gms", "--db", str(db))
    assert rc == 0 and "no sources have run" in out


def test_partial_and_error_lines(tmp_path, capsys):
    reset = datetime.now(UTC) + timedelta(hours=2)
    db = make_db(
        tmp_path,
        SourceHealth("gms", "yt", NOW, NOW, SyncOutcome.PARTIAL),
        SourceHealth(
            "gms", "gsc", NOW, None, SyncOutcome.FAILED, "quota_exhausted", reset
        ),
        SourceHealth("gms", "raw"),
    )
    rc, out, _ = run(capsys, "--tenant", "gms", "--db", str(db))
    lines = {ln.split()[0]: ln for ln in out.splitlines()}
    assert rc == 0
    assert "partial" in lines["yt"] and "error=none" in lines["yt"]
    assert "failed" in lines["gsc"] and "quota_exhausted" in lines["gsc"]
    assert (
        "success=never" in lines["gsc"] and reset.strftime("%Y-%m-%d") in lines["gsc"]
    )
    assert "unknown" in lines["raw"] and "success=never" in lines["raw"]


def test_json_output(tmp_path, capsys):
    db = make_db(tmp_path, SourceHealth("gms", "yt", NOW, NOW, SyncOutcome.FRESH))
    rc, out, _ = run(capsys, "--tenant", "gms", "--db", str(db), "--json")
    (row,) = json.loads(out)
    assert rc == 0 and row["source"] == "yt" and row["last_outcome"] == "fresh"
    assert row["error_class"] is None and row["next_eligible"] is None


def test_tenant_is_required_and_scoped(tmp_path, capsys):
    db = make_db(tmp_path, SourceHealth("gms", "yt", NOW, NOW, SyncOutcome.FRESH))
    _, out, _ = run(capsys, "--tenant", "other", "--db", str(db))
    assert "yt" not in out


def test_db_defaults_to_collection_db(tmp_path, capsys, monkeypatch):
    db = make_db(tmp_path, SourceHealth("gms", "yt", NOW, NOW, SyncOutcome.FRESH))
    monkeypatch.setenv("SEO_COLLECTION_DB", str(db))
    rc, out, _ = run(capsys, "--tenant", "gms")
    assert rc == 0 and out.startswith("yt")
