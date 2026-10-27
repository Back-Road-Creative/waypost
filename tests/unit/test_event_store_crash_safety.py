"""Crash-safety tests for SEOEventStore (atomic rewrites, recovery, locking)."""

import json
import multiprocessing
import os
import threading
from datetime import datetime
from pathlib import Path

import pytest

from seo_manager.core.events import EventType, SEOEvent
from seo_manager.storage import event_store as es
from seo_manager.storage.event_store import SEOEventStore


def _evt(event_id: str, site: str = "examplebrand", **data) -> SEOEvent:
    return SEOEvent(
        event_id=event_id,
        event_type=EventType.METADATA_GENERATED,
        site_id=site,
        content_id=f"c_{event_id}",
        timestamp=datetime.now().isoformat(),
        data=data or {"k": event_id},
    )


def _ids(store: SEOEventStore, site: str = "examplebrand") -> set[str]:
    return {e.event_id for e in store.query_by_site(site)}


def _sidecars(path: Path) -> list[Path]:
    return sorted(path.glob("events.jsonl.corrupt-*"))


def _append_many(path: str, prefix: str, n: int) -> None:
    store = SEOEventStore(Path(path))
    for i in range(n):
        store.append_event(_evt(f"{prefix}{i}"))


class TestAtomicRewrite:
    def test_upsert_crash_during_write_keeps_old_file(self, tmp_path, monkeypatch):
        store = SEOEventStore(tmp_path)
        store.append_event(_evt("a"))
        store.append_event(_evt("b"))
        before = store.events_file.read_bytes()

        def boom(src, dst):
            raise OSError("simulated crash before replace")

        monkeypatch.setattr(es.os, "replace", boom)
        with pytest.raises(OSError):
            store.upsert_event(_evt("a", changed=True))
        monkeypatch.undo()

        assert store.events_file.read_bytes() == before
        assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
        assert _ids(SEOEventStore(tmp_path)) == {"a", "b"}

    def test_upsert_keeps_jsonl_format(self, tmp_path):
        store = SEOEventStore(tmp_path)
        store.append_event(_evt("a"))
        store.upsert_event(_evt("a", changed=True))
        store.upsert_event(_evt("b"))
        lines = store.events_file.read_text().splitlines()
        assert len(lines) == 2
        assert all(json.loads(line)["event_id"] for line in lines)
        assert store.get_by_content_id("examplebrand", "c_a")[0].data == {
            "changed": True
        }

    @pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
    def test_upsert_preserves_group_writable_mode(self, tmp_path):
        store = SEOEventStore(tmp_path)
        store.append_event(_evt("a"))
        store.upsert_event(_evt("a", x=1))
        assert store.events_file.stat().st_mode & 0o777 == 0o664


class TestCrashBetweenDataAndIndex:
    def test_append_crash_before_index_write(self, tmp_path, monkeypatch):
        store = SEOEventStore(tmp_path)
        store.append_event(_evt("a"))

        def boom(self):
            raise OSError("simulated crash")

        monkeypatch.setattr(SEOEventStore, "_save_indexes", boom)
        with pytest.raises(OSError):
            store.append_event(_evt("b"))
        monkeypatch.undo()

        restarted = SEOEventStore(tmp_path)
        assert _ids(restarted) == {"a", "b"}
        assert set(restarted.indexes["by_site"]["examplebrand"]) == {"a", "b"}
        on_disk = json.loads((tmp_path / "index.json").read_text())
        assert set(on_disk["by_site"]["examplebrand"]) == {"a", "b"}

    def test_same_process_self_heals_after_failed_index_write(
        self, tmp_path, monkeypatch
    ):
        store = SEOEventStore(tmp_path)
        monkeypatch.setattr(
            SEOEventStore,
            "_save_indexes",
            lambda self: (_ for _ in ()).throw(OSError()),
        )
        with pytest.raises(OSError):
            store.append_event(_evt("a"))
        monkeypatch.undo()
        store.append_event(_evt("b"))
        assert set(store.indexes["by_site"]["examplebrand"]) == {"a", "b"}

    def test_upsert_crash_after_replace_before_index(self, tmp_path, monkeypatch):
        store = SEOEventStore(tmp_path)
        store.append_event(_evt("a"))
        monkeypatch.setattr(
            SEOEventStore,
            "_save_indexes",
            lambda self: (_ for _ in ()).throw(OSError()),
        )
        with pytest.raises(OSError):
            store.upsert_event(_evt("c"))
        monkeypatch.undo()
        restarted = SEOEventStore(tmp_path)
        assert set(restarted.indexes["by_site"]["examplebrand"]) == {"a", "c"}


