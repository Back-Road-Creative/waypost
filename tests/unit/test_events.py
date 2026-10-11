"""
Unit tests for SEO Event system.

Tests cover:
- Event creation and immutability
- EventType enum values
- JSONL serialization with CRC32 checksums
- Checksum validation on deserialization
- Event data integrity
"""

import json
import zlib
from datetime import datetime

import pytest

from seo_manager.core.events import EventType, SEOEvent


class TestEventType:
    """Test EventType enum"""

    def test_event_type_values(self):
        """Test that all expected event types exist"""
        assert EventType.METADATA_GENERATED.value == "metadata_generated"
        assert EventType.METADATA_UPDATED.value == "metadata_updated"
        assert EventType.VALIDATION_FAILED.value == "validation_failed"
        assert EventType.VALIDATION_PASSED.value == "validation_passed"
        assert EventType.AUTO_FIX_APPLIED.value == "auto_fix_applied"
        assert EventType.PERFORMANCE_UPDATED.value == "performance_updated"
        assert EventType.METADATA_PUBLISHED.value == "metadata_published"


class TestSEOEvent:
    """Test SEOEvent dataclass"""

    def test_create_basic_event(self):
        """Test creating a basic event"""
        event = SEOEvent(
            event_id="evt_001",
            event_type=EventType.METADATA_GENERATED,
            site_id="test_site",
            content_id="content_001",
            timestamp="2026-01-16T12:00:00.000000+00:00",
            data={"title": "Test Title", "quality_score": 0.85},
        )

        assert event.event_id == "evt_001"
        assert event.event_type == EventType.METADATA_GENERATED
        assert event.site_id == "test_site"
        assert event.content_id == "content_001"
        assert event.timestamp == "2026-01-16T12:00:00.000000+00:00"
        assert event.data["title"] == "Test Title"
        assert event.data["quality_score"] == 0.85

    def test_optional_metadata_field(self):
        """Test that metadata field is optional"""
        event = SEOEvent(
            event_id="evt_001",
            event_type=EventType.METADATA_GENERATED,
            site_id="test_site",
            content_id="content_001",
            timestamp="2026-01-16T12:00:00.000000+00:00",
            data={},
        )

        assert event.metadata is None

    def test_metadata_field_with_data(self):
        """Test metadata field with system metadata"""
        event = SEOEvent(
            event_id="evt_001",
            event_type=EventType.AUTO_FIX_APPLIED,
            site_id="test_site",
            content_id="content_001",
            timestamp="2026-01-16T12:00:00.000000+00:00",
            data={"fix": "title_truncated"},
            metadata={"user": "automated", "version": "1.0"},
        )

        assert event.metadata["user"] == "automated"
        assert event.metadata["version"] == "1.0"

    def test_to_jsonl_serialization(self):
        """Test serialization to JSONL format with checksum"""
        event = SEOEvent(
            event_id="evt_001",
            event_type=EventType.METADATA_GENERATED,
            site_id="test_site",
            content_id="content_001",
            timestamp="2026-01-16T12:00:00.000000+00:00",
            data={"title": "Test"},
            metadata={"user": "system"},
        )

        jsonl_str = event.to_jsonl()

        # Should be valid JSON
        data = json.loads(jsonl_str)

        assert data["event_id"] == "evt_001"
        assert data["event_type"] == "metadata_generated"
        assert data["site_id"] == "test_site"
        assert data["content_id"] == "content_001"
        assert data["timestamp"] == "2026-01-16T12:00:00.000000+00:00"
        assert data["data"] == {"title": "Test"}
        assert data["metadata"] == {"user": "system"}
        assert "checksum" in data
        assert isinstance(data["checksum"], int)

    def test_checksum_calculation(self):
        """Test that checksum is correctly calculated"""
        event = SEOEvent(
            event_id="evt_001",
            event_type=EventType.METADATA_GENERATED,
            site_id="test_site",
            content_id="content_001",
            timestamp="2026-01-16T12:00:00.000000+00:00",
            data={"title": "Test"},
        )

        jsonl_str = event.to_jsonl()
        data = json.loads(jsonl_str)
        stored_checksum = data["checksum"]

        # Recalculate checksum
        data_without_checksum = {
            "event_id": data["event_id"],
            "event_type": data["event_type"],
            "site_id": data["site_id"],
            "content_id": data["content_id"],
            "timestamp": data["timestamp"],
            "data": data["data"],
            "metadata": data["metadata"],
        }
        json_str = json.dumps(data_without_checksum, separators=(",", ":"))
        computed_checksum = zlib.crc32(json_str.encode("utf-8"))

        assert stored_checksum == computed_checksum

    def test_from_jsonl_deserialization(self):
        """Test deserialization from JSONL"""

        # This will fail checksum validation, which is expected
        # Let's create a valid JSONL first
        event = SEOEvent(
            event_id="evt_001",
            event_type=EventType.METADATA_GENERATED,
            site_id="test_site",
            content_id="content_001",
            timestamp="2026-01-16T12:00:00.000000+00:00",
            data={"title": "Test"},
            metadata={"user": "system"},
        )

        valid_jsonl = event.to_jsonl()
        restored_event = SEOEvent.from_jsonl(valid_jsonl)

        assert restored_event.event_id == "evt_001"
        assert restored_event.event_type == EventType.METADATA_GENERATED
        assert restored_event.site_id == "test_site"
        assert restored_event.content_id == "content_001"
        assert restored_event.data == {"title": "Test"}
        assert restored_event.metadata == {"user": "system"}

    def test_roundtrip_serialization(self):
        """Test that to_jsonl -> from_jsonl roundtrip preserves data"""
        original = SEOEvent(
            event_id="evt_123",
            event_type=EventType.PERFORMANCE_UPDATED,
            site_id="my_site",
            content_id="post_456",
            timestamp="2026-01-16T14:30:00.000000+00:00",
            data={"views": 1250, "clicks": 87, "impressions": 5000, "ctr": 0.0174},
            metadata={"source": "youtube_analytics", "version": "1.0"},
        )

        # Serialize and deserialize
        jsonl_str = original.to_jsonl()
        restored = SEOEvent.from_jsonl(jsonl_str)

        assert restored.event_id == original.event_id
        assert restored.event_type == original.event_type
        assert restored.site_id == original.site_id
        assert restored.content_id == original.content_id
        assert restored.timestamp == original.timestamp
        assert restored.data == original.data
        assert restored.metadata == original.metadata

    def test_checksum_validation_failure(self):
        """Test that corrupted data fails checksum validation"""
        # Create valid event
        event = SEOEvent(
            event_id="evt_001",
            event_type=EventType.METADATA_GENERATED,
            site_id="test_site",
            content_id="content_001",
            timestamp="2026-01-16T12:00:00.000000+00:00",
            data={"title": "Test"},
        )

        jsonl_str = event.to_jsonl()
        data = json.loads(jsonl_str)

        # Corrupt the data
        data["data"]["title"] = "Corrupted"
        corrupted_jsonl = json.dumps(data)

        # Should raise ValueError on checksum mismatch
        with pytest.raises(ValueError, match="Checksum mismatch"):
            SEOEvent.from_jsonl(corrupted_jsonl)

    def test_enum_conversion_in_serialization(self):
        """Test that EventType enum is converted to string in JSONL"""
        event = SEOEvent(
            event_id="evt_001",
            event_type=EventType.AUTO_FIX_APPLIED,
            site_id="test_site",
            content_id="content_001",
            timestamp="2026-01-16T12:00:00.000000+00:00",
            data={},
        )

        jsonl_str = event.to_jsonl()
        data = json.loads(jsonl_str)

        # EventType should be serialized as string
        assert data["event_type"] == "auto_fix_applied"
        assert isinstance(data["event_type"], str)

    def test_enum_conversion_in_deserialization(self):
        """Test that string is converted back to EventType enum"""
        jsonl_data = {
            "event_id": "evt_001",
            "event_type": "validation_passed",
            "site_id": "test_site",
            "content_id": "content_001",
            "timestamp": "2026-01-16T12:00:00.000000+00:00",
            "data": {},
            "metadata": {},
        }

        # Calculate checksum
        json_str = json.dumps(jsonl_data, separators=(",", ":"))
        checksum = zlib.crc32(json_str.encode("utf-8"))
        jsonl_data["checksum"] = checksum

        jsonl_str = json.dumps(jsonl_data)
        event = SEOEvent.from_jsonl(jsonl_str)

        # Should be EventType enum
        assert event.event_type == EventType.VALIDATION_PASSED
        assert isinstance(event.event_type, EventType)

    def test_missing_checksum_handling(self):
        """Test that missing checksum is handled gracefully"""
        jsonl_data = {
            "event_id": "evt_001",
            "event_type": "metadata_generated",
            "site_id": "test_site",
            "content_id": "content_001",
            "timestamp": "2026-01-16T12:00:00.000000+00:00",
            "data": {},
            "metadata": {},
        }

        jsonl_str = json.dumps(jsonl_data)

        # Should handle missing checksum (legacy data compatibility)
        event = SEOEvent.from_jsonl(jsonl_str)

        assert event.event_id == "evt_001"
        assert event.event_type == EventType.METADATA_GENERATED

    def test_empty_metadata_default(self):
        """Test that missing metadata defaults to empty dict"""
        jsonl_data = {
            "event_id": "evt_001",
            "event_type": "metadata_generated",
            "site_id": "test_site",
            "content_id": "content_001",
            "timestamp": "2026-01-16T12:00:00.000000+00:00",
            "data": {},
            # Note: metadata field is missing
        }

        json_str = json.dumps(jsonl_data, separators=(",", ":"))
        checksum = zlib.crc32(json_str.encode("utf-8"))
        jsonl_data["checksum"] = checksum

        jsonl_str = json.dumps(jsonl_data)
        event = SEOEvent.from_jsonl(jsonl_str)

        # Should default to empty dict, not None
        assert event.metadata == {}

    def test_complex_data_structure(self):
        """Test event with complex nested data"""
        complex_data = {
            "metadata": {
                "title": "Test Title",
                "description": "Long description",
                "tags": ["ai", "seo", "testing"],
                "quality_metrics": {
                    "score": 0.87,
                    "keyword_density": 0.045,
                    "readability": 72.3,
                },
            },
            "fixes_applied": [
                {"fix_id": "title_truncate", "confidence": 0.95},
                {"fix_id": "tag_limit", "confidence": 0.90},
            ],
        }

        event = SEOEvent(
            event_id="evt_complex",
            event_type=EventType.METADATA_GENERATED,
            site_id="test_site",
            content_id="complex_001",
            timestamp="2026-01-16T12:00:00.000000+00:00",
            data=complex_data,
        )

        # Roundtrip test
        jsonl_str = event.to_jsonl()
        restored = SEOEvent.from_jsonl(jsonl_str)

        assert restored.data == complex_data
        assert restored.data["metadata"]["tags"] == ["ai", "seo", "testing"]
        assert restored.data["fixes_applied"][0]["confidence"] == 0.95
