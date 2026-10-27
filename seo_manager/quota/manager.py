"""API quota accounting by (project, API, bucket); see docs/quota-accounting.md.

Use ``QuotaManager.reserve`` before dispatching a request and ``settle`` /
``release`` after. The older ``track_operation`` / ``check_quota`` API still
works against the shared YouTube Data pool.
"""

import json
import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from filelock import FileLock

from seo_manager.fsio import atomic_write_json

logger = logging.getLogger(__name__)

# Lock file mode: 0o664 (group-writable). See
# ``seo_manager.storage.event_store._LOCK_MODE`` for the rationale.
_LOCK_MODE = 0o664

#: Google resets daily quota at midnight Pacific time.
QUOTA_TZ = ZoneInfo("America/Los_Angeles")

STATE_VERSION = 2

YOUTUBE_DATA = "youtube_data"
DEFAULT_BUCKET = "default"


def _group_writable_filelock(lock_path: Path) -> FileLock:
    """Construct a FileLock with a group-writable mode (0o664).

    Wrapper exists so the mode is set in exactly one place across
    the codebase. Use as a context manager identically to
    ``filelock.FileLock``.
    """
    return FileLock(str(lock_path), mode=_LOCK_MODE)


class OperationCost(Enum):
    """Per-call costs (YouTube Data API v3, checked 2026-10-27).

    ``videos.insert`` / ``search.list`` are 1 call in their own buckets (the old
    1600/100 are obsolete). Equal values are Enum aliases.
    """

    VIDEO_UPLOAD = 1
    THUMBNAIL_UPLOAD = 50
    VIDEO_SEARCH = 1
    VIDEO_UPDATE = 50
    VIDEO_LIST = 1
    CHANNEL_LIST = 1
    PLAYLIST_INSERT = 50
    PLAYLIST_LIST = 1


class QuotaExceededError(Exception):
    """Raised when quota limit would be exceeded"""


class Outcome(StrEnum):
    """How a reserved request ended."""

    USED = "used"  # reached the API and succeeded: keep the charge
    FAILED = "failed"  # reached the API and failed: charged per charge_on_failure
    NOT_SENT = "not_sent"  # never left this process: refund


@dataclass(frozen=True)
class OperationSpec:
    api: str
    bucket: str
    cost: int


#: Documented defaults. ``None`` = limit unknown, so the bucket is unbudgeted.
#: Real project limits vary: override via ``QuotaManager(limits=...)``.
DEFAULT_LIMITS: dict[str, int | None] = {
    f"{YOUTUBE_DATA}/{DEFAULT_BUCKET}": 10000,
    f"{YOUTUBE_DATA}/videos.insert": 100,
    f"{YOUTUBE_DATA}/search.list": 100,
    "youtube_analytics/default": None,
    "youtube_reporting/default": None,
    "search_console/default": None,
}

OPERATIONS: dict[str, OperationSpec] = {
    "videos.insert": OperationSpec(YOUTUBE_DATA, "videos.insert", 1),
    "search.list": OperationSpec(YOUTUBE_DATA, "search.list", 1),
    "videos.update": OperationSpec(YOUTUBE_DATA, DEFAULT_BUCKET, 50),
    "videos.list": OperationSpec(YOUTUBE_DATA, DEFAULT_BUCKET, 1),
    "channels.list": OperationSpec(YOUTUBE_DATA, DEFAULT_BUCKET, 1),
    "playlistItems.list": OperationSpec(YOUTUBE_DATA, DEFAULT_BUCKET, 1),
    "playlistItems.insert": OperationSpec(YOUTUBE_DATA, DEFAULT_BUCKET, 50),
    "playlists.list": OperationSpec(YOUTUBE_DATA, DEFAULT_BUCKET, 1),
    "playlists.insert": OperationSpec(YOUTUBE_DATA, DEFAULT_BUCKET, 50),
    "thumbnails.set": OperationSpec(YOUTUBE_DATA, DEFAULT_BUCKET, 50),
}

#: Legacy operation names kept so existing callers keep resolving.
OPERATION_ALIASES: dict[str, str] = {
    "video_upload": "videos.insert",
    "thumbnail_upload": "thumbnails.set",
    "video_search": "search.list",
    "video_list": "videos.list",
    "channel_list": "channels.list",
    "playlist_insert": "playlists.insert",
}


