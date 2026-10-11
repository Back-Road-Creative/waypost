"""
Event system for SEO Manager.

Defines event types and event structure for audit trails,
analytics tracking, and state change history.
"""

import json
import zlib
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional


class EventType(Enum):
    """All possible event types in the SEO management system"""

    METADATA_GENERATED = "metadata_generated"
    METADATA_UPDATED = "metadata_updated"
    VALIDATION_FAILED = "validation_failed"
    VALIDATION_PASSED = "validation_passed"
    AUTO_FIX_APPLIED = "auto_fix_applied"
    PERFORMANCE_UPDATED = "performance_updated"
    COMMUNITY_UPDATED = "community_updated"
    DEMOGRAPHICS_UPDATED = "demographics_updated"
    METADATA_PUBLISHED = "metadata_published"


@dataclass
class SEOEvent:
    """
    Immutable event representing a state change in the SEO system.

    Events are stored as JSONL (JSON Lines) with CRC32 checksums
    for corruption detection and data integrity.

    All events are append-only and never modified, enabling:
    - Complete audit trails
    - Time-travel queries
    - Corruption detection and recovery
    """

    event_id: str  # Unique event identifier (UUID)
    event_type: EventType  # Type of event
    site_id: str  # Site partition key for isolation
    content_id: str  # Content reference
    timestamp: str  # ISO 8601 UTC timestamp
    data: Dict  # Event payload (event-specific data)
    metadata: Optional[Dict] = None  # System metadata (version, user, etc.)
    checksum: Optional[int] = None  # CRC32 checksum (computed on serialization)

    def to_jsonl(self) -> str:
        """
        Serialize event to JSONL format with CRC32 checksum.

        The checksum is calculated on the JSON string (without checksum field)
        to detect any data corruption when reading from storage.

        Returns:
            JSONL string (single line of JSON with checksum)
        """
        # Build data dict without checksum
        data = {
            "event_id": self.event_id,
            "event_type": self.event_type.value,  # Convert enum to string
            "site_id": self.site_id,
            "content_id": self.content_id,
            "timestamp": self.timestamp,
            "data": self.data,
            "metadata": self.metadata if self.metadata is not None else {},
        }

        # Serialize to JSON (compact format)
        json_str = json.dumps(data, separators=(",", ":"))

        # Calculate CRC32 checksum
        checksum = zlib.crc32(json_str.encode("utf-8"))

        # Add checksum to data
        data["checksum"] = checksum

        # Return final JSONL string
        return json.dumps(data, separators=(",", ":"))

    @classmethod
    def from_jsonl(cls, line: str) -> "SEOEvent":
        """
        Deserialize event from JSONL format with checksum validation.

        Validates data integrity by recalculating checksum and comparing
        with stored value. Raises ValueError if checksums don't match.

        Args:
            line: JSONL string (single line of JSON)

        Returns:
            SEOEvent instance

        Raises:
            ValueError: If checksum validation fails
        """
        # Parse JSON
        data = json.loads(line)

        # Extract and remove checksum
        stored_checksum = data.pop("checksum", None)

        # Validate checksum if present
        if stored_checksum is not None:
            # Recalculate checksum on data without checksum field
            json_str = json.dumps(data, separators=(",", ":"))
            computed_checksum = zlib.crc32(json_str.encode("utf-8"))

            if computed_checksum != stored_checksum:
                raise ValueError(
                    f"Checksum mismatch: computed={computed_checksum}, "
                    f"stored={stored_checksum}. Data may be corrupted."
                )

        # Convert event_type string back to enum
        data["event_type"] = EventType(data["event_type"])

        # Handle missing metadata field (legacy compatibility)
        if "metadata" not in data:
            data["metadata"] = {}

        # Create and return event
        return cls(**data)
