"""Append-only log of volume and cadence decisions (``cadence_decisions.sqlite3``).

The current value of a knob is the latest row for its ``(knob, region)``. Only
``volume_decide`` writes; readers (the publishing pipeline) call ``current`` and never
compute or default a value themselves. Rows are never updated or deleted: SQL
triggers reject both.

The store takes its path as an argument and reads no config. Reads never create
a file: a missing file or an empty table yields the seed with ``source="seed"``,
and a file that cannot be read raises ``DecisionStoreUnreadable`` (fail closed).
Writes take a group-writable file lock and then ``BEGIN IMMEDIATE``, so
concurrent deciders serialize and lose no row. A write outside the policy
floor/ceiling, or one that breaks the coupling invariant
(``shorts_per_lf <= windows * LF_PUBLISH_INTERVAL_DAYS``), is rejected.
"""

import contextlib
import json
import os
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..scheduling import shorts_times
from ..storage.event_store import _LOCK_MODE, _group_writable_filelock
from . import volume_policy as policy

# Days between long-form publishes: each shorts posting window feeds this many
# days of shorts, so ``shorts_per_lf`` cannot exceed ``windows * 7``.
LF_PUBLISH_INTERVAL_DAYS = 7

_ANY = object()
_BUSY_TIMEOUT_S = 30.0
_COLUMNS = (
    "id, knob, region, value_json, prev_json, evidence_window_start, "
    "evidence_window_end, evidence_n, need_n, floor, ceiling, reason_code, basis, "
    "status, source, policy_rev, applied_at, rollback_of, evidence_json, state_json"
)
_SCHEMA = """
CREATE TABLE IF NOT EXISTS cadence_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    knob TEXT NOT NULL,
    region TEXT,
    value_json TEXT NOT NULL,
    prev_json TEXT NOT NULL,
    evidence_window_start TEXT,
    evidence_window_end TEXT,
    evidence_n INTEGER NOT NULL DEFAULT 0,
    need_n INTEGER NOT NULL DEFAULT 0,
    floor INTEGER,
    ceiling INTEGER,
    reason_code TEXT NOT NULL,
    basis TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN
        ('ok', 'hold', 'blind', 'rollback', 'validated')),
    source TEXT NOT NULL CHECK (source IN
        ('seed', 'policy', 'rollback', 'blind_revert')),
    policy_rev INTEGER NOT NULL,
    applied_at TEXT NOT NULL,
    rollback_of INTEGER,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    state_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS cadence_decisions_key
    ON cadence_decisions (knob, region, id);
CREATE TRIGGER IF NOT EXISTS cadence_decisions_no_update
BEFORE UPDATE ON cadence_decisions
BEGIN SELECT RAISE(ABORT, 'cadence_decisions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS cadence_decisions_no_delete
BEFORE DELETE ON cadence_decisions
BEGIN SELECT RAISE(ABORT, 'cadence_decisions is append-only'); END;
"""


class DecisionStoreUnreadable(RuntimeError):
    """The decision store exists but cannot be read; callers fail closed."""


class DecisionRejected(ValueError):
    """A write that breaks a policy bound or the coupling invariant."""


@dataclass(frozen=True)
class StoredDecision:
    """One row (or the seed, ``id is None``). ``state`` is the controller's
    ``KnobState`` fields after this row, as JSON-safe values."""

    id: int | None
    knob: str
    region: str | None
    value: Any
    prev: Any
    reason_code: str
    basis: str
    status: str
    source: str
    policy_rev: int
    applied_at: str | None
    evidence_window_start: str | None = None
    evidence_window_end: str | None = None
    evidence_n: int = 0
    need_n: int = 0
    floor: int | None = None
    ceiling: int | None = None
    rollback_of: int | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NewRow:
    knob: str
    region: str | None
    value: Any
    prev: Any
    status: str
    source: str
    reason_code: str
    basis: str
    applied_at: datetime
    policy_rev: int = policy.POLICY_REV
    evidence_window: tuple[datetime, datetime] | None = None
    evidence_n: int = 0
    need_n: int = 0
    floor: int | None = None
    ceiling: int | None = None
    rollback_of: int | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)


def _dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _seed_value(knob: str, seed: Any) -> Any:
    if seed is not None:
        return seed
    if knob == "shorts_per_lf":
        return policy.SHORTS_FLOOR
    if knob == "lf_days":
        return policy.LF_DAYS_FLOOR
    return None


