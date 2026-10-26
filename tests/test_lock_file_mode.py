"""Lock + event files must be group-writable (0o664) so multiple users
sharing the same workspace dir can each acquire the lock.

Background: when the dev container runs first and creates `.lock` with
the default `filelock.FileLock` mode (0o644, owner-only writable), a
subsequent run by another user in the same group hits PermissionError
on lock acquisition. The fix: a `_group_writable_filelock` wrapper that
constructs `FileLock(path, mode=0o664)` so the recreated lock file
lands at 0o664 regardless of process umask.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from seo_manager.quota.manager import _LOCK_MODE as quota_lock_mode
from seo_manager.quota.manager import QuotaManager
from seo_manager.quota.manager import _group_writable_filelock as quota_lock_helper
from seo_manager.storage.event_store import _LOCK_MODE as event_lock_mode
from seo_manager.storage.event_store import SEOEventStore
from seo_manager.storage.event_store import (
    _group_writable_filelock as event_lock_helper,
)


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


class TestLockModeConstants:
    def test_event_store_lock_mode_is_group_writable(self) -> None:
        assert event_lock_mode == 0o664

    def test_quota_manager_lock_mode_is_group_writable(self) -> None:
        assert quota_lock_mode == 0o664


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
class TestGroupWritableFilelockHelper:
    def test_event_helper_acquires_with_group_writable_mode(
        self, tmp_path: Path
    ) -> None:
        lock_path = tmp_path / "x.lock"
        with event_lock_helper(lock_path):
            assert lock_path.exists()
            assert _mode(lock_path) == 0o664, (
                f"expected 0o664, got {oct(_mode(lock_path))}"
            )

    def test_quota_helper_acquires_with_group_writable_mode(
        self, tmp_path: Path
    ) -> None:
        lock_path = tmp_path / "q.lock"
        with quota_lock_helper(lock_path):
            assert lock_path.exists()
            assert _mode(lock_path) == 0o664

    def test_helper_recreates_at_664_after_filelock_cleanup(
        self, tmp_path: Path
    ) -> None:
        # filelock.FileLock deletes the file on context exit and recreates
        # it on next acquire. Both acquires must produce 0o664.
        lock_path = tmp_path / "y.lock"
        with event_lock_helper(lock_path):
            assert _mode(lock_path) == 0o664
        # filelock 3.x deletes on exit; next acquire must still hit 664.
        with event_lock_helper(lock_path):
            assert _mode(lock_path) == 0o664


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
class TestEventStoreInitFileModes:
    def test_events_file_is_group_writable_after_init(self, tmp_path: Path) -> None:
        SEOEventStore(storage_path=tmp_path)
        events = tmp_path / "events.jsonl"
        assert events.exists()
        assert _mode(events) == 0o664

    def test_reinit_converges_back_to_group_writable(self, tmp_path: Path) -> None:
        # Simulate a stale 0o644 left by a prior run; second init must
        # converge back to 0o664 so the other user can write.
        SEOEventStore(storage_path=tmp_path)
        events = tmp_path / "events.jsonl"
        events.chmod(0o644)
        assert _mode(events) == 0o644

        SEOEventStore(storage_path=tmp_path)
        assert _mode(events) == 0o664


class TestQuotaManagerLockBehavior:
    def test_init_and_track_operation_succeeds(self, tmp_path: Path) -> None:
        qm = QuotaManager(
            project_id="test-project",
            brand_id="test-brand",
            storage_path=tmp_path,
        )
        qm.track_operation("VIDEO_LIST", cost=1)
        usage = tmp_path / "test-project" / "quota_usage.json"
        assert usage.exists()
