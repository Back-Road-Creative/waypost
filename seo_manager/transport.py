"""Typed classification of Google API failures and a bounded READ retry.

See ``docs/transport-retry.md``. Two jobs:

* ``classify(exc)`` types an ``HttpError`` (or socket fault) by HTTP status
  AND provider ``reason`` (``error.errors[].reason``); a reason beats a status.
* ``retry_read(fn)`` retries an IDEMPOTENT READ on ``TransientError`` only,
  within a fixed attempt count and total delay.

``retry_read`` must never wrap a write whose outcome may already have
happened (an upload, an insert, a thumbnail set, an update): a retry after a
timeout can duplicate or clobber. Writes get ``classify`` only, then a
reconcile step that reads the remote state back.

Classification is duck-typed on ``exc.resp.status`` / ``exc.content``.
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from datetime import time as dtime
from email.utils import parsedate_to_datetime
from typing import Any
from zoneinfo import ZoneInfo

#: The YouTube Data API daily quota resets at midnight Pacific time.
QUOTA_ZONE = ZoneInfo("America/Los_Angeles")

_QUOTA_REASONS = frozenset({"quotaExceeded", "dailyLimitExceeded"})
_RATE_REASONS = frozenset(
    {"rateLimitExceeded", "userRateLimitExceeded", "backendError", "internalError"}
)
_AUTH_REASONS = frozenset(
    {
        "forbidden",
        "insufficientPermissions",
        "authError",
        "invalidCredentials",
        "unauthorized",
        "accessNotConfigured",
    }
)


class TransportError(Exception):
    """A classified API failure; ``__cause__`` is the original."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        reason: str | None = None,
        retry_after: str | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.reason = reason
        self.retry_after = retry_after


class TransientError(TransportError):
    """Worth retrying for a READ: 5xx, 429, rate limits, connection faults."""


class QuotaExhaustedError(TransportError):
    """Daily quota spent. Never retried; wait until ``next_eligible``."""

    def __init__(self, message: str, *, next_eligible: datetime, **kw: Any):
        super().__init__(message, **kw)
        self.next_eligible = next_eligible


class AuthError(TransportError):
    """401 / 403 auth or permission. Terminal: one actionable item."""


class InvalidRequestError(TransportError):
    """Other 400 / 404. Terminal: the request itself is wrong."""


class UnknownTransportError(TransportError):
    """Anything else. Terminal: unknown is not transient."""


def next_quota_reset(now: datetime | None = None) -> datetime:
    """The next midnight America/Los_Angeles strictly after ``now``.

    Built from the Pacific calendar date, not ``now + 24h``, so a DST change
    (23- or 25-hour day) lands on the real midnight.
    """
    if now is None:
        now = datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    local = now.astimezone(QUOTA_ZONE)
    return datetime.combine(
        local.date() + timedelta(days=1), dtime(0, 0), tzinfo=QUOTA_ZONE
    )


def _reason(content: Any) -> str | None:
    if isinstance(content, bytes | bytearray):
        try:
            content = bytes(content).decode("utf-8")
        except UnicodeDecodeError:
            return None
    try:
        body = json.loads(content)
        errors = body["error"]["errors"]
        return errors[0]["reason"] or None
    except (TypeError, ValueError, KeyError, IndexError):
        return None


def _header(resp: Any, name: str) -> str | None:
    try:
        return next(
            (str(v) for k, v in dict(resp).items() if str(k).lower() == name), None
        )
    except (TypeError, ValueError):
        return None


def classify(
    exc: BaseException, *, now: datetime | None = None
) -> TransportError | None:
    """Return the typed error for ``exc``, or ``None`` if it is not transport.

    ``None`` means not transport: callers re-raise the original.
    """
    resp = getattr(exc, "resp", None)
    status = getattr(resp, "status", None)
    if isinstance(status, int) and hasattr(exc, "content"):
        reason = _reason(exc.content)
        retry_after = _header(resp, "retry-after")
        kw: dict[str, Any] = {
            "status": status,
            "reason": reason,
            "retry_after": retry_after,
        }
        msg = f"HTTP {status}" + (f" {reason}" if reason else "")
        err: TransportError
        if reason in _QUOTA_REASONS:
            err = QuotaExhaustedError(msg, next_eligible=next_quota_reset(now), **kw)
        elif reason in _RATE_REASONS or status in (408, 429) or status >= 500:
            err = TransientError(msg, **kw)
        elif status == 401 or reason in _AUTH_REASONS:
            err = AuthError(msg, **kw)
        elif status in (400, 404):
            err = InvalidRequestError(msg, **kw)
        else:
            err = UnknownTransportError(msg, **kw)
        err.__cause__ = exc
        return err
    if isinstance(exc, TimeoutError | ConnectionError):
        err = TransientError(f"{type(exc).__name__}: {exc}")
        err.__cause__ = exc
        return err
    return None


@dataclass(frozen=True)
class RetryPolicy:
    """Bounds for ``retry_read``. All delays are seconds."""

    max_attempts: int = 4
    base_delay: float = 1.0
    max_delay: float = 30.0
    max_total_delay: float = 60.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if min(self.base_delay, self.max_delay, self.max_total_delay) < 0:
            raise ValueError("delays must be >= 0")


def _retry_after_seconds(value: str | None, now: datetime) -> float | None:
    """Seconds from a ``Retry-After`` (delta-seconds or HTTP-date), else None."""
    if not value:
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - now).total_seconds()
    return seconds if seconds > 0 else None


def retry_read[T](
    fn: Callable[[], T],
    *,
    policy: RetryPolicy | None = None,
    sleep: Callable[[float], None] = time.sleep,
    rand: Callable[[], float] = random.random,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> T:
    """Call ``fn`` and retry transient failures. IDEMPOTENT READS ONLY.

    Backoff is exponential with full jitter; ``Retry-After`` replaces it,
    capped at ``max_delay``. On exhaustion the last ``TransientError`` is
    raised; terminal errors raise at once; non-transport exceptions propagate
    unchanged. ``sleep``/``rand``/``now`` are injectable.
    """
    policy = policy or RetryPolicy()
    slept = 0.0
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except Exception as exc:
            err = classify(exc, now=now())
            if err is None:
                raise
            if not isinstance(err, TransientError):
                raise err from exc
            if attempt >= policy.max_attempts:
                raise err from exc
            advised = _retry_after_seconds(err.retry_after, now())
            if advised is not None:
                delay = min(advised, policy.max_delay)
            else:
                ceiling = min(policy.max_delay, policy.base_delay * 2 ** (attempt - 1))
                delay = rand() * ceiling
            if slept + delay > policy.max_total_delay:
                raise err from exc
            sleep(delay)
            slept += delay
