"""
Event Store for SEO Manager.

Provides append-only storage for SEO events with:
- JSONL file format
- File locking for concurrent safety
- Site-based partitioning
- Indexing for fast queries
- Corruption detection and recovery

Durability contract
-------------------
- Every rewrite of ``events.jsonl`` (``upsert_event``, recovery) writes the
  full new JSONL to a temp file in the same directory, fsyncs it, and moves
  it over the original with ``os.replace`` (then fsyncs the directory where
  the platform allows). A reader or a crash sees the old file or the new
  one, never a truncated mix. The on-disk format stays plain JSONL.
  On Windows ``os.replace`` fails while another handle is open, so it is
  retried briefly before the error is raised.
- ``append_event`` writes one line, flushes and fsyncs it, under the lock.
- On load (and before an append that follows a torn write) a truncated final
  line or a corrupt middle line is copied to ``events.jsonl.corrupt-<utc>``
  and removed from the live file; valid events are never dropped, and the
  store is never reset to empty.
- ``index.json`` is derived data. It is written atomically and rebuilt from
  the JSONL on load when it is missing, unreadable or disagrees with the
  events (e.g. a crash between the data write and the index write).
- All writers serialize on the ``.lock`` file, so a rewrite cannot
  interleave with an append, across threads and processes.
"""

import contextlib
import json
import logging
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from filelock import FileLock

from ..core.events import EventType, SEOEvent
from ..fsio import atomic_write_json

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

_REPLACE_ATTEMPTS = 20
_REPLACE_DELAY_S = 0.05


def _fsync_dir(directory: Path) -> None:
    """Flush a directory entry to disk where the platform supports it."""
    if sys.platform == "win32":
        return
    with contextlib.suppress(OSError):
        fd = os.open(str(directory), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _replace_with_retry(src: Path, dst: Path) -> None:
    """``os.replace`` that tolerates a transient Windows sharing violation."""
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if sys.platform != "win32" or attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_DELAY_S)


