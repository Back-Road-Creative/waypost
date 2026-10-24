"""
Unit tests for SEOEventStore.

Tests cover:
- Event appending with file locking
- Site-based storage partitioning
- Query operations (by site, type, date, content)
- Index management
- Corruption detection and handling
- Concurrent access safety
- Index corruption recovery and rebuild from events.jsonl
"""

import shutil
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from seo_manager.core.events import EventType, SEOEvent
from seo_manager.storage.event_store import SEOEventStore


class TestSEOEventStore:
    """Test SEOEventStore functionality"""

    @pytest.fixture
    def temp_storage(self):
        """Create temporary storage directory for tests"""
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        # Cleanup after test
        shutil.rmtree(temp_dir)

    def test_initialize_store(self, temp_storage):
        """Test store initialization creates necessary files"""
        store = SEOEventStore(storage_path=temp_storage)

        assert store.storage_path == temp_storage
        assert store.storage_path.exists()
        assert store.events_file.exists()
        assert store.index_file.exists()
        assert store.lock_file.exists()

    def test_append_single_event(self, temp_storage):
        """Test appending a single event to store"""
        store = SEOEventStore(storage_path=temp_storage)

        event = SEOEvent(
            event_id="evt_001",
            event_type=EventType.METADATA_GENERATED,
            site_id="test_site",
            content_id="content_001",
            timestamp=datetime.now().isoformat(),
            data={"title": "Test Title"},
        )

        event_id = store.append_event(event)

        assert event_id == "evt_001"
        # Verify event was written to file
        with open(store.events_file) as f:
            lines = f.readlines()
            assert len(lines) == 1

    def test_append_multiple_events(self, temp_storage):
        """Test appending multiple events"""
        store = SEOEventStore(storage_path=temp_storage)

        events = [
            SEOEvent(
                event_id=f"evt_{i:03d}",
                event_type=EventType.METADATA_GENERATED,
                site_id="test_site",
                content_id=f"content_{i:03d}",
                timestamp=datetime.now().isoformat(),
                data={"index": i},
            )
            for i in range(5)
        ]

        for event in events:
            store.append_event(event)

        # Verify all events written
        with open(store.events_file) as f:
            lines = f.readlines()
            assert len(lines) == 5

    def test_query_by_site(self, temp_storage):
        """Test querying events by site_id"""
        store = SEOEventStore(storage_path=temp_storage)

        # Add events for different sites
        site_a_events = [
            SEOEvent(
                event_id=f"evt_a{i}",
                event_type=EventType.METADATA_GENERATED,
                site_id="site_a",
                content_id=f"content_{i}",
                timestamp=datetime.now().isoformat(),
                data={},
            )
            for i in range(3)
        ]

        site_b_events = [
            SEOEvent(
                event_id=f"evt_b{i}",
                event_type=EventType.VALIDATION_PASSED,
                site_id="site_b",
                content_id=f"content_{i}",
                timestamp=datetime.now().isoformat(),
                data={},
            )
            for i in range(2)
        ]

        for event in site_a_events + site_b_events:
            store.append_event(event)

        # Query site_a
        results = store.query_by_site(site_id="site_a")
        assert len(results) == 3
        assert all(e.site_id == "site_a" for e in results)

        # Query site_b
        results = store.query_by_site(site_id="site_b")
        assert len(results) == 2
        assert all(e.site_id == "site_b" for e in results)

    def test_query_by_event_type(self, temp_storage):
        """Test filtering events by type"""
        store = SEOEventStore(storage_path=temp_storage)

        # Add different event types
        events = [
            SEOEvent(
                event_id="evt_001",
                event_type=EventType.METADATA_GENERATED,
                site_id="test_site",
                content_id="content_001",
                timestamp=datetime.now().isoformat(),
                data={},
            ),
            SEOEvent(
                event_id="evt_002",
                event_type=EventType.VALIDATION_FAILED,
                site_id="test_site",
                content_id="content_001",
                timestamp=datetime.now().isoformat(),
                data={},
            ),
            SEOEvent(
                event_id="evt_003",
                event_type=EventType.AUTO_FIX_APPLIED,
                site_id="test_site",
                content_id="content_001",
                timestamp=datetime.now().isoformat(),
                data={},
            ),
        ]

        for event in events:
            store.append_event(event)

        # Query for AUTO_FIX_APPLIED events
        results = store.query_by_site(
            site_id="test_site", event_type=EventType.AUTO_FIX_APPLIED
        )

        assert len(results) == 1
        assert results[0].event_type == EventType.AUTO_FIX_APPLIED

    def test_query_by_date_range(self, temp_storage):
        """Test querying events within date range"""
        store = SEOEventStore(storage_path=temp_storage)

        # Add events with different timestamps
        events = [
            SEOEvent(
                event_id="evt_001",
                event_type=EventType.METADATA_GENERATED,
                site_id="test_site",
                content_id="content_001",
                timestamp="2026-01-15T10:00:00.000000+00:00",
                data={},
            ),
            SEOEvent(
                event_id="evt_002",
                event_type=EventType.METADATA_GENERATED,
                site_id="test_site",
                content_id="content_002",
                timestamp="2026-01-16T10:00:00.000000+00:00",
                data={},
            ),
            SEOEvent(
                event_id="evt_003",
                event_type=EventType.METADATA_GENERATED,
                site_id="test_site",
                content_id="content_003",
                timestamp="2026-01-17T10:00:00.000000+00:00",
                data={},
            ),
        ]

        for event in events:
            store.append_event(event)

        # Query for events on 2026-01-16
        results = store.query_by_site(
            site_id="test_site",
            start_date="2026-01-16T00:00:00",
            end_date="2026-01-16T23:59:59",
        )

        assert len(results) == 1
        assert results[0].event_id == "evt_002"

    def test_query_with_limit(self, temp_storage):
        """Test query result limiting"""
        store = SEOEventStore(storage_path=temp_storage)

        # Add 10 events
        events = [
            SEOEvent(
                event_id=f"evt_{i:03d}",
                event_type=EventType.METADATA_GENERATED,
                site_id="test_site",
                content_id=f"content_{i:03d}",
                timestamp=datetime.now().isoformat(),
                data={},
            )
            for i in range(10)
        ]

        for event in events:
            store.append_event(event)

        # Query with limit=5
        results = store.query_by_site(site_id="test_site", limit=5)

        assert len(results) == 5

    def test_get_by_content_id(self, temp_storage):
        """Test retrieving all events for specific content"""
        store = SEOEventStore(storage_path=temp_storage)

        # Add multiple events for same content
        events = [
            SEOEvent(
                event_id="evt_001",
                event_type=EventType.METADATA_GENERATED,
                site_id="test_site",
                content_id="article_123",
                timestamp="2026-01-16T10:00:00.000000+00:00",
                data={},
            ),
            SEOEvent(
                event_id="evt_002",
                event_type=EventType.VALIDATION_FAILED,
                site_id="test_site",
                content_id="article_123",
                timestamp="2026-01-16T10:01:00.000000+00:00",
                data={},
            ),
            SEOEvent(
                event_id="evt_003",
                event_type=EventType.AUTO_FIX_APPLIED,
                site_id="test_site",
                content_id="article_123",
                timestamp="2026-01-16T10:02:00.000000+00:00",
                data={},
            ),
            SEOEvent(
                event_id="evt_004",
                event_type=EventType.METADATA_GENERATED,
                site_id="test_site",
                content_id="article_456",  # Different content
                timestamp="2026-01-16T10:00:00.000000+00:00",
                data={},
            ),
        ]

        for event in events:
            store.append_event(event)

        # Get all events for article_123
        results = store.get_by_content_id(site_id="test_site", content_id="article_123")

        assert len(results) == 3
        assert all(e.content_id == "article_123" for e in results)

    def test_index_creation(self, temp_storage):
        """Test that indexes are created and maintained"""
        store = SEOEventStore(storage_path=temp_storage)

        event = SEOEvent(
            event_id="evt_001",
            event_type=EventType.METADATA_GENERATED,
            site_id="test_site",
            content_id="content_001",
            timestamp="2026-01-16T10:00:00.000000+00:00",
            data={},
        )

        store.append_event(event)

        # Check indexes were updated
        assert "test_site" in store.indexes["by_site"]
        assert "evt_001" in store.indexes["by_site"]["test_site"]
        assert "metadata_generated" in store.indexes["by_type"]
        assert "2026-01-16" in store.indexes["by_date"]
        assert "content_001" in store.indexes["by_content"]

    def test_corrupted_event_handling(self, temp_storage):
        """Test handling of corrupted events in storage"""
        store = SEOEventStore(storage_path=temp_storage)

        # Add valid event
        valid_event = SEOEvent(
            event_id="evt_001",
            event_type=EventType.METADATA_GENERATED,
            site_id="test_site",
            content_id="content_001",
            timestamp=datetime.now().isoformat(),
            data={},
        )
        store.append_event(valid_event)

        # Manually corrupt the file
        with open(store.events_file, "a") as f:
            f.write('{"corrupted": "data without checksum"}\n')

        # Add another valid event
        valid_event2 = SEOEvent(
            event_id="evt_002",
            event_type=EventType.VALIDATION_PASSED,
            site_id="test_site",
            content_id="content_002",
            timestamp=datetime.now().isoformat(),
            data={},
        )
        store.append_event(valid_event2)

        # Query should skip corrupted line
        results = store.query_by_site(site_id="test_site")

        # Should get 2 valid events (corrupted line skipped)
        assert len(results) == 2
        assert results[0].event_id == "evt_001"
        assert results[1].event_id == "evt_002"

    def test_empty_store_query(self, temp_storage):
        """Test querying empty store"""
        store = SEOEventStore(storage_path=temp_storage)

        results = store.query_by_site(site_id="nonexistent_site")

        assert results == []

    def test_store_persistence(self, temp_storage):
        """Test that events persist across store instances"""
        # Create store and add event
        store1 = SEOEventStore(storage_path=temp_storage)
        event = SEOEvent(
            event_id="evt_persistent",
            event_type=EventType.METADATA_GENERATED,
            site_id="test_site",
            content_id="content_001",
            timestamp=datetime.now().isoformat(),
            data={"test": "persistence"},
        )
        store1.append_event(event)

        # Create new store instance pointing to same path
        store2 = SEOEventStore(storage_path=temp_storage)

        # Query should return the event
        results = store2.query_by_site(site_id="test_site")

        assert len(results) == 1
        assert results[0].event_id == "evt_persistent"
        assert results[0].data["test"] == "persistence"

    def test_file_locking_presence(self, temp_storage):
        """Test that file locking is used"""
        store = SEOEventStore(storage_path=temp_storage)

        # Lock file should exist
        assert store.lock_file.exists()

        # Append should work with locking
        event = SEOEvent(
            event_id="evt_locked",
            event_type=EventType.METADATA_GENERATED,
            site_id="test_site",
            content_id="content_001",
            timestamp=datetime.now().isoformat(),
            data={},
        )

        # Should complete successfully (lock acquired and released)
        event_id = store.append_event(event)
        assert event_id == "evt_locked"


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
