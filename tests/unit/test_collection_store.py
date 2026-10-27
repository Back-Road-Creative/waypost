"""Collection contracts and the transactional SQLite store."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from seo_manager.analytics.continuous_optimization_daemon import SyncOutcome
from seo_manager.collection.contracts import Checkpoint, Observation, SourceHealth
from seo_manager.collection.store import (
    SCHEMA_VERSION,
    CollectionStore,
    CorruptStoreError,
    SchemaTooNewError,
    StoreMissingError,
)

T0 = datetime(2026, 10, 1, tzinfo=UTC)


def obs(
    value=1.0, *, tenant="gms", subject="vid1", day=0, rep=0, prov=None, metric="views"
):
    start = T0 + timedelta(days=day)
    return Observation(
        tenant, "youtube_analytics", subject, metric, start, start + timedelta(days=1),
        value, T0 + timedelta(hours=rep), prov,
    )  # fmt: skip


@pytest.fixture
def store(tmp_path):
    s = CollectionStore(tmp_path / "state" / "collection.db")
    s.init()
    return s


# -- contracts -------------------------------------------------------------
@pytest.mark.parametrize(
    "kw",
    [
        {"tenant": ""},
        {"subject": "x" * 201},
        {"value": float("nan")},
        {"value": float("inf")},
        {"value": True},
        {"value": 2**60},
        {"provenance": ""},
        {"provenance": "p" * 501},
        {"schema_version": 99},
    ],
)
def test_observation_refuses_invalid(kw):
    base = dict(
        tenant="gms", source="s", subject="v", metric="m", window_start=T0,
        window_end=T0 + timedelta(days=1), value=1, reported_at=T0,
    )  # fmt: skip
    with pytest.raises(ValueError):
        Observation(**{**base, **kw})


def test_observation_window_rules():
    with pytest.raises(ValueError):
        Observation("t", "s", "v", "m", T0, T0, 1, T0)
    with pytest.raises(ValueError):
        Observation("t", "s", "v", "m", T0, T0 + timedelta(days=400), 1, T0)
    with pytest.raises(ValueError):
        Observation(
            "t", "s", "v", "m", T0.replace(tzinfo=None), T0 + timedelta(days=1), 1, T0
        )


def test_checkpoint_and_health_validate():
    with pytest.raises(ValueError):
        Checkpoint("t", "s", "st", "c" * 2001, T0)
    with pytest.raises(ValueError):
        SourceHealth("t", "s", last_outcome="fresh")  # type: ignore[arg-type]
    h = SourceHealth("t", "s")
    assert h.last_outcome is None and h.age_since_success(T0) is None
    h = SourceHealth("t", "s", last_success_at=T0)
    assert h.age_since_success(T0 + timedelta(hours=2)) == timedelta(hours=2)


# -- reports ---------------------------------------------------------------
def test_duplicate_report_is_a_noop(store):
    assert store.write_batch("gms", [obs(5)]) == 1
    assert store.write_batch("gms", [obs(5), obs(5)]) == 0
    assert len(store.revisions("gms")) == 1
    assert [o.value for o in store.latest("gms")] == [5]


def test_revised_value_adds_revision_and_newest_wins(store):
    store.write_batch("gms", [obs(5, rep=1), obs(7, rep=2)])
    assert [o.value for o in store.revisions("gms")] == [5, 7]
    assert [o.value for o in store.latest("gms")] == [7]
    # an older report arriving late does not displace the newer one
    store.write_batch("gms", [obs(3, rep=0)])
    assert [o.value for o in store.latest("gms")] == [7]


def test_late_report_for_older_window_leaves_others(store):
    store.write_batch("gms", [obs(1, day=5)])
    store.write_batch("gms", [obs(9, day=0, rep=-24)])
    assert sorted(o.value for o in store.latest("gms")) == [1, 9]


def test_tie_on_reported_at_is_deterministic(store, tmp_path):
    a, b = obs(1, rep=1), obs(2, rep=1)
    store.write_batch("gms", [a, b])
    other = CollectionStore(tmp_path / "o.db")
    other.init()
    other.write_batch("gms", [b, a])
    assert store.latest("gms") == other.latest("gms")


def test_unknown_stays_unknown(store):
    store.write_batch("gms", [obs(None)])
    (o,) = store.latest("gms")
    assert o.value is None


# -- atomicity -------------------------------------------------------------
def test_interrupted_batch_leaves_no_partial_state(store):
    cp1 = Checkpoint("gms", "youtube_analytics", "daily", "c1", T0)
    store.write_batch("gms", [obs(1)], checkpoint=cp1)

    def boom():
        yield obs(2, subject="vid2")
        raise RuntimeError("crash mid-batch")

    cp2 = Checkpoint("gms", "youtube_analytics", "daily", "c2", T0 + timedelta(days=1))
    health = SourceHealth("gms", "youtube_analytics", last_outcome=SyncOutcome.FRESH)
    with pytest.raises(RuntimeError):
        store.write_batch("gms", boom(), checkpoint=cp2, health=health)
    assert [o.subject for o in store.revisions("gms")] == ["vid1"]
    assert store.checkpoint("gms", "youtube_analytics", "daily") == cp1
    assert store.health("gms", "youtube_analytics") is None


def test_batch_commits_checkpoint_and_health_together(store):
    cp = Checkpoint("gms", "youtube_analytics", "daily", "c1", T0)
    h = SourceHealth(
        "gms", "youtube_analytics", last_attempt_at=T0, last_outcome=SyncOutcome.FAILED,
        error_class="quota_exhausted", next_eligible=T0 + timedelta(hours=8),
    )  # fmt: skip
    store.write_batch("gms", [obs(1)], checkpoint=cp, health=h)
    assert store.checkpoint("gms", "youtube_analytics", "daily") == cp
    got = store.health("gms", "youtube_analytics")
    assert got == h and got.last_success_at is None


def test_batch_refuses_foreign_tenant(store):
    with pytest.raises(ValueError):
        store.write_batch("gms", [obs(1, tenant="other")])
    with pytest.raises(ValueError):
        store.write_batch("gms", [], health=SourceHealth("other", "s"))
    assert store.revisions("gms") == []


# -- migrations, recovery --------------------------------------------------
def test_missing_file_is_not_created_by_use(tmp_path):
    s = CollectionStore(tmp_path / "x.db")
    with pytest.raises(StoreMissingError):
        s.latest("gms")
    assert not (tmp_path / "x.db").exists()


def test_migrates_from_empty_file(tmp_path):
    p = tmp_path / "e.db"
    sqlite3.connect(p).close()  # a valid, empty, version-0 database
    s = CollectionStore(p)
    assert s.latest("gms") == []
    with sqlite3.connect(p) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    c.close()


def test_newer_version_is_refused_untouched(tmp_path):
    p = tmp_path / "n.db"
    c = sqlite3.connect(p)
    c.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    c.close()
    before = p.read_bytes()
    with pytest.raises(SchemaTooNewError):
        CollectionStore(p).write_batch("gms", [obs(1)])
    assert p.read_bytes() == before


def test_corrupt_file_is_refused_and_kept(tmp_path):
    p = tmp_path / "c.db"
    p.write_bytes(b"this is not a database" * 100)
    before = p.read_bytes()
    s = CollectionStore(p)
    with pytest.raises(CorruptStoreError):
        s.write_batch("gms", [obs(1)])
    with pytest.raises(CorruptStoreError):
        s.latest("gms")
    assert p.read_bytes() == before
    assert list(tmp_path.glob("c.db.corrupt-*"))


# -- isolation, replay -----------------------------------------------------
def test_tenant_isolation(store):
    store.write_batch("gms", [obs(1)])
    store.write_batch(
        "hm", [obs(2, tenant="hm")], checkpoint=Checkpoint("hm", "s", "d", "c", T0)
    )
    assert [o.value for o in store.latest("gms")] == [1]
    assert [o.value for o in store.revisions("hm")] == [2]
    assert store.checkpoint("gms", "s", "d") is None
    assert store.health("gms", "youtube_analytics") is None
    assert store.latest("nobody") == []


def test_rebuild_equals_stored_projection(store):
    store.write_batch(
        "gms", [obs(1, rep=1), obs(2, rep=2), obs(3, day=3), obs(None, day=4)]
    )
    before = store.latest("gms")
    assert store.projection_matches_history()
    store.rebuild_projections()
    assert store.latest("gms") == before
    assert store.projection_matches_history()


def test_revision_history_is_append_only(store):
    store.write_batch("gms", [obs(1)])
    with sqlite3.connect(store.path) as c, pytest.raises(sqlite3.DatabaseError):
        c.execute("DELETE FROM observation_revisions")
    c.close()


# -- properties ------------------------------------------------------------
_obs = st.builds(
    lambda v, d, r, s: obs(v, day=d, rep=r, subject=s),
    st.one_of(
        st.none(), st.integers(-1000, 1000), st.floats(-1e6, 1e6, allow_nan=False)
    ),
    st.integers(0, 3),
    st.integers(0, 3),
    st.sampled_from(["a", "b"]),
)


def _fresh(tmp_path_factory):
    s = CollectionStore(tmp_path_factory.mktemp("prop") / "p.db")
    s.init()
    return s


@settings(max_examples=40, deadline=None)
@given(items=st.lists(_obs, max_size=12), data=st.data())
def test_replay_any_order_with_duplicates_same_projection(
    tmp_path_factory, items, data
):
    shuffled = data.draw(st.permutations(items + items))
    a, b = _fresh(tmp_path_factory), _fresh(tmp_path_factory)
    a.write_batch("gms", items)
    for o in shuffled:  # one at a time, duplicates and all
        b.write_batch("gms", [o])
    assert a.latest("gms") == b.latest("gms")
    assert len(b.revisions("gms")) == len(a.revisions("gms")) == len(set(items))
    assert b.projection_matches_history()


@settings(max_examples=40, deadline=None)
@given(n=st.integers(1, 5), reps=st.integers(1, 4))
def test_unknown_is_never_summed_as_zero(tmp_path_factory, n, reps):
    s = _fresh(tmp_path_factory)
    batch = [obs(None, day=d, rep=r) for d in range(n) for r in range(reps)]
    s.write_batch("gms", batch)
    s.write_batch("gms", batch)
    latest = s.latest("gms")
    assert len(latest) == n
    assert all(o.value is None for o in latest)
    assert sum(o.value for o in latest if o.value is not None) == 0  # no phantom values
    assert len(s.revisions("gms")) == n * reps
