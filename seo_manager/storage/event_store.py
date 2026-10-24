"""
Event Store for SEO Manager.

Provides append-only storage for SEO events with:
- JSONL file format
- File locking for concurrent safety
- Site-based partitioning
- Indexing for fast queries
- Corruption detection and recovery
"""

import contextlib
import json
import logging
from pathlib import Path

from filelock import FileLock

from ..core.events import EventType, SEOEvent

# Lock file mode: 0o664 (group-writable). The default in
# ``filelock.FileLock`` is 0o644 (owner-only writable), which means
# when two users sharing the same workspace dir (e.g. a
# container user and a host user) take turns
# running the pipeline, the second caller hits ``PermissionError``
# on lock acquisition even though both belong to the same group.
_LOCK_MODE = 0o664


def _group_writable_filelock(lock_path: Path) -> FileLock:
    """Construct a FileLock with a group-writable mode (0o664).

    Wrapper exists so the mode is set in exactly one place across
    the codebase. Use as a context manager identically to
    ``filelock.FileLock``.
    """
    return FileLock(str(lock_path), mode=_LOCK_MODE)


logger = logging.getLogger(__name__)


class SEOEventStore:
    """
    Append-only event store with site isolation.

    Storage structure:
    {storage_path}/
      events.jsonl        # Main event log
      index.json          # Date/type/content indexes
      .lock               # File lock for concurrent access
    """

    def __init__(self, storage_path: Path):
        """
        Initialize event store.

        Args:
            storage_path: Directory path for storing events
        """
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)

        self.events_file = self.storage_path / "events.jsonl"
        self.index_file = self.storage_path / "index.json"
        self.lock_file = self.storage_path / ".lock"

        # Ensure data + lock files exist with group-writable mode
        # (0o664). Path.touch() respects the process umask (typically
        # 022 → 0o644). The chmod converges back to 0o664 on every
        # init so whichever user creates the file first, the other
        # user can still write. The .lock file is pre-created here
        # for caller-visibility (existing tests assert it exists
        # post-init); filelock.FileLock will recreate it under our
        # _LOCK_MODE on each acquire regardless.
        for f in (self.events_file, self.lock_file):
            f.touch(exist_ok=True)
            with contextlib.suppress(OSError, PermissionError):
                f.chmod(_LOCK_MODE)

        # Load or initialize indexes
        self._load_indexes()

    def append_event(self, event: SEOEvent) -> str:
        """
        Append event to store (thread-safe).

        Args:
            event: SEOEvent to append

        Returns:
            event_id for tracking
        """
        with _group_writable_filelock(self.lock_file):
            # Append to JSONL file
            with open(self.events_file, "a") as f:
                f.write(event.to_jsonl() + "\n")

            # Update indexes
            self._update_indexes(event)

        return event.event_id

    def upsert_event(self, event: SEOEvent) -> str:
        """
        Insert or update an event by event_id (thread-safe).

        If an event with the same event_id already exists, replaces it
        in-place (updates data and timestamp). Otherwise appends as new.

        Args:
            event: SEOEvent to insert or update

        Returns:
            event_id for tracking
        """
        with _group_writable_filelock(self.lock_file):
            # Read all existing lines
            lines = []
            replaced = False
            if self.events_file.exists():
                with open(self.events_file) as f:
                    for line in f:
                        stripped = line.strip()
                        if not stripped:
                            continue
                        try:
                            existing = SEOEvent.from_jsonl(stripped)
                            if existing.event_id == event.event_id:
                                # Replace this line with the updated event
                                lines.append(event.to_jsonl())
                                replaced = True
                            else:
                                lines.append(stripped)
                        except (ValueError, json.JSONDecodeError, KeyError):
                            # Keep corrupted lines as-is
                            lines.append(stripped)

            if not replaced:
                lines.append(event.to_jsonl())

            # Rewrite the file
            with open(self.events_file, "w") as f:
                for line in lines:
                    f.write(line + "\n")

            # Rebuild indexes (simplest correct approach after rewrite)
            self.indexes = self._empty_indexes()
            self._rebuild_indexes()

        return event.event_id

    def query_by_site(
        self,
        site_id: str,
        event_type: EventType | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int | None = None,
    ) -> list[SEOEvent]:
        """
        Query events with filters.

        All queries are scoped to site_id for isolation.

        Args:
            site_id: Site identifier (required)
            event_type: Filter by event type (optional)
            start_date: ISO timestamp for range start (optional)
            end_date: ISO timestamp for range end (optional)
            limit: Maximum number of results (optional)

        Returns:
            List of matching events
        """
        events = []

        with open(self.events_file) as f:
            for line in f:
                try:
                    # Deserialize event
                    event = SEOEvent.from_jsonl(line.strip())

                    # Filter by site (required)
                    if event.site_id != site_id:
                        continue

                    # Filter by event type (optional)
                    if event_type and event.event_type != event_type:
                        continue

                    # Filter by date range (optional)
                    if start_date and event.timestamp < start_date:
                        continue
                    if end_date and event.timestamp > end_date:
                        continue

                    # Add to results
                    events.append(event)

                    # Apply limit
                    if limit and len(events) >= limit:
                        break

                except (ValueError, json.JSONDecodeError, KeyError) as e:
                    # Corrupted line - log and skip
                    # In production, you might want proper logging
                    print(f"Warning: Skipping corrupted event line: {e}")
                    continue

        return events

    def get_by_content_id(self, site_id: str, content_id: str) -> list[SEOEvent]:
        """
        Get all events for specific content.

        Args:
            site_id: Site identifier
            content_id: Content identifier

        Returns:
            List of events for this content
        """
        events = []

        with open(self.events_file) as f:
            for line in f:
                try:
                    event = SEOEvent.from_jsonl(line.strip())

                    # Filter by site and content_id
                    if event.site_id == site_id and event.content_id == content_id:
                        events.append(event)

                except (ValueError, json.JSONDecodeError, KeyError):
                    # Skip corrupted lines
                    continue

        return events

    def _load_indexes(self):
        """Load or create indexes, rebuilding from events.jsonl if corrupted."""
        if self.index_file.exists():
            try:
                with open(self.index_file) as f:
                    self.indexes = json.load(f)
                return
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(
                    f"Index file corrupted ({e}), rebuilding from events.jsonl"
                )
                self.indexes = self._empty_indexes()
                self._rebuild_indexes()
                return
        self.indexes = self._empty_indexes()
        self._save_indexes()

    @staticmethod
    def _empty_indexes() -> dict:
        return {
            "by_site": {},
            "by_type": {},
            "by_date": {},
            "by_content": {},
        }

    def _rebuild_indexes(self):
        """Rebuild indexes by scanning events.jsonl. Skips corrupted lines."""
        rebuilt = 0
        skipped = 0
        with open(self.events_file) as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    event = SEOEvent.from_jsonl(stripped)
                    self._update_indexes_in_memory(event)
                    rebuilt += 1
                except (ValueError, json.JSONDecodeError, KeyError):
                    skipped += 1
                    continue
        self._save_indexes()
        logger.info(
            f"Index rebuilt: {rebuilt} events indexed, {skipped} corrupted lines skipped"
        )

    def _update_indexes_in_memory(self, event: SEOEvent):
        """Update in-memory indexes without persisting (used during rebuild)."""
        if event.site_id not in self.indexes["by_site"]:
            self.indexes["by_site"][event.site_id] = []
        self.indexes["by_site"][event.site_id].append(event.event_id)

        type_key = event.event_type.value
        if type_key not in self.indexes["by_type"]:
            self.indexes["by_type"][type_key] = []
        self.indexes["by_type"][type_key].append(event.event_id)

        date_key = event.timestamp[:10]
        if date_key not in self.indexes["by_date"]:
            self.indexes["by_date"][date_key] = []
        self.indexes["by_date"][date_key].append(event.event_id)

        if event.content_id not in self.indexes["by_content"]:
            self.indexes["by_content"][event.content_id] = []
        self.indexes["by_content"][event.content_id].append(event.event_id)

    def _update_indexes(self, event: SEOEvent):
        """Update indexes after event append"""
        # Index by site
        if event.site_id not in self.indexes["by_site"]:
            self.indexes["by_site"][event.site_id] = []
        self.indexes["by_site"][event.site_id].append(event.event_id)

        # Index by type
        type_key = event.event_type.value
        if type_key not in self.indexes["by_type"]:
            self.indexes["by_type"][type_key] = []
        self.indexes["by_type"][type_key].append(event.event_id)

        # Index by date (YYYY-MM-DD)
        date_key = event.timestamp[:10]  # Extract YYYY-MM-DD
        if date_key not in self.indexes["by_date"]:
            self.indexes["by_date"][date_key] = []
        self.indexes["by_date"][date_key].append(event.event_id)

        # Index by content
        if event.content_id not in self.indexes["by_content"]:
            self.indexes["by_content"][event.content_id] = []
        self.indexes["by_content"][event.content_id].append(event.event_id)

        # Save updated indexes
        self._save_indexes()

    def _save_indexes(self):
        """Persist indexes to disk"""
        with open(self.index_file, "w") as f:
            json.dump(self.indexes, f, indent=2)
