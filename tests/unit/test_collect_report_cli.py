"""``seo-manager collect report``: read-only daily report, plus the
collection-to-report acceptance path. No network."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from seo_manager import cli
from seo_manager.collection.cycle import run_cycle
from seo_manager.collection.store import CollectionStore
from seo_manager.collection.youtube_search_terms import (
    SOURCE as TERMS,
)
from seo_manager.collection.youtube_search_terms import (
    YouTubeSearchTermsAdapter,
)

NOW = datetime(2026, 10, 27, 18, tzinfo=UTC)


class FakeAnalytics:
    def __init__(self, rows):
        self.rows = rows

    def reports(self):
        return self

    def query(self, **kw):
        self.kw = kw
        return self

    def execute(self):
        return {"rows": self.rows}


class FakeYouTube:
    """Fakes ``videos().list(**kw).execute()`` from a video_id -> (title, desc) map."""

    def __init__(self, texts):
        self.texts = texts
        self.calls = []

    def videos(self):
        return self

    def list(self, **kw):
        self.calls.append(kw)
        return self

    def execute(self):
        ids = self.calls[-1]["id"].split(",")
        return {
            "items": [
                {"id": i, "snippet": {"title": t, "description": d}}
                for i, (t, d) in self.texts.items()
                if i in ids
            ]
        }


def report(capsys, *argv):
    rc = cli.main(["collect", "report", *argv])
    out = capsys.readouterr()
    return rc, out.out, out.err


def collected_db(tmp_path):
    db = tmp_path / "c.db"
    store = CollectionStore(db)
    store.init()
    analytics = FakeAnalytics(
        [["lava tube hike", 50, 9], ["covered word", 40, 5], ["tiny", 2, 1]]
    )
    adapter = YouTubeSearchTermsAdapter(analytics, ["v1"], read=lambda fn: fn())
    run_cycle(store, "gms", [adapter], now=NOW)
    return db


def test_missing_db_exits_2_and_is_not_created(tmp_path, capsys):
    db = tmp_path / "nope" / "c.db"
    rc, out, err = report(capsys, "--tenant", "gms", "--db", str(db))
    assert rc == 2 and "collect report" in err and out == ""
    assert not db.exists()


def test_empty_store_reports_never_ran_and_no_fetch(tmp_path, capsys, monkeypatch):
    db = tmp_path / "c.db"
    CollectionStore(db).init()
    monkeypatch.setattr(
        cli, "_youtube_data_client", lambda p: (_ for _ in ()).throw(AssertionError)
    )
    rc, out, _ = report(capsys, "--tenant", "gms", "--db", str(db), "--json")
    data = json.loads(out)
    assert rc == 0
    assert {s["state"] for s in data["sources"]} == {"never_ran"}
    assert data["open"] == [] and data["recovery_history"] == "not_recorded"


def test_credentials_but_no_videos_is_not_needed_not_fetched(
    tmp_path, capsys, monkeypatch
):
    db = tmp_path / "c.db"
    CollectionStore(db).init()
    monkeypatch.setattr(
        cli, "_youtube_data_client", lambda p: (_ for _ in ()).throw(AssertionError)
    )
    rc, out, _ = report(
        capsys,
        "--tenant",
        "gms",
        "--db",
        str(db),
        "--json",
        "--youtube-credentials",
        "x",
    )
    assert rc == 0 and json.loads(out)["video_text"]["state"] == "not_needed"


def test_without_credentials_no_fetch_and_text_says_why(tmp_path, capsys):
    db = collected_db(tmp_path)
    rc, out, _ = report(capsys, "--tenant", "gms", "--db", str(db), "--json")
    data = json.loads(out)
    assert rc == 0
    assert data["video_text"]["state"] == "not_configured"
    assert data["open"] == []
    assert data["needs_evidence"][0]["missing_evidence"][0] == "video_text_unavailable"


def test_json_shape(tmp_path, capsys):
    db = collected_db(tmp_path)
    _, out, _ = report(capsys, "--tenant", "gms", "--db", str(db), "--json")
    assert set(json.loads(out)) == {
        "tenant",
        "generated_at",
        "sources",
        "recovery_history",
        "video_text",
        "open",
        "open_total",
        "needs_evidence",
        "next_evaluation",
    }


def test_client_build_failure_is_reported_by_type_only(tmp_path, capsys, monkeypatch):
    db = collected_db(tmp_path)

    def boom(path):
        raise FileNotFoundError("/secret/path/token.json")

    monkeypatch.setattr(cli, "_youtube_data_client", boom)
    rc, out, err = report(
        capsys, "--tenant", "gms", "--db", str(db), "--youtube-credentials", "x"
    )
    assert rc == 0
    assert "client_build_failed:FileNotFoundError" in out
    assert "secret" not in out + err


def test_collection_to_report_acceptance(tmp_path, capsys, monkeypatch):
    db = collected_db(tmp_path)
    yt = FakeYouTube({"v1": ("Covered word guide", "a walk")})
    monkeypatch.setattr(cli, "_youtube_data_client", lambda path: yt)
    rc, out, _ = report(
        capsys,
        "--tenant",
        "gms",
        "--db",
        str(db),
        "--youtube-credentials",
        "creds.json",
        "--json",
    )
    data = json.loads(out)
    assert rc == 0 and data["video_text"]["state"] == "fetched"
    assert [o["asset"] for o in data["open"]] == ["v1"]
    top = data["open"][0]
    assert "lava tube hike" in top["observed"]
    assert any(e["source"] == TERMS and e["metric"] == "views" for e in top["evidence"])
    assert 'searching "covered word"' not in json.dumps(data["open"])
    states = {s["source"]: s["state"] for s in data["sources"]}
    assert states[TERMS] == "fresh"
    assert list(states.values()).count("never_ran") == 2
    assert data["next_evaluation"] is not None
    # human text form carries the same facts
    _, text, _ = report(
        capsys, "--tenant", "gms", "--db", str(db), "--youtube-credentials", "c"
    )
    assert "lava tube hike" in text and TERMS in text
