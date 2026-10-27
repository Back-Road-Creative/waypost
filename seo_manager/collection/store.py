"""Transactional SQLite store for collected evidence.

Durability: ``synchronous=FULL``, one ``BEGIN IMMEDIATE`` transaction per
collection batch, explicit ROLLBACK on any exception, a cross-process file
lock (the cadence store's pattern). A database that cannot be read is refused
and kept; it is never replaced by an empty one. Only ``init()`` creates a file.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sqlite3
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path

from seo_manager.analytics.continuous_optimization_daemon import SyncOutcome
from seo_manager.collection.contracts import Checkpoint, Observation, SourceHealth
from seo_manager.storage.event_store import _group_writable_filelock


class CollectionStoreError(Exception):
    """Base class for store refusals."""


class StoreMissingError(CollectionStoreError):
    """The database does not exist; call ``init()`` to create it deliberately."""


class SchemaTooNewError(CollectionStoreError):
    """The database was written by newer code. No downgrade, no reset."""


class CorruptStoreError(CollectionStoreError):
    """The file is unreadable. It was kept (and copied aside when possible)."""


_M1_STATEMENTS = (
    # executescript() would COMMIT the migration transaction: one statement each.
    """CREATE TABLE observation_revisions (
            tenant TEXT NOT NULL, source TEXT NOT NULL, subject TEXT NOT NULL,
            metric TEXT NOT NULL, window_start TEXT NOT NULL, window_end TEXT NOT NULL,
            reported_at TEXT NOT NULL,
            value REAL,
            value_key TEXT NOT NULL,
            provenance TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            PRIMARY KEY (tenant, source, subject, metric, window_start, window_end,
                         reported_at, value_key, provenance)
        )""",
    """CREATE TABLE observation_latest (
            tenant TEXT NOT NULL, source TEXT NOT NULL, subject TEXT NOT NULL,
            metric TEXT NOT NULL, window_start TEXT NOT NULL, window_end TEXT NOT NULL,
            reported_at TEXT NOT NULL,
            value REAL,
            value_key TEXT NOT NULL,
            provenance TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            PRIMARY KEY (tenant, source, subject, metric, window_start, window_end)
        )""",
    """CREATE TRIGGER observation_revisions_no_update BEFORE UPDATE
            ON observation_revisions BEGIN SELECT RAISE(ABORT, 'append-only'); END""",
    """CREATE TRIGGER observation_revisions_no_delete BEFORE DELETE
            ON observation_revisions BEGIN SELECT RAISE(ABORT, 'append-only'); END""",
    """CREATE TABLE checkpoints (
            tenant TEXT NOT NULL, source TEXT NOT NULL, stream TEXT NOT NULL,
            cursor TEXT NOT NULL, updated_at TEXT NOT NULL, schema_version INTEGER NOT NULL,
            PRIMARY KEY (tenant, source, stream)
        )""",
    """CREATE TABLE source_health (
            tenant TEXT NOT NULL, source TEXT NOT NULL,
            last_attempt_at TEXT, last_success_at TEXT, last_outcome TEXT,
            error_class TEXT, next_eligible TEXT, schema_version INTEGER NOT NULL,
            PRIMARY KEY (tenant, source)
        )""",
)


def _m1(conn: sqlite3.Connection) -> None:
    for stmt in _M1_STATEMENTS:
        conn.execute(stmt)


#: Ordered migration steps; step ``i`` takes ``user_version`` i to i+1.
MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [_m1]
SCHEMA_VERSION = len(MIGRATIONS)

_KEY_COLS = "tenant, source, subject, metric, window_start, window_end"
_ROW_COLS = f"{_KEY_COLS}, reported_at, value, value_key, provenance, schema_version"
# The projection is the revision maximal by (reported_at, value_key, provenance):
# a total order, so the result is independent of arrival order.
_PICK = f"""
    SELECT {_ROW_COLS} FROM observation_revisions r
    WHERE NOT EXISTS (
        SELECT 1 FROM observation_revisions o
        WHERE o.tenant = r.tenant AND o.source = r.source AND o.subject = r.subject
          AND o.metric = r.metric AND o.window_start = r.window_start
          AND o.window_end = r.window_end
          AND (o.reported_at, o.value_key, o.provenance)
              > (r.reported_at, r.value_key, r.provenance))