def _seed(knob: str, region: str | None, seed: Any) -> StoredDecision:
    v = _seed_value(knob, seed)
    return StoredDecision(
        None, knob, region, v, v, "SEED", "no decision recorded yet",
        "ok", "seed", policy.POLICY_REV, None,
    )  # fmt: skip


def _row(r: Sequence[Any]) -> StoredDecision:
    return StoredDecision(
        r[0], r[1], r[2], json.loads(r[3]), json.loads(r[4]), r[11], r[12], r[13],
        r[14], r[15], r[16], r[5], r[6], r[7], r[8], r[9], r[10], r[17],
        json.loads(r[18]), json.loads(r[19]),
    )  # fmt: skip


def _unreadable(db: Path, exc: Exception) -> DecisionStoreUnreadable:
    return DecisionStoreUnreadable(f"cannot read {db}: {exc}")


def _is_empty_file_error(exc: sqlite3.Error) -> bool:
    return "no such table" in str(exc)


def _read(db: Path, sql: str, args: Sequence[Any]) -> list[Sequence[Any]] | None:
    """Rows for a read-only query; None when there is no file or no table."""
    db = Path(db)
    if not db.exists():
        return None
    try:
        with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as conn:
            return conn.execute(sql, args).fetchall()
    except sqlite3.OperationalError as exc:
        if _is_empty_file_error(exc):
            return None
        raise _unreadable(db, exc) from exc
    except (sqlite3.DatabaseError, OSError) as exc:
        raise _unreadable(db, exc) from exc


def current(
    db: Path, knob: str, region: str | None = None, seed: Any = None
) -> StoredDecision:
    """The latest decision for ``(knob, region)``, else the seed (no file made)."""
    rows = _read(
        db,
        f"SELECT {_COLUMNS} FROM cadence_decisions "
        "WHERE knob = ? AND region IS ? ORDER BY id DESC LIMIT 1",
        (knob, region),
    )
    return _row(rows[0]) if rows else _seed(knob, region, seed)


def history(
    db: Path, knob: str | None = None, region: Any = _ANY
) -> list[StoredDecision]:
    """Rows oldest first, optionally for one knob and/or region."""
    where, args = [], []
    if knob is not None:
        where.append("knob = ?")
        args.append(knob)
    if region is not _ANY:
        where.append("region IS ?")
        args.append(region)
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    rows = _read(
        db, f"SELECT {_COLUMNS} FROM cadence_decisions{clause} ORDER BY id", args
    )
    return [_row(r) for r in rows or []]


def keys(db: Path) -> list[tuple[str, str | None]]:
    """Every ``(knob, region)`` that has a row."""
    rows = _read(
        db,
        "SELECT DISTINCT knob, region FROM cadence_decisions ORDER BY knob, region",
        (),
    )
    return [(r[0], r[1]) for r in rows or []]


def ensure_schema(db: Path) -> None:
    """Create the file and table. Writers only; readers never call this."""
    with _writer(Path(db)):
        pass