class TestAppendRecovery:
    def test_truncated_final_line_is_quarantined(self, tmp_path):
        store = SEOEventStore(tmp_path)
        store.append_event(_evt("a"))
        store.append_event(_evt("b"))
        with open(store.events_file, "ab") as f:
            f.write(b'{"event_id": "c", "event_ty')  # torn append, no newline

        restarted = SEOEventStore(tmp_path)
        assert _ids(restarted) == {"a", "b"}
        side = _sidecars(tmp_path)
        assert len(side) == 1
        assert b'"event_id": "c"' in side[0].read_bytes()
        assert restarted.events_file.read_bytes().endswith(b"\n")
        restarted.append_event(_evt("d"))
        assert _ids(SEOEventStore(tmp_path)) == {"a", "b", "d"}

    def test_truncated_utf8_tail_is_quarantined(self, tmp_path):
        store = SEOEventStore(tmp_path)
        store.append_event(_evt("a"))
        with open(store.events_file, "ab") as f:
            f.write('{"x": "é'.encode()[:-1])  # cut inside a multibyte char
        assert _ids(store) == {"a"}  # reads tolerate it before recovery
        assert _ids(SEOEventStore(tmp_path)) == {"a"}
        assert len(_sidecars(tmp_path)) == 1

    def test_append_after_torn_tail_does_not_glue_lines(self, tmp_path):
        store = SEOEventStore(tmp_path)
        store.append_event(_evt("a"))
        with open(store.events_file, "ab") as f:
            f.write(b'{"event_id": "zz"')
        store.append_event(_evt("b"))  # same instance, no restart
        assert _ids(store) == {"a", "b"}
        assert len(_sidecars(tmp_path)) == 1

    def test_corrupt_middle_line_quarantined_not_fatal(self, tmp_path):
        store = SEOEventStore(tmp_path)
        for i in "abc":
            store.append_event(_evt(i))
        lines = store.events_file.read_text().splitlines()
        lines.insert(1, "THIS IS NOT VALID JSON{{{")
        store.events_file.write_text("\n".join(lines) + "\n")

        restarted = SEOEventStore(tmp_path)
        assert _ids(restarted) == {"a", "b", "c"}
        side = _sidecars(tmp_path)
        assert len(side) == 1
        assert b"NOT VALID JSON" in side[0].read_bytes()
        assert b"NOT VALID JSON" not in restarted.events_file.read_bytes()

    def test_clean_store_creates_no_sidecar(self, tmp_path):
        store = SEOEventStore(tmp_path)
        store.append_event(_evt("a"))
        SEOEventStore(tmp_path)
        assert _sidecars(tmp_path) == []


class TestIndexRebuild:
    def test_missing_index_rebuilt(self, tmp_path):
        store = SEOEventStore(tmp_path)
        store.append_event(_evt("a"))
        (tmp_path / "index.json").unlink()
        restarted = SEOEventStore(tmp_path)
        assert restarted.indexes["by_site"]["examplebrand"] == ["a"]
        assert (tmp_path / "index.json").exists()

    def test_corrupt_index_rebuilt(self, tmp_path):
        store = SEOEventStore(tmp_path)
        store.append_event(_evt("a"))
        (tmp_path / "index.json").write_text("{not json")
        restarted = SEOEventStore(tmp_path)
        assert restarted.indexes["by_site"]["examplebrand"] == ["a"]
        json.loads((tmp_path / "index.json").read_text())

    def test_stale_index_rebuilt(self, tmp_path):
        store = SEOEventStore(tmp_path)
        store.append_event(_evt("a"))
        stale = (tmp_path / "index.json").read_text()
        store.append_event(_evt("b"))
        (tmp_path / "index.json").write_text(stale)
        restarted = SEOEventStore(tmp_path)
        assert set(restarted.indexes["by_site"]["examplebrand"]) == {"a", "b"}


class TestConcurrentWriters:
    def test_threads_with_separate_stores_lose_nothing(self, tmp_path):
        stores = [SEOEventStore(tmp_path) for _ in range(4)]

        def work(idx: int) -> None:
            for i in range(15):
                if i % 5 == 0:
                    stores[idx].upsert_event(_evt(f"t{idx}_{i}"))
                else:
                    stores[idx].append_event(_evt(f"t{idx}_{i}"))

        threads = [threading.Thread(target=work, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        expected = {f"t{i}_{j}" for i in range(4) for j in range(15)}
        fresh = SEOEventStore(tmp_path)
        assert _ids(fresh) == expected
        assert set(fresh.indexes["by_site"]["examplebrand"]) == expected
        assert len(fresh.indexes["by_site"]["examplebrand"]) == len(expected)
        assert _sidecars(tmp_path) == []

    def test_processes_lose_nothing(self, tmp_path):
        ctx = multiprocessing.get_context("spawn")
        procs = [
            ctx.Process(target=_append_many, args=(str(tmp_path), f"p{i}_", 12))
            for i in range(3)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(120)
            assert p.exitcode == 0
        expected = {f"p{i}_{j}" for i in range(3) for j in range(12)}
        fresh = SEOEventStore(tmp_path)
        assert _ids(fresh) == expected
        assert set(fresh.indexes["by_site"]["examplebrand"]) == expected


class TestConsumersStillRead:
    def test_tracker_and_manager_read_recovered_store(self, tmp_path):
        from seo_manager.analytics.tracker import AnalyticsTracker

        store = SEOEventStore(tmp_path)
        store.append_event(_evt("a"))
        with open(store.events_file, "ab") as f:
            f.write(b'{"torn":')
        restarted = SEOEventStore(tmp_path)
        AnalyticsTracker(
            restarted, "examplebrand"
        )  # constructs against a recovered store
        assert _ids(restarted) == {"a"}
