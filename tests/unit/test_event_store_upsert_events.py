"""Batch upsert tests for SEOEventStore.upsert_events."""

import json
from datetime import datetime

from seo_manager.core.events import EventType, SEOEvent
from seo_manager.storage.event_store import SEOEventStore


def _evt(event_id: str, site: str = "examplebrand", **data) -> SEOEvent:
    return SEOEvent(
        event_id=event_id,
        event_type=EventType.METADATA_GENERATED,
        site_id=site,
        content_id=f"c_{event_id}",
        timestamp="2026-01-02T03:04:05",
        data=data or {"k": event_id},
    )


def _file_ids(store: SEOEventStore) -> list[str]:
    return [
        json.loads(line)["event_id"]
        for line in store.events_file.read_text().splitlines()
    ]


def test_replace_keeps_position(tmp_path):
    store = SEOEventStore(tmp_path)
    for i in ("a", "b", "c"):
        store.append_event(_evt(i))
    ids = store.upsert_events([_evt("b", changed=True)])
    assert ids == ["b"]
    assert _file_ids(store) == ["a", "b", "c"]
    assert store.get_by_content_id("examplebrand", "c_b")[0].data == {"changed": True}


def test_new_events_append_in_input_order(tmp_path):
    store = SEOEventStore(tmp_path)
    store.append_event(_evt("a"))
    ids = store.upsert_events([_evt("z"), _evt("m"), _evt("a", changed=1)])
    assert ids == ["z", "m", "a"]
    assert _file_ids(store) == ["a", "z", "m"]


def test_duplicate_ids_in_batch_last_wins_once(tmp_path):
    store = SEOEventStore(tmp_path)
    store.append_event(_evt("a"))
    store.upsert_events([_evt("n", v=1), _evt("a", v=1), _evt("n", v=2)])
    assert _file_ids(store) == ["a", "n"]
    assert store.get_by_content_id("examplebrand", "c_n")[0].data == {"v": 2}


def test_equivalent_to_sequential_upsert(tmp_path):
    batch = [_evt("b", v=1), _evt("x"), _evt("b", v=2), _evt("y", site="other")]
    seed = [_evt("a"), _evt("b"), _evt("c")]
    s1 = SEOEventStore(tmp_path / "seq")
    s2 = SEOEventStore(tmp_path / "bat")
    for s in (s1, s2):
        for e in seed:
            s.append_event(e)
    for e in batch:
        s1.upsert_event(e)
    s2.upsert_events(batch)
    assert s1.events_file.read_bytes() == s2.events_file.read_bytes()
    for site in ("examplebrand", "other"):
        assert [e.to_jsonl() for e in s1.query_by_site(site)] == [
            e.to_jsonl() for e in s2.query_by_site(site)
        ]
    assert s1.indexes == s2.indexes


def test_index_rebuilt_once_per_batch(tmp_path, monkeypatch):
    store = SEOEventStore(tmp_path)
    store.append_event(_evt("a"))
    calls = []
    orig = store._rebuild_indexes
    monkeypatch.setattr(store, "_rebuild_indexes", lambda: (calls.append(1), orig())[1])
    store.upsert_events([_evt(f"e{i}") for i in range(20)])
    assert len(calls) == 1
    assert len(_file_ids(store)) == 21


def test_upsert_event_delegates_to_batch(tmp_path, monkeypatch):
    store = SEOEventStore(tmp_path)
    seen = []
    orig = store.upsert_events
    monkeypatch.setattr(
        store, "upsert_events", lambda evs: (seen.append(list(evs)), orig(evs))[1]
    )
    assert store.upsert_event(_evt("a")) == "a"
    assert len(seen) == 1


def test_empty_batch_writes_nothing(tmp_path, monkeypatch):
    store = SEOEventStore(tmp_path)
    store.append_event(_evt("a"))
    before = store.events_file.read_bytes()
    mtime = store.events_file.stat().st_mtime_ns
    monkeypatch.setattr(
        store, "_rebuild_indexes", lambda: (_ for _ in ()).throw(AssertionError)
    )
    assert store.upsert_events([]) == []
    assert store.events_file.read_bytes() == before
    assert store.events_file.stat().st_mtime_ns == mtime


def test_corrupt_line_quarantined_like_upsert_event(tmp_path):
    store = SEOEventStore(tmp_path)
    store.append_event(_evt("a"))
    with open(store.events_file, "a", encoding="utf-8") as f:
        f.write("not json\n")
    store.upsert_events([_evt("b")])
    assert _file_ids(store) == ["a", "b"]
    assert list(tmp_path.glob("events.jsonl.corrupt-*"))