"""


def _iso(dt: datetime | None) -> str | None:
    return None if dt is None else dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _dt(s: str | None) -> datetime | None:
    return (
        None
        if s is None
        else datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    )


def _value_key(v: float | int | None) -> str:
    return "" if v is None else repr(float(v))


class CollectionStore:
    """Tenant-scoped evidence store. ``path`` is required (runtime state, not tracked)."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._lock = _group_writable_filelock(
            self.path.with_name(self.path.name + ".lock")
        )
        self._verified = False

    # -- connection -------------------------------------------------------
    def _quarantine(self) -> None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        with contextlib.suppress(OSError):
            shutil.copy2(
                self.path, self.path.with_name(f"{self.path.name}.corrupt-{stamp}")
            )

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            try:
                conn.execute("PRAGMA synchronous=FULL")
                if not self._verified:
                    row = conn.execute("PRAGMA quick_check").fetchone()
                    if row is None or row[0] != "ok":
                        raise sqlite3.DatabaseError("quick_check failed")
                version = conn.execute("PRAGMA user_version").fetchone()[0]
            except sqlite3.OperationalError:
                raise  # locked / busy: not corruption
            except sqlite3.DatabaseError as exc:
                self._quarantine()
                raise CorruptStoreError(f"{self.path} is unreadable: {exc}") from exc
            if version > SCHEMA_VERSION:
                raise SchemaTooNewError(
                    f"{self.path} is schema v{version}; this code supports v{SCHEMA_VERSION}"
                )
            if version < SCHEMA_VERSION:
                self._migrate(conn)
            self._verified = True
        except BaseException:
            conn.close()
            raise
        return conn

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        conn.execute("BEGIN IMMEDIATE")
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            for step in range(version, SCHEMA_VERSION):
                MIGRATIONS[step](conn)
                conn.execute(f"PRAGMA user_version = {step + 1}")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    @contextlib.contextmanager
    def _conn(
        self, write: bool = False, create: bool = False
    ) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if not create and not self.path.exists():
                raise StoreMissingError(f"{self.path} does not exist; call init()")
            conn = self._open()
            try:
                if create:
                    with contextlib.suppress(OSError):
                        os.chmod(self.path, 0o664)
                if write:
                    conn.execute("BEGIN IMMEDIATE")
                try:
                    yield conn
                except BaseException:
                    if write:
                        conn.execute("ROLLBACK")
                    raise
                if write:
                    conn.execute("COMMIT")
            finally:
                conn.close()

    def init(self) -> None:
        """Create the database at the current schema (the only way to make a new file)."""
        with self._conn(create=True):
            pass

    # -- writes -----------------------------------------------------------
    @staticmethod
    def _insert(conn: sqlite3.Connection, obs: Observation) -> bool:
        """Add a revision; ``False`` when this exact report is already recorded."""
        params = (
            *obs.key[:4],
            _iso(obs.window_start),
            _iso(obs.window_end),
            _iso(obs.reported_at),
            None if obs.value is None else float(obs.value),
            _value_key(obs.value),
            obs.provenance or "",
            obs.schema_version,
        )
        cur = conn.execute(
            f"INSERT OR IGNORE INTO observation_revisions ({_ROW_COLS}) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            params,
        )
        if cur.rowcount:  # a new revision: re-pick this key's projection
            conn.execute(
                f"DELETE FROM observation_latest WHERE ({_KEY_COLS}) = (?,?,?,?,?,?)",
                params[:6],
            )
            conn.execute(
                f"INSERT INTO observation_latest ({_ROW_COLS}) {_PICK} "
                "AND (r.tenant, r.source, r.subject, r.metric, r.window_start, r.window_end)"
                " = (?,?,?,?,?,?)",
                params[:6],
            )
        return bool(cur.rowcount)

    def write_batch(
        self,
        tenant: str,
        observations: Iterable[Observation] = (),
        checkpoint: Checkpoint | None = None,
        health: SourceHealth | None = None,
    ) -> int:
        """Commit observations, the checkpoint advance and the health update together.

        Returns the number of new revisions. Any exception leaves the store as it was.
        """
        for item in (checkpoint, health):
            if item is not None and item.tenant != tenant:
                raise ValueError("batch item belongs to a different tenant")
        with self._conn(write=True) as conn:
            added = 0
            for obs in observations:
                if obs.tenant != tenant:
                    raise ValueError("observation belongs to a different tenant")
                added += self._insert(conn, obs)
            if checkpoint is not None:
                conn.execute(
                    "INSERT OR REPLACE INTO checkpoints VALUES (?,?,?,?,?,?)",
                    (
                        checkpoint.tenant,
                        checkpoint.source,
                        checkpoint.stream,
                        checkpoint.cursor,
                        _iso(checkpoint.updated_at),
                        checkpoint.schema_version,
                    ),
                )
            if health is not None:
                conn.execute(
                    "INSERT OR REPLACE INTO source_health VALUES (?,?,?,?,?,?,?,?)",
                    (
                        health.tenant,
                        health.source,
                        _iso(health.last_attempt_at),
                        _iso(health.last_success_at),
                        None
                        if health.last_outcome is None
                        else health.last_outcome.value,
                        health.error_class,
                        _iso(health.next_eligible),
                        health.schema_version,
                    ),
                )
        return added

    def rebuild_projections(self) -> None:
        """Recompute the latest-value projection from the revision history."""
        with self._conn(write=True) as conn:
            conn.execute("DELETE FROM observation_latest")
            conn.execute(f"INSERT INTO observation_latest ({_ROW_COLS}) {_PICK}")

    # -- reads (always tenant-scoped) ---------------------------------------
    @staticmethod
    def _obs(r: sqlite3.Row) -> Observation:
        return Observation(
            tenant=r["tenant"],
            source=r["source"],
            subject=r["subject"],
            metric=r["metric"],
            window_start=_dt(r["window_start"]),  # type: ignore[arg-type]
            window_end=_dt(r["window_end"]),  # type: ignore[arg-type]
            value=None if r["value"] is None else _num(r["value"]),
            reported_at=_dt(r["reported_at"]),  # type: ignore[arg-type]
            provenance=r["provenance"] or None,
            schema_version=r["schema_version"],
        )

    def latest(
        self, tenant: str, source: str | None = None, subject: str | None = None
    ) -> list[Observation]:
        return self._select("observation_latest", tenant, source, subject)

    def revisions(
        self, tenant: str, source: str | None = None, subject: str | None = None
    ) -> list[Observation]:
        return self._select("observation_revisions", tenant, source, subject)

    def _select(
        self, table: str, tenant: str, source: str | None, subject: str | None
    ) -> list[Observation]:
        sql = f"SELECT {_ROW_COLS} FROM {table} WHERE tenant = ?"
        args: list[str] = [tenant]
        for col, val in (("source", source), ("subject", subject)):
            if val is not None:
                sql += f" AND {col} = ?"
                args.append(val)
        sql += f" ORDER BY {_KEY_COLS}, reported_at, value_key, provenance"
        with self._conn() as conn:
            return [self._obs(r) for r in conn.execute(sql, args)]

    def checkpoint(self, tenant: str, source: str, stream: str) -> Checkpoint | None:
        with self._conn() as conn:
            r = conn.execute(
                "SELECT * FROM checkpoints WHERE tenant=? AND source=? AND stream=?",
                (tenant, source, stream),
            ).fetchone()
        if r is None:
            return None
        return Checkpoint(
            tenant,
            source,
            stream,
            r["cursor"],
            _dt(r["updated_at"]),
            r["schema_version"],  # type: ignore[arg-type]
        )

    def health(self, tenant: str, source: str) -> SourceHealth | None:
        with self._conn() as conn:
            r = conn.execute(
                "SELECT * FROM source_health WHERE tenant=? AND source=?",
                (tenant, source),
            ).fetchone()
        if r is None:
            return None
        return SourceHealth(
            tenant,
            source,
            _dt(r["last_attempt_at"]),
            _dt(r["last_success_at"]),
            None if r["last_outcome"] is None else SyncOutcome(r["last_outcome"]),
            r["error_class"],
            _dt(r["next_eligible"]),
            r["schema_version"],
        )

    def projection_matches_history(self) -> bool:
        """True when the stored projection equals one recomputed from history."""
        with self._conn() as conn:
            a = conn.execute(
                f"SELECT {_ROW_COLS} FROM observation_latest ORDER BY {_KEY_COLS}"
            ).fetchall()
            b = conn.execute(f"{_PICK} ORDER BY {_KEY_COLS}").fetchall()
        return [tuple(x) for x in a] == [tuple(x) for x in b]


def _num(v: float) -> float | int:
    return int(v) if float(v).is_integer() else v
