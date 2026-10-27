"""One collection cycle: ask each adapter, record the result and the source's health.

Adapters only fetch; this module is the only writer. Per source the outcome is
one ``write_batch`` transaction: observations, checkpoint and health together on
success, health alone on failure (a failed pull never advances a checkpoint).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, NamedTuple, Protocol

from seo_manager.analytics.continuous_optimization_daemon import SyncOutcome
from seo_manager.collection.contracts import Checkpoint, Observation, SourceHealth
from seo_manager.collection.store import CollectionStore
from seo_manager.transport import (
    AuthError,
    InvalidRequestError,
    QuotaExhaustedError,
    TransientError,
    TransportError,
)

ADAPTER_ERROR = "adapter_error"


class CollectResult(NamedTuple):
    """What an adapter returns; a plain 3-tuple in the same order is accepted too."""

    observations: Sequence[Observation]
    #: The checkpoint advance, or ``None`` to leave it where it is.
    checkpoint: Checkpoint | None
    #: FRESH (complete), PARTIAL (a sub-request failed or a cap was hit) or EMPTY.
    outcome: SyncOutcome


class SourceAdapter(Protocol):
    """A source of observations. A whole-source failure raises a ``TransportError``."""

    source: str
    #: The checkpoint stream the cycle hands back to ``collect``.
    stream: str

    def collect(
        self, tenant: str, checkpoint: Checkpoint | None, now: datetime
    ) -> (
        CollectResult | tuple[Sequence[Observation], Checkpoint | None, SyncOutcome]
    ): ...


@dataclass(frozen=True)
class SourceRun:
    """What the cycle did for one source."""

    source: str
    status: Literal["collected", "deferred", "failed"]
    outcome: SyncOutcome | None = None
    added: int = 0
    error_class: str | None = None


def _error_class(exc: BaseException) -> str:
    if isinstance(exc, QuotaExhaustedError):
        return "quota_exhausted"
    if isinstance(exc, AuthError):
        return "auth"
    if isinstance(exc, TransientError):
        return "transient"
    if isinstance(exc, InvalidRequestError):
        return "invalid_request"
    if isinstance(exc, TransportError):
        return "unknown"
    return ADAPTER_ERROR


def health_after_error(
    tenant: str,
    source: str,
    exc: BaseException,
    now: datetime,
    previous: SourceHealth | None,
) -> SourceHealth:
    """Health after a failed attempt; the last success is carried, never invented."""
    return SourceHealth(
        tenant,
        source,
        last_attempt_at=now,
        last_success_at=None if previous is None else previous.last_success_at,
        last_outcome=SyncOutcome.FAILED,
        error_class=_error_class(exc),
        next_eligible=exc.next_eligible
        if isinstance(exc, QuotaExhaustedError)
        else None,
    )


def health_after_success(
    tenant: str, source: str, outcome: SyncOutcome, now: datetime
) -> SourceHealth:
    """Health after the adapter returned; errors and deferrals are cleared."""
    return SourceHealth(
        tenant,
        source,
        last_attempt_at=now,
        last_success_at=now,
        last_outcome=outcome,
    )


def _run_one(
    store: CollectionStore,
    tenant: str,
    adapter: SourceAdapter,
    now: datetime,
    previous: SourceHealth | None,
) -> SourceRun:
    source = adapter.source
    if previous is not None and previous.next_eligible and previous.next_eligible > now:
        return SourceRun(source, "deferred", error_class=previous.error_class)
    try:
        observations, checkpoint, outcome = adapter.collect(
            tenant, store.checkpoint(tenant, source, adapter.stream), now
        )
        health = health_after_success(tenant, source, outcome, now)
        added = store.write_batch(tenant, observations, checkpoint, health)
    except Exception as exc:  # one adapter must never stop the others
        failed = health_after_error(tenant, source, exc, now, previous)
        store.write_batch(tenant, health=failed)
        return SourceRun(source, "failed", SyncOutcome.FAILED, 0, failed.error_class)
    return SourceRun(source, "collected", outcome, added)


def run_cycle(
    store: CollectionStore,
    tenant: str,
    adapters: Sequence[SourceAdapter],
    *,
    now: datetime,
) -> list[SourceRun]:
    """Collect from each adapter in turn; failures are recorded, not raised."""
    return [
        _run_one(store, tenant, a, now, store.health(tenant, a.source))
        for a in adapters
    ]
