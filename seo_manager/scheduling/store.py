"""``schedule.sqlite3``: ``lf_bookings`` (date-granular long-form slots), ``ready_drives``
(audited drives waiting for a slot) and ``alerts_sent`` (push dedupe). Its own file, not the
social queue's. Writes serialise on a group-writable file lock (a host user and a container
user) plus ``BEGIN IMMEDIATE``; ``claim_drive`` books every slot and flips the drive to
``claimed`` in one such transaction."""

from __future__ import annotations

import contextlib
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Iterator

from zoneinfo import ZoneInfo

from seo_manager.core.profile import canon_region
from seo_manager.storage.event_store import _group_writable_filelock

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lf_bookings (
  project TEXT NOT NULL, lf_index INTEGER NOT NULL, region TEXT,
  slot_at TEXT NOT NULL, video_id TEXT, booked_at TEXT NOT NULL,
  PRIMARY KEY (project, lf_index));
CREATE TABLE IF NOT EXISTS ready_drives (
  project TEXT PRIMARY KEY, region TEXT NOT NULL, lf_count INTEGER NOT NULL,
  output_dir TEXT, state_path TEXT, mirror_sha TEXT, audit_log TEXT,
  created_at TEXT NOT NULL, status TEXT NOT NULL, claim_id TEXT UNIQUE,
  claimed_at TEXT, lf_mode TEXT, ingest_mode TEXT);
CREATE TABLE IF NOT EXISTS alerts_sent (
  kind TEXT NOT NULL, key TEXT NOT NULL, sent_at TEXT NOT NULL, PRIMARY KEY (kind, key));
"""
DRIVE_STATUSES = ("auditing", "ready", "claimed", "complete", "failed")
LF_MODES = ("best_moments", "full_drive")  # the drive CLI's --lf-mode choices
INGEST_MODES = ("gopro", "generic")  # the drive CLI's --ingest-mode choices
ALERT_WINDOW = timedelta(hours=24)


class SlotTaken(RuntimeError):
    """A booking would land on a date another project already holds."""


class StaleDrive(ValueError):
    """The drive's registered mirror sha is not the one the caller expected."""


@dataclass(frozen=True)
class Drive:
    project: str
    region: str
    lf_count: int
    output_dir: str | None
    state_path: str | None
    mirror_sha: str | None
    audit_log: str | None
    created_at: datetime
    status: str
    claim_id: str | None
    claimed_at: datetime | None = None
    lf_mode: str | None = (
        None  # how the audit selected LFs; the publish pass must match
    )
    ingest_mode: str | None = None


@dataclass(frozen=True)
class Booking:
    project: str
    lf_index: int
    region: str | None
    slot_at: datetime
    video_id: str | None
    booked_at: datetime


def regions_clash(
    a: str | None, b: str | None, block_cross_region: bool = False
) -> bool:
    """Same region blocks; an anonymous (``None``) side blocks everyone; others only if asked."""
    a, b = canon_region(a), canon_region(b)
    return a is None or b is None or a == b or block_cross_region


