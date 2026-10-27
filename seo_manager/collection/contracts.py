"""Versioned, bounded contracts for collected evidence.

A value that was not reported is ``None`` (unknown) and stays ``None``: it is
never coerced to zero, an empty string or "now". Ages are computed on read.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from seo_manager.analytics.continuous_optimization_daemon import SyncOutcome

SCHEMA_VERSION = 1
MAX_IDENTIFIER_LEN = 200
MAX_CURSOR_LEN = 2000
MAX_PROVENANCE_LEN = 500
MAX_WINDOW = timedelta(days=366)
#: Integers beyond this lose precision in a REAL column.
MAX_EXACT_INT = 2**53


def _ident(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    if len(value) > MAX_IDENTIFIER_LEN:
        raise ValueError(f"{name} exceeds {MAX_IDENTIFIER_LEN} characters")
    return value


def _aware(name: str, value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _opt_aware(name: str, value: object) -> datetime | None:
    return None if value is None else _aware(name, value)


@dataclass(frozen=True)
class Observation:
    """One reported metric value for a subject over a window (UTC-normalised)."""

    tenant: str
    source: str
    subject: str
    metric: str
    window_start: datetime
    window_end: datetime
    #: ``None`` means unknown. It is never treated as zero.
    value: float | int | None
    reported_at: datetime
    provenance: str | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("tenant", "source", "subject", "metric"):
            _ident(name, getattr(self, name))
        start = _aware("window_start", self.window_start)
        end = _aware("window_end", self.window_end)
        if end <= start:
            raise ValueError("window_end must be after window_start")
        if end - start > MAX_WINDOW:
            raise ValueError(f"window longer than {MAX_WINDOW.days} days")
        object.__setattr__(self, "window_start", start)
        object.__setattr__(self, "window_end", end)
        object.__setattr__(self, "reported_at", _aware("reported_at", self.reported_at))
        v = self.value
        if v is not None:
            if isinstance(v, bool) or not isinstance(v, int | float):
                raise ValueError("value must be a number or None")
            if not math.isfinite(v) or abs(v) > MAX_EXACT_INT:
                raise ValueError("value must be finite and within +/-2**53")
        p = self.provenance
        if p is not None and (
            not isinstance(p, str) or not p or len(p) > MAX_PROVENANCE_LEN
        ):
            raise ValueError(
                f"provenance must be 1-{MAX_PROVENANCE_LEN} characters or None"
            )
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {self.schema_version}")

    @property
    def key(self) -> tuple[str, str, str, str, datetime, datetime]:
        return (
            self.tenant,
            self.source,
            self.subject,
            self.metric,
            self.window_start,
            self.window_end,
        )


@dataclass(frozen=True)
class Checkpoint:
    """Where a source stream resumes. The cursor is opaque."""

    tenant: str
    source: str
    stream: str
    cursor: str
    updated_at: datetime
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("tenant", "source", "stream"):
            _ident(name, getattr(self, name))
        if not isinstance(self.cursor, str) or len(self.cursor) > MAX_CURSOR_LEN:
            raise ValueError(
                f"cursor must be a string up to {MAX_CURSOR_LEN} characters"
            )
        object.__setattr__(self, "updated_at", _aware("updated_at", self.updated_at))
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {self.schema_version}")


@dataclass(frozen=True)
class SourceHealth:
    """What the last collection attempt against a source did; ``None`` = unknown."""

    tenant: str
    source: str
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_outcome: SyncOutcome | None = None
    #: e.g. ``quota_exhausted``, ``auth``, ``transient``.
    error_class: str | None = None
    next_eligible: datetime | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _ident("tenant", self.tenant)
        _ident("source", self.source)
        for name in ("last_attempt_at", "last_success_at", "next_eligible"):
            object.__setattr__(self, name, _opt_aware(name, getattr(self, name)))
        if self.last_outcome is not None and not isinstance(
            self.last_outcome, SyncOutcome
        ):
            raise ValueError("last_outcome must be a SyncOutcome or None")
        if self.error_class is not None:
            _ident("error_class", self.error_class)
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {self.schema_version}")

    def age_since_success(self, now: datetime) -> timedelta | None:
        """Computed on read, never stored; ``None`` when no success is known."""
        if self.last_success_at is None:
            return None
        return _aware("now", now) - self.last_success_at
