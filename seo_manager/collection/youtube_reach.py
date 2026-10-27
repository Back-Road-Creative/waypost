"""Read-only ingestion of YouTube Reporting API reach reports.

Reads the reports of reporting jobs that already exist for
``channel_reach_basic_a1`` and ``channel_reach_combined_a1``; it never creates a
job (that stays the probe's ``--create-job``). With no reach job, or nothing
newer than the checkpoint, ``collect`` returns EMPTY and the checkpoint it was
given.

The two report types are separate sources, because a video appears in both and
their rows must never be summed. The ctr cell is stored exactly as reported;
its unit is unverified until a real report proves it, so its provenance says so.

Checkpoint: stream ``reach_reports``, cursor = the newest ``createTime`` handled.
A report whose download failed transiently holds the cursor back so the next
run retries it; a report that cannot be parsed is skipped for good (PARTIAL).
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from seo_manager.analytics.continuous_optimization_daemon import SyncOutcome
from seo_manager.analytics.youtube_reach_probe import REACH_METRICS, _pages
from seo_manager.collection.contracts import (
    MAX_EXACT_INT,
    MAX_IDENTIFIER_LEN,
    MAX_PROVENANCE_LEN,
    Checkpoint,
    Observation,
)
from seo_manager.transport import (
    AuthError,
    QuotaExhaustedError,
    TransientError,
    TransportError,
    retry_read,
)

SOURCE = "youtube_reach"
STREAM = "reach_reports"
BASIC_SOURCE = "youtube_reach_basic"
COMBINED_SOURCE = "youtube_reach_combined"
BASIC_TYPE = "channel_reach_basic_a1"
COMBINED_TYPE = "channel_reach_combined_a1"
_PACIFIC = ZoneInfo("America/Los_Angeles")
_BASIC_COLUMNS = ("date", "video_id", *REACH_METRICS)
_COMBINED_DIMS = (
    "traffic_source_type",
    "traffic_source_detail",
    "operating_system",
    "device_type",
)
_TYPES = {
    BASIC_TYPE: (BASIC_SOURCE, _BASIC_COLUMNS),
    COMBINED_TYPE: (COMBINED_SOURCE, (*_BASIC_COLUMNS, *_COMBINED_DIMS)),
}


def _time(text: str) -> datetime:
    when = datetime.fromisoformat(text)
    return when.replace(tzinfo=UTC) if when.tzinfo is None else when


def _number(cell: str | None) -> float | int | None:
    """The cell as reported; blank or malformed is unknown (``None``)."""
    try:
        value = float((cell or "").strip())
    except ValueError:
        return None
    if not math.isfinite(value) or abs(value) > MAX_EXACT_INT:
        return None
    return int(value) if value.is_integer() and "." not in cell else value


def _pacific_day(text: str) -> tuple[datetime, datetime] | None:
    try:
        day = date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    except ValueError:
        return None
    if len(text) != 8:
        return None
    nxt = day + timedelta(days=1)
    start = datetime(day.year, day.month, day.day, tzinfo=_PACIFIC)
    end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=_PACIFIC)
    return start.astimezone(UTC), end.astimezone(UTC)


def _subject(row: dict[str, str], combined: bool) -> tuple[str, str]:
    """(subject, extra provenance); an overlong key becomes a digest."""
    raw = row["video_id"].strip()
    if combined:
        raw = "|".join([raw, *(row[c].strip() for c in _COMBINED_DIMS)])
    if len(raw) <= MAX_IDENTIFIER_LEN:
        return raw, ""
    return f"sha256:{hashlib.sha256(raw.encode()).hexdigest()[:32]}", raw


def _provenance(report: dict, type_id: str, metric: str, raw: str) -> str:
    base = f"report={report['id']};type={type_id}"
    if metric.endswith("_ctr"):
        base = f"rate_unit=unverified;{base}"
    if raw:
        base = f"{base};subject_raw={raw}"
    return base[:MAX_PROVENANCE_LEN]


def _parse(
    text: str, report: dict, type_id: str, tenant: str
) -> list[Observation] | None:
    """Observations of one report, or ``None`` when a required column is absent."""
    source, required = _TYPES[type_id]
    reader = csv.DictReader(io.StringIO(text))
    if not set(required) <= set(reader.fieldnames or ()):
        return None
    reported_at = _time(report["createTime"])
    out: list[Observation] = []
    for row in reader:
        window = _pacific_day((row["date"] or "").strip())
        if window is None or not (row["video_id"] or "").strip():
            continue
        subject, raw = _subject(row, type_id == COMBINED_TYPE)
        for metric in REACH_METRICS:
            out.append(
                Observation(
                    tenant=tenant,
                    source=source,
                    subject=subject,
                    metric=metric,
                    window_start=window[0],
                    window_end=window[1],
                    value=_number(row[metric]),
                    reported_at=reported_at,
                    provenance=_provenance(report, type_id, metric, raw),
                )
            )
    return out


class YouTubeReachAdapter:
    """Collect reach observations from existing Reporting API jobs; writes nothing."""

    source = SOURCE
    stream = STREAM

    def __init__(
        self,
        reporting: Any,
        download: Callable[[str], str],
        *,
        read: Callable[[Callable[[], Any]], Any] = retry_read,
        max_reports: int = 30,
    ) -> None:
        self._reporting = reporting
        self._download = download
        self._read = read
        self._max = max_reports

    def _pending(self, after: datetime | None) -> list[tuple[datetime, str, dict]]:
        """Reports of reach jobs created after ``after``, oldest first."""
        reporting = self._reporting
        jobs = self._read(
            lambda: _pages(reporting.jobs().list, "jobs", includeSystemManaged=True)
        )
        found: list[tuple[datetime, str, dict]] = []
        for job in jobs:
            type_id = job.get("reportTypeId")
            if type_id not in _TYPES:
                continue
            extra = {} if after is None else {"createdAfter": _z(after)}
            reports = self._read(
                lambda job=job, extra=extra: _pages(
                    reporting.jobs().reports().list, "reports", jobId=job["id"], **extra
                )
            )
            for report in reports:
                when = _time(report["createTime"])
                if after is None or when > after:
                    found.append((when, type_id, report))
        found.sort(key=lambda item: (item[0], item[2]["id"]))
        return found

    def collect(
        self, tenant: str, checkpoint: Checkpoint | None, now: datetime
    ) -> tuple[Sequence[Observation], Checkpoint | None, SyncOutcome]:
        after = _time(checkpoint.cursor) if checkpoint and checkpoint.cursor else None
        pending = self._pending(after)
        if not pending:
            return (), checkpoint, SyncOutcome.EMPTY
        # The cap never splits one createTime: the cursor is a time, so a
        # report left behind at the cursor's own time would be skipped.
        cut = self._max
        while cut < len(pending) and pending[cut][0] == pending[cut - 1][0]:
            cut += 1
        batch, rest = pending[:cut], pending[cut:]
        observations: list[Observation] = []
        partial = bool(rest)
        done: list[datetime] = []
        # A transient failure: the cursor stays strictly before it.
        barrier: datetime | None = None
        for when, type_id, report in batch:
            try:
                text = self._read(lambda r=report: self._download(r["downloadUrl"]))
            except (QuotaExhaustedError, AuthError):
                raise
            except TransientError:
                partial = True
                barrier = when if barrier is None else min(barrier, when)
                continue
            except TransportError:
                partial = True
                text = None
            parsed = None if text is None else _parse(text, report, type_id, tenant)
            if parsed is None:
                partial = True
            else:
                observations.extend(parsed)
            done.append(when)
        safe = [t for t in done if barrier is None or t < barrier]
        cursor = max(safe) if safe else None
        outcome = SyncOutcome.PARTIAL if partial else SyncOutcome.FRESH
        if cursor is None:
            return observations, checkpoint, outcome
        return (
            observations,
            Checkpoint(tenant, SOURCE, STREAM, cursor.isoformat(), now),
            outcome,
        )


def _z(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
