"""Tests for SEOEventStore index resilience and corruption recovery."""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from seo_manager.core.events import EventType, SEOEvent
from seo_manager.storage.event_store import SEOEventStore


def _make_event(content_id="video_0", site_id="examplebrand"):
    return SEOEvent(
        event_id=str(uuid.uuid4()),
        event_type=EventType.METADATA_GENERATED,
        site_id=site_id,
        content_id=content_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        data={"title": f"Test {content_id}"},
    )


class TestIndexCorruptionRecovery:
    """Verify event store recovers from corrupted index.json."""

    def test_corrupted_index_rebuilds_from_events(self, tmp_path):
        """If index.json is corrupted, rebuild from events.jsonl automatically."""
        store_path = tmp_path / "seo"
        store = SEOEventStore(store_path)

        for i in range(3):
            store.append_event(_make_event(f"video_{i}"))

        assert len(store.indexes["by_site"]["examplebrand"]) == 3

        # Corrupt index.json
        (store_path / "index.json").write_text(
            '{"by_site": {"examplebrand": [CORRUPTED'
        )

        # Re-init — should rebuild without error
        store2 = SEOEventStore(store_path)
        assert len(store2.indexes["by_site"]["examplebrand"]) == 3

    def test_missing_index_creates_fresh(self, tmp_path):
        """If index.json doesn't exist, create empty indexes."""
        store_path = tmp_path / "seo"
        store = SEOEventStore(store_path)
        assert store.indexes["by_site"] == {}
        assert (store_path / "index.json").exists()

    def test_rebuild_skips_corrupted_event_lines(self, tmp_path):
        """Rebuild should skip corrupted lines in events.jsonl."""
        store_path = tmp_path / "seo"
        store = SEOEventStore(store_path)

        for i in range(3):
            store.append_event(_make_event(f"video_{i}"))

        # Inject corrupted line into events.jsonl
        events_file = store_path / "events.jsonl"
        content = events_file.read_text()
        lines = content.strip().split("\n")
        lines.insert(1, "THIS IS NOT VALID JSON{{{")
        events_file.write_text("\n".join(lines) + "\n")

        # Corrupt index to force rebuild
        (store_path / "index.json").write_text("BROKEN")

        # Re-init — should rebuild, skipping bad line
        store2 = SEOEventStore(store_path)
        assert len(store2.indexes["by_site"]["examplebrand"]) == 3

    def test_rebuild_preserves_all_index_types(self, tmp_path):
        """Rebuild should populate by_site, by_type, by_date, by_content."""
        store_path = tmp_path / "seo"
        store = SEOEventStore(store_path)
        store.append_event(_make_event("video_1"))

        # Corrupt and rebuild
        (store_path / "index.json").write_text("BROKEN")
        store2 = SEOEventStore(store_path)

        assert "examplebrand" in store2.indexes["by_site"]
        assert EventType.METADATA_GENERATED.value in store2.indexes["by_type"]
        assert "video_1" in store2.indexes["by_content"]
        assert len(store2.indexes["by_date"]) >= 1

    def test_query_works_after_rebuild(self, tmp_path):
        """After index rebuild, queries should return correct results."""
        store_path = tmp_path / "seo"
        store = SEOEventStore(store_path)

        for i in range(5):
            store.append_event(_make_event(f"video_{i}"))

        # Corrupt and rebuild
        (store_path / "index.json").write_text("BROKEN")
        store2 = SEOEventStore(store_path)

        events = store2.query_by_site("examplebrand")
        assert len(events) == 5

    def test_empty_events_file_with_corrupted_index(self, tmp_path):
        """Corrupted index + empty events.jsonl should result in empty indexes."""
        store_path = tmp_path / "seo"
        SEOEventStore(store_path)  # initialize store dirs

        (store_path / "index.json").write_text("NOT JSON")

        store2 = SEOEventStore(store_path)
        assert store2.indexes["by_site"] == {}