class _writer:
    """Lock, open and ``BEGIN IMMEDIATE``; commit on exit, roll back on error."""

    def __init__(self, db: Path) -> None:
        self.db = db
        self.lock: Any = None
        self.conn: sqlite3.Connection | None = None

    def __enter__(self) -> sqlite3.Connection:
        self.db.parent.mkdir(parents=True, exist_ok=True)
        self.lock = _group_writable_filelock(self.db.with_name(self.db.name + ".lock"))
        self.lock.acquire(timeout=_BUSY_TIMEOUT_S)
        try:
            self.conn = sqlite3.connect(
                self.db, timeout=_BUSY_TIMEOUT_S, isolation_level=None
            )
            self.conn.executescript(_SCHEMA)
            self.conn.execute("BEGIN IMMEDIATE")
        except sqlite3.DatabaseError as exc:
            self._release()
            raise _unreadable(self.db, exc) from exc
        except BaseException:
            self._release()
            raise
        return self.conn

    def _release(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None
        with contextlib.suppress(OSError):
            os.chmod(self.db, _LOCK_MODE)
        self.lock.release()

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            if self.conn is not None:
                self.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        except sqlite3.Error:
            pass
        finally:
            self._release()


def _latest(conn: sqlite3.Connection, knob: str, region: str | None) -> Any:
    r = conn.execute(
        "SELECT value_json FROM cadence_decisions "
        "WHERE knob = ? AND region IS ? ORDER BY id DESC LIMIT 1",
        (knob, region),
    ).fetchone()
    return json.loads(r[0]) if r else None


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _check_bounds(row: NewRow) -> None:
    if row.knob not in policy.KNOBS:
        raise DecisionRejected(f"unknown knob {row.knob!r}")
    if row.knob == "shorts_times":
        if row.value is not None and not isinstance(row.value, list):
            raise DecisionRejected("shorts_times value must be a list of windows")
        return
    lo, hi = policy.SHORTS_FLOOR, policy.SHORTS_CEILING
    if row.knob == "lf_days":
        lo, hi = policy.LF_DAYS_FLOOR, policy.LF_DAYS_CEILING_PER_REGION
    if not _is_int(row.value) or not lo <= row.value <= hi:
        raise DecisionRejected(
            f"{row.knob} value {row.value!r} outside floor/ceiling [{lo}, {hi}]"
        )


def _latest_times(conn: sqlite3.Connection) -> list[tuple[str | None, Any]]:
    """``(region, latest shorts_times value)`` of every region (and the global row)."""
    rows = conn.execute(
        "SELECT region, value_json FROM cadence_decisions WHERE id IN ("
        "SELECT MAX(id) FROM cadence_decisions WHERE knob = 'shorts_times' "
        "GROUP BY region)"
    ).fetchall()
    return [(r[0], json.loads(r[1])) for r in rows]


def _lf_day_count(conn: sqlite3.Connection, region: str | None) -> int:
    """The region's decided LF day count; 1 when no ``lf_days`` row says (the yaml
    seed is not in the store, so the nightly decider makes the full check)."""
    n = _latest(conn, "lf_days", region) if region is not None else None
    return n if _is_int(n) else 1


def _check_coupling(conn: sqlite3.Connection, row: NewRow) -> None:
    spl = _latest(conn, "shorts_per_lf", None)
    spl = policy.SHORTS_FLOOR if spl is None else spl
    try:
        if row.knob == "shorts_per_lf":
            for region, times in _latest_times(conn):
                if isinstance(times, list):
                    shorts_times.check_coupling(
                        row.value, times, _lf_day_count(conn, region)
                    )
        elif row.knob == "shorts_times" and isinstance(row.value, list):
            shorts_times.check_coupling(spl, row.value, _lf_day_count(conn, row.region))
        elif row.knob == "lf_days" and row.region is not None:
            times = _latest(conn, "shorts_times", row.region)
            if isinstance(times, list) and _is_int(row.value):
                shorts_times.check_coupling(spl, times, row.value)
    except ValueError as exc:
        raise DecisionRejected(str(exc)) from None


def _insert(conn: sqlite3.Connection, row: NewRow) -> int:
    _check_bounds(row)
    _check_coupling(conn, row)
    lo, hi = _default_bounds(row.knob)
    w = row.evidence_window
    cur = conn.execute(
        "INSERT INTO cadence_decisions (knob, region, value_json, prev_json, "
        "evidence_window_start, evidence_window_end, evidence_n, need_n, floor, "
        "ceiling, reason_code, basis, status, source, policy_rev, applied_at, "
        "rollback_of, evidence_json, state_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            row.knob, row.region, _dumps(row.value), _dumps(row.prev),
            w[0].isoformat() if w else None, w[1].isoformat() if w else None,
            row.evidence_n, row.need_n,
            row.floor if row.floor is not None else lo,
            row.ceiling if row.ceiling is not None else hi,
            row.reason_code, row.basis, row.status, row.source, row.policy_rev,
            row.applied_at.isoformat(), row.rollback_of,
            _dumps(row.evidence), _dumps(row.state),
        ),
    )  # fmt: skip
    return int(cur.lastrowid or 0)


def _default_bounds(knob: str) -> tuple[int | None, int | None]:
    if knob == "shorts_per_lf":
        return policy.SHORTS_FLOOR, policy.SHORTS_CEILING
    if knob == "lf_days":
        return policy.LF_DAYS_FLOOR, policy.LF_DAYS_CEILING_PER_REGION
    return None, None


def append_many(db: Path, rows: Sequence[NewRow]) -> list[int]:
    """Append ``rows`` in one transaction: all land, or none (and no new file
    when the first row is rejected). Returns the new ids."""
    for row in rows:  # reject the plainly invalid before any file is created
        _check_bounds(row)
    with _writer(Path(db)) as conn:
        return [_insert(conn, row) for row in rows]


def append(db: Path, **fields: Any) -> int:
    """Append one row (``NewRow`` fields as keywords); returns its id."""
    return append_many(db, [NewRow(**fields)])[0]
