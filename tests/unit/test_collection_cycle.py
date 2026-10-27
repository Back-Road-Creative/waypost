"""The collection cycle: adapters in, one transaction per source, health out."""

from __future__ import annotations

import multiprocessing as mp
from datetime import UTC, datetime, timedelta

import pytest

from seo_manager import paths
from seo_manager.analytics.continuous_optimization_daemon import SyncOutcome
from seo_manager.collection.contracts import Checkpoint, Observation, SourceHealth
from seo_manager.collection.cycle import (
    CollectResult,
    health_after_error,
    health_after_success,
    run_cycle,
)
from seo_manager.collection.store import CollectionStore
from seo_manager.transport import (
    AuthError,
    InvalidRequestError,
    QuotaExhaustedError,
    TransientError,
    TransportError,
    UnknownTransportError,
)

NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)
T0 = datetime(2026, 10, 1, tzinfo=UTC)


def obs(value=1.0, *, source="fake", subject="v1", tenant="gms"):
    return Observation(
        tenant, source, subject, "views", T0, T0 + timedelta(days=1), value, NOW
    )


class FakeAdapter:
    stream = "main"

    def __init__(self, source="fake", rows=(), outcome=SyncOutcome.FRESH, raises=None):
        self.source = source
        self.rows = tuple(rows)
        self.outcome = outcome
        self.raises = raises
        self.calls = []

    def collect(self, tenant, checkpoint, now):
        self.calls.append(checkpoint)
        if self.raises is not None:
            raise self.raises
        cp = Checkpoint(tenant, self.source, self.stream, "c1", now)
        return CollectResult(self.rows, cp, self.outcome)


@pytest.fixture
def store(tmp_path):
    s = CollectionStore(tmp_path / "c.db")
    s.init()
    return s


def test_collected_writes_observations_checkpoint_and_health(store):
    ad = FakeAdapter(rows=[obs()])
    (run,) = run_cycle(store, "gms", [ad], now=NOW)
    assert (run.source, run.status, run.outcome, run.added) == (
        "fake", "collected", SyncOutcome.FRESH, 1,
    )  # fmt: skip
    assert run.error_class is None
    assert store.checkpoint("gms", "fake", "main").cursor == "c1"
    h = store.health("gms", "fake")
    assert h.last_success_at == NOW and h.last_outcome is SyncOutcome.FRESH
    assert len(store.latest("gms")) == 1


def test_plain_tuple_result_is_accepted(store):
    class TupleAdapter(FakeAdapter):
        def collect(self, tenant, checkpoint, now):
            return (self.rows, None, SyncOutcome.EMPTY)

    (run,) = run_cycle(store, "gms", [TupleAdapter()], now=NOW)
    assert run.status == "collected" and run.outcome is SyncOutcome.EMPTY
    assert store.health("gms", "fake").last_outcome is SyncOutcome.EMPTY


def test_adapter_receives_the_stored_checkpoint(store):
    ad = FakeAdapter(rows=[obs()])
    run_cycle(store, "gms", [ad], now=NOW)
    run_cycle(store, "gms", [ad], now=NOW + timedelta(hours=1))
    assert ad.calls[0] is None
    assert ad.calls[1].cursor == "c1"


def test_duplicate_recollect_adds_nothing(store):
    ad = FakeAdapter(rows=[obs()])
    run_cycle(store, "gms", [ad], now=NOW)
    (run,) = run_cycle(store, "gms", [ad], now=NOW + timedelta(hours=1))
    assert run.added == 0
    assert len(store.revisions("gms")) == 1


def test_partial_outcome_is_recorded(store):
    ad = FakeAdapter(rows=[obs()], outcome=SyncOutcome.PARTIAL)
    (run,) = run_cycle(store, "gms", [ad], now=NOW)
    assert run.outcome is SyncOutcome.PARTIAL
    assert store.health("gms", "fake").last_outcome is SyncOutcome.PARTIAL


def test_quota_exhausted_defers_the_next_cycle_without_calling(store):
    reset = NOW + timedelta(hours=6)
    ad = FakeAdapter(raises=QuotaExhaustedError("q", next_eligible=reset))
    (run,) = run_cycle(store, "gms", [ad], now=NOW)
    assert (run.status, run.error_class) == ("failed", "quota_exhausted")
    assert store.health("gms", "fake").next_eligible == reset
    assert store.checkpoint("gms", "fake", "main") is None
    (again,) = run_cycle(store, "gms", [ad], now=NOW + timedelta(hours=1))
    assert again.status == "deferred" and len(ad.calls) == 1
    assert store.health("gms", "fake").last_attempt_at == NOW  # no write
    (later,) = run_cycle(store, "gms", [ad], now=reset)  # eligible again
    assert later.status == "failed" and len(ad.calls) == 2