class ScheduleStore:
    """``tz`` and ``regions`` are required: build it as
    ``ScheduleStore(path, cadence.tz, cadence.regions)`` so booking dates and slot dates are
    read in one zone (the slot functions refuse a mismatch) and ``book`` refuses a region the
    cadence does not know."""

    def __init__(self, path: Path | str, tz: ZoneInfo, regions: Iterable[str]):
        self.path = Path(path)
        self.tz = tz
        self.regions = frozenset(canon_region(r) for r in regions)
        self._lock = _group_writable_filelock(
            self.path.with_name(self.path.name + ".lock")
        )
        self._ready = False

    def _local(self, dt: datetime) -> datetime:
        return (dt.replace(tzinfo=self.tz) if dt.tzinfo is None else dt).astimezone(
            self.tz
        )

    @contextlib.contextmanager
    def exclusive(self) -> Iterator[None]:
        """Hold the cross-process lock across a read-decide-write sequence (reentrant)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            yield

    @staticmethod
    def _has_table(conn, name: str) -> bool:
        return (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name = ?", (name,)
            ).fetchone()
            is not None
        )

    @contextlib.contextmanager
    def _conn(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        with self.exclusive():
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            conn.row_factory = sqlite3.Row
            try:
                # A read of an existing database never touches its schema (dry-run writes
                # nothing); the first write adds what an older file lacks.
                if not self._ready and (
                    write or not self._has_table(conn, "lf_bookings")
                ):
                    conn.executescript(_SCHEMA)
                    cols = {
                        r[1] for r in conn.execute("PRAGMA table_info(ready_drives)")
                    }
                    for col in ("claimed_at", "lf_mode", "ingest_mode"):
                        if col not in cols:
                            conn.execute(
                                f"ALTER TABLE ready_drives ADD COLUMN {col} TEXT"
                            )
                    with contextlib.suppress(OSError):
                        os.chmod(self.path, 0o664)
                    self._ready = True
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
        """Create the database and its schema (the only deliberate way to make a new file)."""
        with self._conn(write=True):
            pass

    # -- bookings ---------------------------------------------------------
    def _booking(self, r: sqlite3.Row) -> Booking:
        return Booking(
            r["project"],
            r["lf_index"],
            r["region"],
            self._local(datetime.fromisoformat(r["slot_at"])),
            r["video_id"],
            datetime.fromisoformat(r["booked_at"]),
        )

    def bookings(self, region: str | None = None) -> list[Booking]:
        sql, args = "SELECT * FROM lf_bookings", ()
        if region is not None:
            sql, args = sql + " WHERE region = ?", (canon_region(region),)
        if not self.path.exists():  # a read never creates the database
            return []
        with self._conn() as c:
            rows = c.execute(
                sql + " ORDER BY slot_at, project, lf_index", args
            ).fetchall()
        return sorted(
            (self._booking(r) for r in rows),
            key=lambda b: (b.slot_at, b.project, b.lf_index),
        )

    def _check_free(self, c, project, lf_index, region, slot, block_cross_region):
        for r in c.execute(
            "SELECT * FROM lf_bookings WHERE NOT (project = ? AND lf_index = ?)",
            (project, lf_index),
        ):
            other = self._booking(r)
            if other.slot_at.date() == slot.date() and regions_clash(
                other.region, region, block_cross_region
            ):
                raise SlotTaken(
                    f"{slot.date().isoformat()} ({region or 'any region'}) is held by "
                    f"{other.project} LF{other.lf_index}"
                )

    def book(
        self,
        project: str,
        lf_index: int,
        region: str | None,
        slot_at: datetime,
        video_id: str | None = None,
        booked_at: datetime | None = None,
        *,
        block_cross_region: bool = False,
    ) -> None:
        """Upsert one LF booking; refuse a date any other row holds (another project, or
        another LF of this one). ``region`` is stored canonical; an unknown one raises."""
        region = canon_region(region)
        if region is not None and region not in self.regions:
            raise ValueError(
                f"unknown region {region!r}; cadence has {sorted(self.regions)}"
            )
        slot = self._local(slot_at)
        with self._conn(write=True) as c:
            self._check_free(c, project, lf_index, region, slot, block_cross_region)
            c.execute(
                "INSERT OR REPLACE INTO lf_bookings VALUES (?,?,?,?,?,?)",
                (
                    project,
                    lf_index,
                    region,
                    slot.isoformat(),
                    video_id,
                    self._local(booked_at or datetime.now(self.tz)).isoformat(),
                ),
            )

    def release_unconfirmed(self, project: str) -> int:
        """Drop a project's bookings that never got a video id (a failed claim's reservation)."""
        with self._conn(write=True) as c:
            return self._release(c, project)

    @staticmethod
    def _release(c, project: str) -> int:
        return c.execute(
            "DELETE FROM lf_bookings WHERE project = ? AND video_id IS NULL", (project,)
        ).rowcount

    # -- ready drives -----------------------------------------------------
    def _region(self, region: str) -> str:
        name = canon_region(region)
        if name not in self.regions:
            raise ValueError(
                f"unknown region {region!r}; cadence has {sorted(self.regions)}"
            )
        return name

    def _drive(self, r: sqlite3.Row | None) -> Drive | None:
        if r is None:
            return None
        keys = r.keys()  # an older database has no claimed_at / modes
        return Drive(
            r["project"],
            r["region"],
            r["lf_count"],
            r["output_dir"],
            r["state_path"],
            r["mirror_sha"],
            r["audit_log"],
            datetime.fromisoformat(r["created_at"]),
            r["status"],
            r["claim_id"],
            datetime.fromisoformat(r["claimed_at"])
            if "claimed_at" in keys and r["claimed_at"]
            else None,
            r["lf_mode"] if "lf_mode" in keys else None,
            r["ingest_mode"] if "ingest_mode" in keys else None,
        )

    def _drive_rows(self, where: str = "", args: tuple = ()) -> list[Drive]:
        if not self.path.exists():  # a read never creates the database
            return []
        with self._conn() as c:
            if not self._has_table(c, "ready_drives"):  # an older file: no drives yet
                return []
            rows = c.execute(
                f"SELECT * FROM ready_drives {where} ORDER BY created_at, project", args
            ).fetchall()
        return [self._drive(r) for r in rows]

    def drive(self, project: str) -> Drive | None:
        return next(iter(self._drive_rows("WHERE project = ?", (project,))), None)

    def drive_by_claim(self, claim_id: str) -> Drive | None:
        return next(iter(self._drive_rows("WHERE claim_id = ?", (claim_id,))), None)

    def drives(self, region: str, status: str) -> list[Drive]:
        return self._drive_rows(
            "WHERE region = ? AND status = ?", (self._region(region), status)
        )

    def all_drives(self, region: str | None = None) -> list[Drive]:
        if region is None:
            return self._drive_rows()
        return self._drive_rows("WHERE region = ?", (self._region(region),))

    def ready_depth(self, region: str) -> int:
        return len(self.drives(region, "ready"))

    def register_ready(
        self,
        project: str,
        region: str,
        lf_count: int,
        *,
        output_dir: str | None = None,
        state_path: str | None = None,
        mirror_sha: str | None = None,
        audit_log: str | None = None,
        now: datetime | None = None,
        status: str = "ready",
        lf_mode: str | None = None,
        ingest_mode: str | None = None,
    ) -> None:
        """Insert or refresh a drive (a failed one becomes claimable again); a claimed or
        complete drive is never resurrected. ``lf_mode`` / ``ingest_mode`` are how the audit
        ran; the publish pass must run the same (a refresh without them keeps the stored)."""
        if lf_mode is not None and lf_mode not in LF_MODES:
            raise ValueError(f"lf_mode must be one of {LF_MODES}, got {lf_mode!r}")
        if ingest_mode is not None and ingest_mode not in INGEST_MODES:
            raise ValueError(
                f"ingest_mode must be one of {INGEST_MODES}, got {ingest_mode!r}"
            )
        if status not in ("auditing", "ready"):
            raise ValueError(f"cannot register a drive as {status!r}")
        if lf_count < 1:
            raise ValueError("lf_count must be >= 1")
        region = self._region(region)
        stamp = self._local(now or datetime.now(self.tz)).isoformat()
        with self._conn(write=True) as c:
            row = c.execute(
                "SELECT status FROM ready_drives WHERE project = ?", (project,)
            ).fetchone()
            if row and row["status"] in ("claimed", "complete"):
                raise ValueError(
                    f"{project} is already {row['status']}; refusing to re-register"
                )
            c.execute(
                "INSERT INTO ready_drives (project, region, lf_count, output_dir, state_path,"
                " mirror_sha, audit_log, created_at, status, lf_mode, ingest_mode)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(project) DO UPDATE SET region=excluded.region,"
                " lf_count=excluded.lf_count, status=excluded.status, claim_id=NULL,"
                " output_dir=COALESCE(excluded.output_dir, output_dir),"
                " state_path=COALESCE(excluded.state_path, state_path),"
                " mirror_sha=COALESCE(excluded.mirror_sha, mirror_sha),"
                " audit_log=COALESCE(excluded.audit_log, audit_log),"
                " lf_mode=COALESCE(excluded.lf_mode, lf_mode),"
                " ingest_mode=COALESCE(excluded.ingest_mode, ingest_mode)",
                (
                    project,
                    region,
                    lf_count,
                    output_dir,
                    state_path,
                    mirror_sha,
                    audit_log,
                    stamp,
                    status,
                    lf_mode,
                    ingest_mode,
                ),
            )

    def set_status(self, project: str, status: str) -> None:
        """Hand-move a drive: only ``auditing`` <-> ``ready`` and ``auditing``/``ready`` ->
        ``failed``. ``claimed`` and ``complete`` are entered and left only through
        ``claim_drive`` / ``finish_claim`` (which book and free the slots in the same
        transaction); a ``failed`` drive comes back through ``register_ready``."""
        if status not in DRIVE_STATUSES:
            raise ValueError(f"unknown drive status {status!r}")
        with self._conn(write=True) as c:
            row = c.execute(
                "SELECT status FROM ready_drives WHERE project = ?", (project,)
            ).fetchone()
            if row is None:
                raise KeyError(project)
            cur = row["status"]
            if status in ("claimed", "complete"):
                raise ValueError(
                    f"{project}: cannot set {status!r} by hand; only claim_drive "
                    "enters it and finish_claim completes it"
                )
            if cur in ("claimed", "complete"):
                raise ValueError(
                    f"{project} is {cur}; only finish_claim closes a claim "
                    "(it frees the unconfirmed slots)"
                )
            if cur == "failed" and status != "failed":
                raise ValueError(
                    f"{project} is failed; use register_ready to make it claimable again"
                )
            c.execute(
                "UPDATE ready_drives SET status = ? WHERE project = ?",
                (status, project),
            )

    def claim_drive(
        self,
        project: str,
        slots: list[datetime],
        claim_id: str,
        *,
        expect_sha: str | None = None,
        now: datetime | None = None,
        block_cross_region: bool = False,
    ) -> None:
        """Atomically (one ``BEGIN IMMEDIATE``): re-read the drive, refuse unless it is still
        ``ready`` (and still at ``expect_sha``), refuse any slot another row holds, book
        ``slots[i]`` as LF ``i+1`` with no video id, and mark the drive ``claimed``."""
        stamp = self._local(now or datetime.now(self.tz)).isoformat()
        with self._conn(write=True) as c:
            d = self._drive(
                c.execute(
                    "SELECT * FROM ready_drives WHERE project = ?", (project,)
                ).fetchone()
            )
            if d is None:
                raise KeyError(project)
            if d.status != "ready":
                raise ValueError(f"{project} is {d.status}, not ready")
            if expect_sha is not None and d.mirror_sha != expect_sha:
                raise StaleDrive(
                    f"{project} mirror sha is {d.mirror_sha!r}, expected {expect_sha!r}"
                )
            if len(slots) != d.lf_count:
                raise ValueError(
                    f"{project} needs {d.lf_count} slot(s), got {len(slots)}"
                )
            today = self._local(now or datetime.now(self.tz)).date()
            for i, slot in enumerate(map(self._local, slots), 1):
                held = c.execute(
                    "SELECT slot_at, video_id FROM lf_bookings"
                    " WHERE project = ? AND lf_index = ? AND video_id IS NOT NULL",
                    (project, i),
                ).fetchone()
                if held is not None:  # already published: never overwritten
                    if self._local(datetime.fromisoformat(held["slot_at"])) != slot:
                        raise ValueError(
                            f"{project} LF{i} is already published as {held['video_id']!r} on "
                            f"{held['slot_at']}; refusing to move it to {slot.isoformat()}"
                        )
                    continue
                if (
                    slot.date() == today
                ):  # same rule as claim(): never the claim's own day
                    raise ValueError(
                        f"{project} LF{i}: {slot.isoformat()} is the claim day; "
                        "a claim never books the base date"
                    )
                self._check_free(c, project, i, d.region, slot, block_cross_region)
                c.execute(
                    "INSERT OR REPLACE INTO lf_bookings VALUES (?,?,?,?,NULL,?)",
                    (project, i, d.region, slot.isoformat(), stamp),
                )
            c.execute(
                "UPDATE ready_drives SET status='claimed', claim_id=?, claimed_at=?"
                " WHERE project=?",
                (claim_id, stamp, project),
            )

    def finish_claim(self, claim_id: str, ok: bool) -> str:
        """Close a claim in one transaction: ``ok`` keeps the bookings (``complete``); else
        unconfirmed bookings are freed (``failed``). Repeating the same outcome is a no-op."""
        want = "complete" if ok else "failed"
        with self._conn(write=True) as c:
            d = self._drive(
                c.execute(
                    "SELECT * FROM ready_drives WHERE claim_id = ?", (claim_id,)
                ).fetchone()
            )
            if d is None:
                raise KeyError(f"unknown claim {claim_id!r}")
            if d.status == want:
                return want
            if d.status != "claimed":
                raise ValueError(f"claim {claim_id} is {d.status}, not claimed")
            if not ok:
                self._release(c, d.project)
            c.execute(
                "UPDATE ready_drives SET status = ? WHERE project = ?",
                (want, d.project),
            )
        return want

    # -- alerts -----------------------------------------------------------
    def mark_alert(self, kind: str, key: str, now: datetime | None = None) -> bool:
        """True when ``(kind, key)`` was not sent in the last 24 h -- and records it."""
        now = self._local(now or datetime.now(self.tz))
        with self._conn(write=True) as c:
            row = c.execute(
                "SELECT sent_at FROM alerts_sent WHERE kind = ? AND key = ?",
                (kind, key),
            ).fetchone()
            if row and now - datetime.fromisoformat(row["sent_at"]) < ALERT_WINDOW:
                return False
            c.execute(
                "INSERT OR REPLACE INTO alerts_sent VALUES (?,?,?)",
                (kind, key, now.isoformat()),
            )
            return True
