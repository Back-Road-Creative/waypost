"""
Global API Quota Manager.

Tracks YouTube API quota usage across ALL operations and brands
to prevent exceeding the 10,000 units/day limit per Google Cloud project.

Critical: Multiple brands using the same Google Cloud project share
the same 10,000 unit daily quota. This manager ensures we never exceed it.
"""

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from filelock import FileLock

from seo_manager.fsio import atomic_write_json

logger = logging.getLogger(__name__)

# Lock file mode: 0o664 (group-writable). See
# ``seo_manager.storage.event_store._LOCK_MODE`` for the rationale.
_LOCK_MODE = 0o664


def _group_writable_filelock(lock_path: Path) -> FileLock:
    """Construct a FileLock with a group-writable mode (0o664).

    Wrapper exists so the mode is set in exactly one place across
    the codebase. Use as a context manager identically to
    ``filelock.FileLock``.
    """
    return FileLock(str(lock_path), mode=_LOCK_MODE)


class OperationCost(Enum):
    """Standard YouTube API operation costs"""

    VIDEO_UPLOAD = 1600
    THUMBNAIL_UPLOAD = 50
    VIDEO_SEARCH = 100
    VIDEO_LIST = 1
    CHANNEL_LIST = 1
    PLAYLIST_INSERT = 50
    PLAYLIST_LIST = 1


class QuotaExceededError(Exception):
    """Raised when quota limit would be exceeded"""

    pass


@dataclass
class QuotaOperation:
    """Record of a quota-consuming operation"""

    operation: str
    cost: int
    timestamp: str
    brand_id: str | None = None
    metadata: dict | None = None