def _atomic_write_lines(path: Path, lines: list[str]) -> None:
    """Atomically replace ``path`` with ``lines`` (one JSONL record each)."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        with contextlib.suppress(OSError):
            tmp.chmod(path.stat().st_mode & 0o777 if path.exists() else _LOCK_MODE)
        _replace_with_retry(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        raise
    _fsync_dir(path.parent)


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
        # _LOCK_MODE on each acquire regardless. It is touched after the
        # locked reconcile because filelock on Windows deletes the lock
        # file on release.
        self.indexes = self._empty_indexes()
        self._indexes_dirty = False

        self.events_file.touch(exist_ok=True)
        # Recover a torn events file and reconcile the indexes with it.
        with _group_writable_filelock(self.lock_file):
            self._reconcile_locked()

        for f in (self.events_file, self.lock_file):
            f.touch(exist_ok=True)
            with contextlib.suppress(OSError, PermissionError):
                f.chmod(_LOCK_MODE)

    def append_event(self, event: SEOEvent) -> str:
        """
        Append event to store (thread-safe, process-safe).

        Args:
            event: SEOEvent to append

        Returns:
            event_id for tracking
        """
        with _group_writable_filelock(self.lock_file):
            if self._needs_recovery():
                self._recover_locked()

            # A previous append died (or raised) after writing data but
            # before the index; rebuild rather than trust the stale copy.
            if self._indexes_dirty:
                self._reconcile_locked()
            else:
                self._refresh_indexes_locked()

            with open(self.events_file, "a", encoding="utf-8") as f:
                f.write(event.to_jsonl() + "\n")
                f.flush()
                os.fsync(f.fileno())

            self._indexes_dirty = True
            self._update_indexes(event)
            self._indexes_dirty = False

        return event.event_id

    def upsert_event(self, event: SEOEvent) -> str:
        """
        Insert or update an event by event_id (thread-safe, crash-safe).

        If an event with the same event_id already exists, replaces it
        in-place (updates data and timestamp). Otherwise appends as new.
        The file is replaced atomically (temp file + ``os.replace``).

        Args:
            event: SEOEvent to insert or update

        Returns:
            event_id for tracking
        """
        with _group_writable_filelock(self.lock_file):
            entries = self._recover_locked()

            lines = []
            replaced = False
            for existing, raw in entries:
                if existing.event_id == event.event_id:
                    lines.append(event.to_jsonl())
                    replaced = True
                else:
                    lines.append(raw)
            if not replaced:
                lines.append(event.to_jsonl())

            self._indexes_dirty = True
            _atomic_write_lines(self.events_file, lines)
            self._rebuild_indexes()
            self._indexes_dirty = False

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

        with open(self.events_file, encoding="utf-8", errors="replace") as f:
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

        with open(self.events_file, encoding="utf-8", errors="replace") as f:
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

    def _needs_recovery(self) -> bool:
        """True when the events file ends mid-line (torn append)."""
        try:
            size = self.events_file.stat().st_size
            if size == 0:
                return False
            with open(self.events_file, "rb") as f:
                f.seek(size - 1)
                return f.read(1) != b"\n"
        except FileNotFoundError:
            return False

    def _scan_locked(self) -> tuple[list[tuple[SEOEvent, str]], list[bytes], bool]:
        """Parse the events file. Returns (valid entries, bad lines, torn tail)."""
        try:
            data = self.events_file.read_bytes()
        except FileNotFoundError:
            return [], [], False
        entries: list[tuple[SEOEvent, str]] = []
        bad: list[bytes] = []
        parts = data.split(b"\n")
        torn = bool(parts[-1])  # no trailing newline
        for chunk in parts:
            if not chunk.strip():
                continue
            try:
                raw = chunk.decode("utf-8").strip()
                entries.append((SEOEvent.from_jsonl(raw), raw))
            except (ValueError, KeyError, TypeError):  # incl. JSON/Unicode errors
                bad.append(chunk)
        return entries, bad, torn

    def _quarantine(self, bad: list[bytes]) -> Path:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        sidecar = self.events_file.with_name(f"{self.events_file.name}.corrupt-{stamp}")
        with open(sidecar, "ab") as f:
            for chunk in bad:
                f.write(chunk + b"\n")
            f.flush()
            os.fsync(f.fileno())
        return sidecar

    def _recover_locked(self) -> list[tuple[SEOEvent, str]]:
        """Quarantine bad lines, repair a torn tail, return the valid entries.

        Caller holds the lock. The sidecar is durable before the live file is
        rewritten, so a crash in between loses nothing.
        """
        entries, bad, torn = self._scan_locked()
        if bad or torn:
            if bad:
                sidecar = self._quarantine(bad)
                logger.warning(
                    "Quarantined %d corrupt line(s) from %s to %s",
                    len(bad),
                    self.events_file,
                    sidecar,
                )
            _atomic_write_lines(self.events_file, [raw for _, raw in entries])
            self._indexes_dirty = True
        return entries

    def _reconcile_locked(self) -> None:
        """Recover the events file; rebuild indexes if missing/corrupt/stale."""
        entries = self._recover_locked()
        expected = self._build_indexes([e for e, _ in entries])
        loaded = self._read_index_file()
        if loaded != expected:
            if loaded is not None:
                logger.warning("Index inconsistent with events.jsonl, rebuilding")
            atomic_write_json(self.index_file, expected)
        self.indexes = expected
        self._indexes_dirty = False

    def _read_index_file(self) -> dict | None:
        try:
            with open(self.index_file, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _refresh_indexes_locked(self) -> None:
        """Pick up index changes made by other writers since we last looked."""
        loaded = self._read_index_file()
        if loaded is None or set(loaded) != set(self._empty_indexes()):
            self._reconcile_locked()
        else:
            self.indexes = loaded

    @staticmethod
    def _empty_indexes() -> dict:
        return {
            "by_site": {},
            "by_type": {},
            "by_date": {},
            "by_content": {},
        }

    def _build_indexes(self, events: list[SEOEvent]) -> dict:
        saved = self.indexes
        self.indexes = self._empty_indexes()
        try:
            for event in events:
                self._update_indexes_in_memory(event)
            return self.indexes
        finally:
            self.indexes = saved

    def _rebuild_indexes(self):
        """Rebuild indexes by scanning events.jsonl. Skips corrupted lines."""
        entries, bad, _ = self._scan_locked()
        self.indexes = self._build_indexes([e for e, _ in entries])
        self._save_indexes()
        logger.info(
            f"Index rebuilt: {len(entries)} events indexed, {len(bad)} corrupted lines skipped"
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
        """Persist indexes to disk atomically"""
        atomic_write_json(self.index_file, self.indexes)