def quota_day(now: datetime) -> str:
    """Quota day (``YYYY-MM-DD``) of an instant, in Los Angeles time."""
    return _aware(now).astimezone(QUOTA_TZ).date().isoformat()


def next_reset(now: datetime) -> datetime:
    """The next LA midnight after ``now``, as an aware UTC datetime."""
    nxt = _aware(now).astimezone(QUOTA_TZ).date() + timedelta(days=1)
    return datetime(nxt.year, nxt.month, nxt.day, tzinfo=QUOTA_TZ).astimezone(UTC)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _parse_key(key: str | tuple[str, str]) -> str:
    if isinstance(key, tuple):
        api, bucket = key
        return f"{api}/{bucket}"
    if "/" not in key:
        raise ValueError(f"quota bucket key must look like 'api/bucket': {key!r}")
    return key


@dataclass
class QuotaOperation:
    """Record of a quota-consuming operation"""

    operation: str
    cost: int
    timestamp: str
    brand_id: str | None = None
    metadata: dict | None = None
    api: str | None = None
    bucket: str | None = None
    outcome: str | None = None
    reservation_id: str | None = None


@dataclass(frozen=True)
class Reservation:
    """Handle returned by :meth:`QuotaManager.reserve`."""

    id: str
    operation: str
    api: str
    bucket: str
    cost: int
    brand_id: str | None


@dataclass
class _Bucket:
    used: int = 0
    reserved: int = 0
    charged_failures: int = 0
    brands: dict[str, int] = field(default_factory=dict)