class QuotaManager:
    """
    Manages YouTube API quota across all operations and brands.

    Each Google Cloud project has a 10,000 unit/day quota that is SHARED
    across all brands/channels using that project.

    Features:
    - Tracks all operations
    - Enforces daily limits
    - Warns at 80% and 90%
    - Prevents quota exhaustion
    - Thread-safe with file locking
    - Persists state across sessions
    - Resets daily at midnight UTC
    """

    def __init__(
        self,
        project_id: str,
        brand_id: str | None = None,
        storage_path: Path | None = None,
        daily_limit: int = 10000,
    ):
        """
        Initialize quota manager.

        Args:
            project_id: Google Cloud project ID (quota shared per project!)
            brand_id: Optional brand/channel identifier (for reporting only)
            storage_path: Where to store quota state
            daily_limit: Daily quota limit (default: 10,000)
        """
        self.project_id = project_id
        self.brand_id = brand_id
        self.daily_limit = daily_limit

        # Storage
        storage_path = storage_path or Path("./data/quota")
        self.storage_path = storage_path / project_id
        self.storage_path.mkdir(parents=True, exist_ok=True)

        self.quota_file = self.storage_path / "quota_usage.json"
        self.lock_file = self.storage_path / ".quota.lock"

        # Load current state. The lock file itself is recreated with
        # group-writable mode by ``_group_writable_filelock`` on every
        # acquire (filelock.FileLock deletes/recreates it each time).
        self._load_state()

    def _load_state(self):
        """Load quota state from disk (thread-safe)"""
        with _group_writable_filelock(self.lock_file):
            self._load_state_inner()

    def _load_state_inner(self):
        """Load quota state (must be called with lock held)"""
        if self.quota_file.exists():
            try:
                with open(self.quota_file, encoding="utf-8") as f:
                    data = json.load(f)

                # Check if we need to reset (new day)
                last_reset = datetime.fromisoformat(
                    data.get("last_reset", "2000-01-01T00:00:00+00:00")
                )
                now = datetime.now(UTC)

                if now.date() > last_reset.date():
                    # New day - reset quota
                    self._reset_state()
                else:
                    # Load existing state
                    operations = [
                        QuotaOperation(**op) for op in data.get("operations", [])
                    ]
                    total_used = data.get("total_used", 0)
                    self.operations = operations
                    self.total_used = total_used
                    self.last_reset = last_reset
            except (ValueError, TypeError, KeyError, AttributeError) as e:
                # A quota file that cannot be read is not a reason to refuse to
                # run: start from an empty day and say so.
                logger.warning(
                    "Quota file %s is unreadable (%s); starting with a fresh "
                    "quota state",
                    self.quota_file,
                    e,
                )
                self._reset_state()
        else:
            # Initialize new state
            self._reset_state()

    def _reset_state(self):
        """Reset quota state (called from within lock)"""
        self.operations = []
        self.total_used = 0
        self.last_reset = datetime.now(UTC)
        self._save_state_inner()

    def _save_state(self):
        """Save quota state to disk (thread-safe)"""
        with _group_writable_filelock(self.lock_file):
            self._save_state_inner()

    def _save_state_inner(self):
        """Save quota state (must be called with lock held)"""
        data = {
            "project_id": self.project_id,
            "total_used": self.total_used,
            "daily_limit": self.daily_limit,
            "last_reset": self.last_reset.isoformat(),
            "operations": [asdict(op) for op in self.operations],
        }

        atomic_write_json(self.quota_file, data)

    def track_operation(
        self,
        operation: str,
        cost: int,
        metadata: dict | None = None,
        enforce: bool = False,
    ):
        """
        Track an API operation and its quota cost.

        Args:
            operation: Operation name
            cost: Quota units consumed
            metadata: Optional operation metadata
            enforce: If True, raise error if quota exceeded

        Raises:
            QuotaExceededError: If enforce=True and quota exceeded
        """
        # Read-modify-write under one lock so another manager's writes since
        # our last load are kept instead of overwritten.
        with _group_writable_filelock(self.lock_file):
            self._load_state_inner()

            if self.total_used + cost > self.daily_limit and enforce:
                raise QuotaExceededError(
                    f"Operation would exceed daily quota: "
                    f"{self.total_used + cost} > {self.daily_limit} units. "
                    f"Current usage: {self.total_used}/{self.daily_limit} "
                    f"({(self.total_used / self.daily_limit) * 100:.1f}%)"
                )

            op = QuotaOperation(
                operation=operation,
                cost=cost,
                timestamp=datetime.now(UTC).isoformat(),
                brand_id=self.brand_id,
                metadata=metadata,
            )

            self.operations.append(op)
            self.total_used += cost

            self._save_state_inner()

    def check_quota(self, cost: int) -> bool:
        """
        Check if operation would exceed quota.

        Args:
            cost: Quota units needed

        Returns:
            True if operation is safe, False if would exceed
        """
        self._load_state()
        return (self.total_used + cost) <= self.daily_limit

    def can_safely_perform(self, operation_type: str) -> bool:
        """
        Check if operation type can be safely performed.

        Args:
            operation_type: Operation type name

        Returns:
            True if safe, False if would exceed quota
        """
        cost = self.estimate_cost(operation_type)
        return self.check_quota(cost)

    def estimate_cost(self, operation_type: str) -> int:
        """
        Estimate quota cost for operation type.

        Args:
            operation_type: Operation type

        Returns:
            Estimated quota units
        """
        cost_map = {
            "video_upload": OperationCost.VIDEO_UPLOAD.value,
            "thumbnail_upload": OperationCost.THUMBNAIL_UPLOAD.value,
            "video_search": OperationCost.VIDEO_SEARCH.value,
            "video_list": OperationCost.VIDEO_LIST.value,
            "channel_list": OperationCost.CHANNEL_LIST.value,
            "playlist_insert": OperationCost.PLAYLIST_INSERT.value,
        }

        return cost_map.get(operation_type, 0)

    def get_usage(self) -> dict:
        """
        Get current quota usage.

        Returns:
            Dict with usage statistics
        """
        self._load_state()
        return {
            "project_id": self.project_id,
            "total_used": self.total_used,
            "total_limit": self.daily_limit,
            "remaining": self.daily_limit - self.total_used,
            "percentage_used": (self.total_used / self.daily_limit) * 100,
            "operations_count": len(self.operations),
            "last_reset": self.last_reset.isoformat(),
        }

    def get_warnings(self) -> list[str]:
        """
        Get quota warning messages.

        Returns:
            List of warning messages
        """
        self._load_state()
        warnings = []
        percentage = (self.total_used / self.daily_limit) * 100

        if percentage >= 80:
            warnings.append(
                f"⚠️  WARNING: {percentage:.1f}% quota used "
                f"({self.total_used}/{self.daily_limit} units)"
            )
        if percentage >= 90:
            warnings.append(
                f"⚠️  CRITICAL: {percentage:.1f}% quota used "
                f"({self.total_used}/{self.daily_limit} units). "
                f"Only {self.daily_limit - self.total_used} units remaining!"
            )

        return warnings

    def get_history(self, limit: int = 100) -> list[dict]:
        """
        Get operation history.

        Args:
            limit: Max operations to return

        Returns:
            List of operation dicts
        """
        self._load_state()
        return [asdict(op) for op in self.operations[-limit:]]

    def generate_report(self) -> dict:
        """
        Generate detailed quota usage report.

        Returns:
            Dict with comprehensive usage statistics
        """
        usage = self.get_usage()

        # Group by operation type
        by_operation = {}
        for op in self.operations:
            if op.operation not in by_operation:
                by_operation[op.operation] = {"count": 0, "total_cost": 0}
            by_operation[op.operation]["count"] += 1
            by_operation[op.operation]["total_cost"] += op.cost

        # Group by brand (if tracked)
        by_brand = {}
        for op in self.operations:
            if op.brand_id:
                if op.brand_id not in by_brand:
                    by_brand[op.brand_id] = {"count": 0, "total_cost": 0}
                by_brand[op.brand_id]["count"] += 1
                by_brand[op.brand_id]["total_cost"] += op.cost

        return {
            **usage,
            "by_operation": by_operation,
            "by_brand": by_brand,
            "warnings": self.get_warnings(),
        }

    def reset_quota(self):
        """Manually reset quota (for testing or new day)"""
        self._reset_state()

    @classmethod
    def get_project_usage(
        cls, project_id: str, storage_path: Path | None = None
    ) -> int:
        """
        Get total quota usage for a project (across all brands).

        Args:
            project_id: Google Cloud project ID
            storage_path: Storage path

        Returns:
            Total units used
        """
        storage_path = storage_path or Path("./data/quota")
        quota_file = storage_path / project_id / "quota_usage.json"

        if not quota_file.exists():
            return 0

        with open(quota_file, encoding="utf-8") as f:
            data = json.load(f)

        return data.get("total_used", 0)
