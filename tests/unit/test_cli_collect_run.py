"""``seo-manager collect run``: one cycle, honest about what did not run."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from seo_manager import cli
from seo_manager.analytics.continuous_optimization_daemon import SyncOutcome
from seo_manager.collection import sources
from seo_manager.collection.contracts import Checkpoint, Observation
from seo_manager.collection.cycle import CollectResult
from seo_manager.collection.sources import SourceNotRun
from seo_manager.collection.store import CollectionStore

T0 = datetime(2026, 10, 1, tzinfo=UTC)


class Fake:
    stream = "main"

    def __init__(self, source, rows=0, raises=None):
        self.source, self.rows, self.raises = source, rows, raises

    def collect(self, tenant, checkpoint, now):
        if self.raises:
            raise self.raises
        obs = [
            Observation(
                tenant,
                self.source,
                f"v{i}",
                "views",
                T0,
                T0 + timedelta(days=1),
                1.0,
                now,
            )
            for i in range(self.rows)
        ]
        return CollectResult(
            obs,
            Checkpoint(tenant, self.source, self.stream, "c", now),
            SyncOutcome.FRESH,
        )


def run(capsys, monkeypatch, tmp_path, adapters, skipped=(), *extra):
    monkeypatch.setattr(
        sources, "build_adapters", lambda **kw: (list(adapters), list(skipped))
    )
    db = tmp_path / "c.db"
    rc = cli.main(["collect", "run", "--tenant", "gms", "--db", str(db), *extra])
    out = capsys.readouterr()
    return rc, out.out, out.err, db


def test_success_writes_through_the_cycle_and_exits_0(capsys, monkeypatch, tmp_path):
    rc, out, _, db = run(capsys, monkeypatch, tmp_path, [Fake("a", rows=2)])
    assert rc == 0
    assert "a  collected  outcome=fresh  added=2  error=none" in out
    h = CollectionStore(db).health("gms", "a")
    assert h.last_outcome is SyncOutcome.FRESH and h.last_success_at is not None


def test_failed_adapter_exits_1_and_does_not_stop_the_others(
    capsys, monkeypatch, tmp_path
):
    ads = [Fake("a", raises=RuntimeError("x")), Fake("b", rows=1)]
    rc, out, _, db = run(capsys, monkeypatch, tmp_path, ads)
    assert rc == 1
    assert "a  failed" in out and "b  collected" in out
    assert CollectionStore(db).health("gms", "b").last_outcome is SyncOutcome.FRESH


def test_unconfigured_source_is_listed_never_stored_never_success(
    capsys, monkeypatch, tmp_path
):
    skipped = [SourceNotRun("c", "not_configured:video_mapping")]
    rc, out, _, db = run(capsys, monkeypatch, tmp_path, [Fake("a", rows=1)], skipped)
    assert rc == 0
    assert "c  not run: not_configured:video_mapping" in out
    assert "c  collected" not in out
    assert CollectionStore(db).health("gms", "c") is None


def test_nothing_configured_exits_2_without_success_or_db(
    capsys, monkeypatch, tmp_path
):
    skipped = [SourceNotRun("a", "not_configured:youtube_credentials")]
    rc, out, err, db = run(capsys, monkeypatch, tmp_path, [], skipped)
    assert rc == 2
    assert "not run: not_configured:youtube_credentials" in out
    assert "collected" not in out and "no source is configured" in err
    assert not db.exists()


def test_json_shape(capsys, monkeypatch, tmp_path):
    skipped = [SourceNotRun("c", "client_build_failed:OSError")]
    rc, out, _, _ = run(
        capsys, monkeypatch, tmp_path, [Fake("a", rows=1)], skipped, "--json"
    )
    assert rc == 0
    assert json.loads(out) == [
        {"source": "a", "status": "collected", "outcome": "fresh", "added": 1,
         "error_class": None},
        {"source": "c", "status": "not_run", "reason": "client_build_failed:OSError"},
    ]  # fmt: skip


def test_real_wiring_with_no_flags_exits_2(capsys, tmp_path):
    rc = cli.main(["collect", "run", "--tenant", "gms", "--db", str(tmp_path / "c.db")])
    out = capsys.readouterr()
    assert rc == 2 and "not_configured:youtube_credentials" in out.out