def test_failure_keeps_checkpoint_and_last_success(store):
    ok = FakeAdapter(rows=[obs()])
    run_cycle(store, "gms", [ok], now=NOW)
    bad = FakeAdapter(raises=AuthError("no"))
    later = NOW + timedelta(days=1)
    run_cycle(store, "gms", [bad], now=later)
    h = store.health("gms", "fake")
    assert h.last_success_at == NOW and h.last_attempt_at == later
    assert h.last_outcome is SyncOutcome.FAILED and h.error_class == "auth"
    assert store.checkpoint("gms", "fake", "main").cursor == "c1"
    assert len(store.revisions("gms")) == 1


def test_adapter_bug_is_recorded_and_other_adapters_still_run(store):
    bad = FakeAdapter(source="bad", raises=RuntimeError("boom"))
    good = FakeAdapter(source="good", rows=[obs(source="good")])
    runs = run_cycle(store, "gms", [bad, good], now=NOW)
    assert [(r.source, r.status) for r in runs] == [
        ("bad", "failed"), ("good", "collected"),
    ]  # fmt: skip
    assert runs[0].error_class == "adapter_error"
    assert store.health("gms", "bad").error_class == "adapter_error"
    assert store.health("gms", "bad").last_outcome is SyncOutcome.FAILED
    assert store.health("gms", "good").last_outcome is SyncOutcome.FRESH


@pytest.mark.parametrize(
    "exc,klass",
    [
        (AuthError("a"), "auth"),
        (TransientError("t"), "transient"),
        (InvalidRequestError("i"), "invalid_request"),
        (UnknownTransportError("u"), "unknown"),
        (TransportError("x"), "unknown"),
    ],
)
def test_error_class_mapping(exc, klass):
    h = health_after_error("gms", "s", exc, NOW, None)
    assert h.error_class == klass and h.next_eligible is None
    assert h.last_outcome is SyncOutcome.FAILED
    assert h.last_attempt_at == NOW and h.last_success_at is None


def test_health_after_error_carries_previous_success_and_quota_time():
    prev = SourceHealth("gms", "s", NOW, NOW - timedelta(days=2), SyncOutcome.FRESH)
    reset = NOW + timedelta(hours=3)
    h = health_after_error(
        "gms", "s", QuotaExhaustedError("q", next_eligible=reset), NOW, prev
    )
    assert h.last_success_at == prev.last_success_at and h.next_eligible == reset


def test_health_after_success_clears_errors():
    h = health_after_success("gms", "s", SyncOutcome.PARTIAL, NOW)
    assert h.last_attempt_at == h.last_success_at == NOW
    assert h.error_class is None and h.next_eligible is None
    assert h.last_outcome is SyncOutcome.PARTIAL


def test_collection_db_env_override_and_default(monkeypatch, tmp_path):
    monkeypatch.setenv("SEO_COLLECTION_DB", str(tmp_path / "x.db"))
    assert paths.collection_db() == tmp_path / "x.db"
    monkeypatch.delenv("SEO_COLLECTION_DB")
    monkeypatch.setenv("SEO_MANAGER_DATA_HOME", str(tmp_path / "home"))
    assert paths.collection_db() == tmp_path / "home" / "collection.sqlite3"
    assert not (tmp_path / "home").exists()


def test_list_health_is_tenant_scoped(store):
    store.write_batch(
        "gms", health=health_after_success("gms", "a", SyncOutcome.FRESH, NOW)
    )
    store.write_batch(
        "gms", health=health_after_success("gms", "b", SyncOutcome.EMPTY, NOW)
    )
    store.write_batch(
        "other", health=health_after_success("other", "c", SyncOutcome.FRESH, NOW)
    )
    assert [h.source for h in store.list_health("gms")] == ["a", "b"]
    assert store.list_health("nobody") == []


def _writer(db: str, subject: str, n: int) -> None:
    s = CollectionStore(db)
    for i in range(n):
        s.write_batch("gms", [obs(float(i), subject=subject)])


def test_two_processes_write_one_database(tmp_path):
    db = tmp_path / "shared.db"
    CollectionStore(db).init()
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=_writer, args=(str(db), f"p{k}", 5)) for k in (1, 2)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
        assert p.exitcode == 0
    s = CollectionStore(db)
    subjects = {o.subject for o in s.revisions("gms")}
    assert subjects == {"p1", "p2"}
    assert len(s.revisions("gms")) == 10
    assert s.projection_matches_history()