class QuotaManager:
    """
    Quota across operations and brands, budgeted by (project, api, bucket).

    Every read-modify-write runs under one file lock. Defaults
    (``DEFAULT_LIMITS``) follow Google's docs but project limits vary: override
    them with ``limits`` (``None`` = unknown, unbudgeted). The day resets at
    midnight America/Los_Angeles.
    """

    def __init__(
        self,
        project_id: str,
        brand_id: str | None = None,
        storage_path: Path | None = None,
        daily_limit: int | None = 10000,
        *,
        limits: Mapping[str | tuple[str, str], int | None] | None = None,
        brand_allocations: Mapping[str | tuple[str, str], Mapping[str, int]]
        | None = None,
        charge_on_failure: bool = True,
        clock: Callable[[], datetime] | None = None,
    ):
        """``limits``/``brand_allocations`` are keyed ``"api/bucket"``; ``daily_limit``
        is the shared YouTube Data pool (``limits`` wins). ``clock`` returns an
        aware "now" for tests."""
        self.project_id = project_id
        self.brand_id = brand_id
        self.charge_on_failure = charge_on_failure
        self._clock = clock or (lambda: datetime.now(UTC))

        self.limits: dict[str, int | None] = dict(DEFAULT_LIMITS)
        self.limits[f"{YOUTUBE_DATA}/{DEFAULT_BUCKET}"] = daily_limit
        for key, value in (limits or {}).items():
            self.limits[_parse_key(key)] = value

        self.brand_allocations: dict[str, dict[str, int]] = {}
        for key, alloc in (brand_allocations or {}).items():
            k = _parse_key(key)
            self.brand_allocations[k] = dict(alloc)
            limit = self.limits.get(k)
            if limit is not None and sum(alloc.values()) > limit:
                raise ValueError(
                    f"brand allocations for {k} ({sum(alloc.values())}) exceed "
                    f"its limit ({limit})"
                )

        storage_path = storage_path or Path("./data/quota")
        self.storage_path = storage_path / project_id
        self.storage_path.mkdir(parents=True, exist_ok=True)

        self.quota_file = self.storage_path / "quota_usage.json"
        self.lock_file = self.storage_path / ".quota.lock"

        self._buckets: dict[str, _Bucket] = {}
        self._reservations: dict[str, dict[str, Any]] = {}
        self.operations: list[QuotaOperation] = []
        self.day = ""
        self.last_reset = self._now()

        self._load_state()

    # Legacy views of the shared YouTube Data pool

    @property
    def daily_limit(self) -> int | None:
        return self.limits[f"{YOUTUBE_DATA}/{DEFAULT_BUCKET}"]

    @property
    def total_used(self) -> int:
        return self._bucket(f"{YOUTUBE_DATA}/{DEFAULT_BUCKET}").used

    # Time

    def _now(self) -> datetime:
        return _aware(self._clock())

    def next_reset(self) -> datetime:
        """When the current quota day ends (LA midnight, aware UTC)."""
        return next_reset(self._now())

    # State

    def _bucket(self, key: str) -> _Bucket:
        return self._buckets.setdefault(key, _Bucket())

    def _load_state(self):
        """Load quota state from disk (thread-safe)"""
        with _group_writable_filelock(self.lock_file):
            self._load_state_inner()

    def _load_state_inner(self):
        """Load quota state (must be called with lock held)"""
        if not self.quota_file.exists():
            self._reset_state()
            return
        try:
            with open(self.quota_file, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("version") != STATE_VERSION:
                # State from before buckets existed: not carried over.
                logger.warning(
                    "Quota file %s is in the old format; starting with a "
                    "fresh quota state",
                    self.quota_file,
                )
                self._reset_state()
            elif data["day"] != quota_day(self._now()):
                self._reset_state()
            else:
                self._adopt_state(data)
        except (ValueError, TypeError, KeyError, AttributeError) as e:
            # A quota file that cannot be read is not a reason to refuse to
            # run: start from an empty day and say so.
            logger.warning(
                "Quota file %s is unreadable (%s); starting with a fresh quota state",
                self.quota_file,
                e,
            )
            self._reset_state()

    def _adopt_state(self, data: dict) -> None:
        self.operations = [QuotaOperation(**op) for op in data.get("operations", [])]
        self._buckets = {k: _Bucket(**b) for k, b in data["buckets"].items()}
        self._reservations = dict(data.get("reservations", {}))
        self.day = data["day"]
        self.last_reset = datetime.fromisoformat(data["last_reset"])

    def _reset_state(self):
        """Reset quota state (called from within lock)"""
        now = self._now()
        self.operations = []
        self._buckets = {}
        self._reservations = {}
        self.day = quota_day(now)
        self.last_reset = now
        self._save_state_inner()

    def _save_state(self):
        """Save quota state to disk (thread-safe)"""
        with _group_writable_filelock(self.lock_file):
            self._save_state_inner()

    def _save_state_inner(self):
        """Save quota state (must be called with lock held)"""
        data = {
            "version": STATE_VERSION,
            "project_id": self.project_id,
            "day": self.day,
            "last_reset": self.last_reset.isoformat(),
            # Legacy mirrors of the shared YouTube Data pool.
            "total_used": self.total_used,
            "daily_limit": self.daily_limit,
            "buckets": {k: asdict(b) for k, b in self._buckets.items()},
            "reservations": self._reservations,
            "operations": [asdict(op) for op in self.operations],
        }
        atomic_write_json(self.quota_file, data)

    # Resolution and admission

    def resolve(
        self,
        operation: str,
        *,
        api: str | None = None,
        bucket: str | None = None,
        cost: int | None = None,
    ) -> OperationSpec:
        """Map an operation name to (api, bucket, cost); unknown names need
        ``api`` and cost 1 call, since no unit cost is assumed."""
        spec = OPERATIONS.get(OPERATION_ALIASES.get(operation, operation))
        if spec is None:
            if api is None:
                raise ValueError(
                    f"unknown quota operation {operation!r}: pass api= (and "
                    "optionally bucket=, cost=)"
                )
            return OperationSpec(
                api, bucket or DEFAULT_BUCKET, 1 if cost is None else cost
            )
        return OperationSpec(
            api or spec.api,
            bucket or (spec.bucket if api in (None, spec.api) else DEFAULT_BUCKET),
            spec.cost if cost is None else cost,
        )

    def _check_fits(self, key: str, brand: str | None, cost: int) -> None:
        limit = self.limits.get(key)
        if limit is None:
            return
        b = self._bucket(key)
        if b.used + cost > limit:
            raise QuotaExceededError(
                f"{self.project_id}/{key} would exceed its daily limit: "
                f"{b.used + cost} > {limit} units (used {b.used})"
            )
        allocs = self.brand_allocations.get(key)
        if not allocs:
            return
        if brand is not None and brand in allocs:
            used, cap, who = b.brands.get(brand, 0), allocs[brand], f"brand {brand!r}"
        else:
            used = b.used - sum(b.brands.get(x, 0) for x in allocs)
            cap, who = limit - sum(allocs.values()), "unallocated brands"
        if used + cost > cap:
            raise QuotaExceededError(
                f"{who} would exceed their share of {self.project_id}/{key}: "
                f"{used + cost} > {cap}"
            )

    def _charge_inner(self, key: str, brand: str | None, cost: int, *, held: bool):
        b = self._bucket(key)
        b.used += cost
        b.reserved += cost if held else 0
        if brand:
            b.brands[brand] = b.brands.get(brand, 0) + cost

    def _refund_inner(self, key: str, brand: str | None, cost: int) -> None:
        b = self._bucket(key)
        b.used = max(0, b.used - cost)
        if brand in b.brands:
            b.brands[brand] = max(0, b.brands[brand] - cost)

    def _log_inner(self, operation, spec, cost, brand, metadata, outcome, rid=None):
        self.operations.append(
            QuotaOperation(
                operation,
                cost,
                self._now().isoformat(),
                brand,
                metadata,
                spec.api,
                spec.bucket,
                outcome,
                rid,
            )
        )

    # Reservation contract

    def reserve(
        self,
        operation: str,
        *,
        brand_id: str | None = None,
        api: str | None = None,
        bucket: str | None = None,
        cost: int | None = None,
        metadata: dict | None = None,
    ) -> Reservation:
        """Check and record a request's cost in one critical section.

        Call before dispatching. The cost counts as used at once; an unsettled
        reservation stays charged until the day resets. Raises
        QuotaExceededError when full, ValueError for an unknown operation
        without ``api``.
        """
        spec = self.resolve(operation, api=api, bucket=bucket, cost=cost)
        brand = brand_id if brand_id is not None else self.brand_id
        key = f"{spec.api}/{spec.bucket}"
        with _group_writable_filelock(self.lock_file):
            self._load_state_inner()
            self._check_fits(key, brand, spec.cost)
            res = Reservation(
                id=uuid.uuid4().hex,
                operation=operation,
                api=spec.api,
                bucket=spec.bucket,
                cost=spec.cost,
                brand_id=brand,
            )
            self._charge_inner(key, brand, spec.cost, held=True)
            self._reservations[res.id] = asdict(res)
            self._log_inner(
                operation, spec, spec.cost, brand, metadata, "reserved", res.id
            )
            self._save_state_inner()
        return res

    def settle(
        self,
        reservation: Reservation,
        outcome: Outcome = Outcome.USED,
        *,
        actual_cost: int | None = None,
    ) -> bool:
        """Reconcile a reservation; False if it is no longer outstanding.

        USED keeps the charge (``actual_cost`` corrects it), FAILED keeps it
        unless ``charge_on_failure`` is False, NOT_SENT refunds.
        """
        outcome = Outcome(outcome)
        with _group_writable_filelock(self.lock_file):
            self._load_state_inner()
            held = self._reservations.pop(reservation.id, None)
            if held is None:
                logger.warning(
                    "quota reservation %s is not outstanding", reservation.id
                )
                return False
            key = f"{held['api']}/{held['bucket']}"
            brand = held["brand_id"]
            b = self._bucket(key)
            b.reserved = max(0, b.reserved - held["cost"])
            refund = outcome is Outcome.NOT_SENT or (
                outcome is Outcome.FAILED and not self.charge_on_failure
            )
            kept = (
                0 if refund else (held["cost"] if actual_cost is None else actual_cost)
            )
            self._refund_inner(key, brand, held["cost"])
            if not refund:
                self._charge_inner(key, brand, kept, held=False)
            if outcome is Outcome.FAILED and not refund:
                b.charged_failures += 1
            for op in reversed(self.operations):
                if op.reservation_id == reservation.id:
                    op.outcome = outcome.value
                    op.cost = kept
                    break
            self._save_state_inner()
        return True

    def release(self, reservation: Reservation) -> bool:
        """Refund a reservation whose request was never sent."""
        return self.settle(reservation, Outcome.NOT_SENT)

    # Legacy API (shared YouTube Data pool unless the name is known)

    def track_operation(
        self,
        operation: str,
        cost: int,
        metadata: dict | None = None,
        enforce: bool = False,
    ):
        """
        Record an already-performed operation's cost.

        Known method names (``videos.insert``, ``video_upload``...) charge
        their own bucket; any other name charges the shared YouTube Data pool.

        Raises:
            QuotaExceededError: If enforce=True and the limit would be exceeded
        """
        spec = OperationSpec(YOUTUBE_DATA, DEFAULT_BUCKET, cost)
        if self._known(operation):
            spec = self.resolve(operation, cost=cost)
        key = f"{spec.api}/{spec.bucket}"
        with _group_writable_filelock(self.lock_file):
            self._load_state_inner()
            if enforce:
                self._check_fits(key, self.brand_id, cost)
            self._charge_inner(key, self.brand_id, cost, held=False)
            self._log_inner(operation, spec, cost, self.brand_id, metadata, "used")
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
        limit = self.daily_limit
        return limit is None or (self.total_used + cost) <= limit

    def can_safely_perform(self, operation_type: str) -> bool:
        """True if one call of this operation fits its own bucket."""
        if not self._known(operation_type):
            return True
        spec = self.resolve(operation_type)
        self._load_state()
        try:
            self._check_fits(f"{spec.api}/{spec.bucket}", self.brand_id, spec.cost)
        except QuotaExceededError:
            return False
        return True

    @staticmethod
    def _known(operation: str) -> bool:
        return OPERATION_ALIASES.get(operation, operation) in OPERATIONS

    def estimate_cost(self, operation_type: str, **kw: Any) -> int:
        """Units one call costs in its bucket (0 for an unknown name)."""
        if not self._known(operation_type) and "api" not in kw:
            return 0
        return self.resolve(operation_type, **kw).cost

    # Reporting

    def get_bucket_usage(self, api: str, bucket: str = DEFAULT_BUCKET) -> dict:
        """Usage of one bucket; ``limit`` is None when unbudgeted."""
        self._load_state()
        key = f"{api}/{bucket}"
        b = self._bucket(key)
        limit = self.limits.get(key)
        return {
            "limit": limit,
            "budgeted": limit is not None,
            "used": b.used,
            "reserved": b.reserved,
            "remaining": None if limit is None else limit - b.used,
            "charged_failures": b.charged_failures,
            "by_brand": dict(b.brands),
        }

    def get_usage(self) -> dict:
        """
        Get current quota usage.

        Returns:
            Dict with usage statistics
        """
        self._load_state()
        limit = self.daily_limit
        return {
            "project_id": self.project_id,
            "total_used": self.total_used,
            "total_limit": limit,
            "remaining": None if limit is None else limit - self.total_used,
            "percentage_used": (None if not limit else (self.total_used / limit) * 100),
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
        limit = self.daily_limit
        if not limit:
            return []
        warnings = []
        percentage = (self.total_used / limit) * 100
        if percentage >= 80:
            warnings.append(
                f"⚠️  WARNING: {percentage:.1f}% quota used "
                f"({self.total_used}/{limit} units)"
            )
        if percentage >= 90:
            warnings.append(
                f"⚠️  CRITICAL: {percentage:.1f}% quota used "
                f"({self.total_used}/{limit} units). "
                f"Only {limit - self.total_used} units remaining!"
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

        keys = sorted(set(self.limits) | set(self._buckets))
        buckets = {}
        for key in keys:
            api, _, bucket = key.partition("/")
            buckets[f"{self.project_id}/{key}"] = self.get_bucket_usage(api, bucket)
        return {
            **usage,
            "by_operation": by_operation,
            "by_brand": by_brand,
            "warnings": self.get_warnings(),
            "buckets": buckets,
            "unbudgeted": [k for k in keys if self.limits.get(k) is None],
        }

    def reset_quota(self):
        """Manually reset quota (for testing or new day)"""
        with _group_writable_filelock(self.lock_file):
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
