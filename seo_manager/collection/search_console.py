"""Owned-site Search Console evidence, read-only.

The caller supplies an authorized ``searchconsole`` v1 resource; this module
only calls ``searchanalytics().query(...)`` and never writes the store.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from seo_manager.analytics.continuous_optimization_daemon import SyncOutcome
from seo_manager.collection.contracts import (
    MAX_IDENTIFIER_LEN,
    MAX_PROVENANCE_LEN,
    Checkpoint,
    Observation,
)
from seo_manager.transport import TransientError, retry_read

SOURCE = "search_console"
STREAM = "search_analytics"
METRICS = ("clicks", "impressions", "ctr", "position")
SEARCH_TYPES = frozenset({"web", "image", "video", "news", "discover", "googleNews"})
DIMENSIONS = frozenset(
    {"date", "query", "page", "country", "device", "searchAppearance"}
)
DATA_STATES = frozenset({"final", "all"})
#: Types the API does not allow the ``query`` dimension for.
NO_QUERY_TYPES = frozenset({"discover", "googleNews"})
MAX_ROW_LIMIT = 25000
_TAGS = {
    "query": "q",
    "page": "p",
    "country": "c",
    "device": "d",
    "searchAppearance": "a",
}


def validate_request(
    search_types: Sequence[str], dimensions: Sequence[str], data_state: str
) -> None:
    """Refuse combinations the API rejects, before any call is made."""
    if not search_types or len(set(search_types)) != len(search_types):
        raise ValueError("search_types must be non-empty and unique")
    if bad := [t for t in search_types if t not in SEARCH_TYPES]:
        raise ValueError(f"unsupported search type: {bad[0]}")
    if not dimensions or len(set(dimensions)) != len(dimensions):
        raise ValueError("dimensions must be non-empty and unique")
    if bad := [d for d in dimensions if d not in DIMENSIONS]:
        raise ValueError(f"unsupported dimension: {bad[0]}")
    if "searchAppearance" in dimensions and len(dimensions) > 1:
        raise ValueError("searchAppearance cannot be combined with other dimensions")
    if "query" in dimensions and (nq := NO_QUERY_TYPES & set(search_types)):
        raise ValueError(f"type {sorted(nq)[0]} does not support the query dimension")
    if data_state not in DATA_STATES:
        raise ValueError(f"data_state must be one of {sorted(DATA_STATES)}")


def _midnight(day: date, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC)


class SearchConsoleAdapter:
    """Collects clicks, impressions, CTR and position for one verified property.

    Dates are the property's reporting days: Search Console reports in Pacific
    time, so ``report_zone`` defaults to ``America/Los_Angeles``. The window is
    ``[today - settle_days - window_days + 1, today - settle_days]`` in that zone.
    The API returns top rows only: a query that is absent is not zero demand.
    """

    source = SOURCE
    stream = STREAM

    def __init__(
        self,
        searchconsole: Any,
        site_url: str,
        *,
        search_types: Sequence[str] = ("web",),
        dimensions: Sequence[str] = ("date", "query", "page"),
        data_state: str = "final",
        row_limit: int = MAX_ROW_LIMIT,
        max_pages: int = 4,
        window_days: int = 28,
        settle_days: int = 3,
        report_zone: str = "America/Los_Angeles",
        read: Callable[[Callable[[], Any]], Any] = retry_read,
    ) -> None:
        validate_request(search_types, dimensions, data_state)
        if not 1 <= row_limit <= MAX_ROW_LIMIT:
            raise ValueError(f"row_limit must be 1..{MAX_ROW_LIMIT}")
        if max_pages < 1 or window_days < 1 or settle_days < 0:
            raise ValueError("max_pages/window_days must be >= 1, settle_days >= 0")
        self._svc = searchconsole
        self._site = site_url
        self._types = tuple(search_types)
        self._dims = tuple(dimensions)
        self._state = data_state
        self._row_limit = row_limit
        self._max_pages = max_pages
        self._window_days = window_days
        self._settle_days = settle_days
        self._zone = ZoneInfo(report_zone)
        self._read = read

    # -- window --------------------------------------------------------
    def _target_end(self, now: datetime) -> date:
        return now.astimezone(self._zone).date() - timedelta(days=self._settle_days)

    # -- collection ----------------------------------------------------
    def collect(
        self, tenant: str, checkpoint: Checkpoint | None, now: datetime
    ) -> tuple[Sequence[Observation], Checkpoint | None, SyncOutcome]:
        end = self._target_end(now)
        if checkpoint is not None and checkpoint.cursor >= end.isoformat():
            return (), checkpoint, SyncOutcome.EMPTY
        start = end - timedelta(days=self._window_days - 1)
        out: list[Observation] = []
        partial = False
        failures: list[TransientError] = []
        for stype in self._types:
            try:
                rows, meta, capped = self._fetch_type(stype, start, end)
            except TransientError as exc:
                failures.append(exc)
                partial = True
                continue
            partial = partial or capped
            out.extend(
                self._observations(tenant, stype, rows, meta, capped, start, end, now)
            )
        if failures and len(failures) == len(self._types):
            raise failures[-1]
        if failures:
            # Hold the cursor so the next run retries the types that failed.
            return out, checkpoint, SyncOutcome.PARTIAL
        # A cap alone advances: a retry would return the same truncated rows.
        new_cp = Checkpoint(tenant, SOURCE, STREAM, end.isoformat(), now)
        if partial:
            return out, new_cp, SyncOutcome.PARTIAL
        return out, new_cp, SyncOutcome.FRESH if out else SyncOutcome.EMPTY

    def _fetch_type(
        self, stype: str, start: date, end: date
    ) -> tuple[list[dict[str, Any]], dict[str, Any], bool]:
        rows: list[dict[str, Any]] = []
        meta: dict[str, Any] = {}
        for page in range(self._max_pages):
            body = {
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "dimensions": list(self._dims),
                "type": stype,
                "dataState": self._state,
                "rowLimit": self._row_limit,
                "startRow": page * self._row_limit,
            }
            resp = self._read(lambda b=body: self._query(b)) or {}
            batch = resp.get("rows") or []
            meta = meta or resp.get("metadata") or {}
            rows.extend(batch)
            if len(batch) < self._row_limit:
                return rows, meta, False
        return rows, meta, True

    def _query(self, body: dict[str, Any]) -> Any:
        return (
            self._svc.searchanalytics().query(siteUrl=self._site, body=body).execute()
        )

    # -- mapping -------------------------------------------------------
    def _first_incomplete(self, meta: dict[str, Any]) -> date | None:
        if self._state != "all":
            return None
        found: list[date] = []
        for key in ("first_incomplete_date", "first_incomplete_hour"):
            raw = meta.get(key)
            if isinstance(raw, str) and len(raw) >= 10:
                found.append(date.fromisoformat(raw[:10]))
        return min(found) if found else None

    def _subject(self, stype: str, keys: list[str]) -> tuple[str, str | None]:
        tag = "".join(_TAGS[d] for d in self._dims if d != "date") or "site"
        vals = [k for d, k in zip(self._dims, keys, strict=False) if d != "date"]
        subject = "|".join([stype, tag, *vals]) if vals else f"{stype}|{tag}"
        if len(subject) <= MAX_IDENTIFIER_LEN:
            return subject, None
        digest = hashlib.sha256(subject.encode()).hexdigest()[:32]
        return f"{stype}|sha256:{digest}", subject

    def _observations(
        self,
        tenant: str,
        stype: str,
        rows: list[dict[str, Any]],
        meta: dict[str, Any],
        capped: bool,
        start: date,
        end: date,
        now: datetime,
    ) -> list[Observation]:
        first_bad = self._first_incomplete(meta)
        out: list[Observation] = []
        for row in rows:
            keys = [str(k) for k in row.get("keys") or []]
            if len(keys) != len(self._dims):
                continue
            if "date" in self._dims:
                day = date.fromisoformat(keys[self._dims.index("date")])
                w0, w1 = day, day
            else:
                w0, w1 = start, end
            ws, we = (
                _midnight(w0, self._zone),
                _midnight(w1 + timedelta(days=1), self._zone),
            )
            subject, raw = self._subject(stype, keys)
            flags = ["incomplete=true"] if first_bad and w1 >= first_bad else []
            if capped:
                flags.append("page_cap_hit=true")
            prov = self._provenance(stype, flags, raw)
            for metric in METRICS:
                v = row.get(metric)
                out.append(
                    Observation(
                        tenant,
                        SOURCE,
                        subject,
                        metric,
                        ws,
                        we,
                        v if isinstance(v, int | float) else None,
                        now,
                        prov,
                    )
                )
        return out

    def _provenance(self, stype: str, flags: list[str], raw: str | None) -> str:
        parts = [
            f"type={stype}",
            f"dataState={self._state}",
            f"dims={','.join(self._dims)}",
        ]
        text = ";".join(parts + flags)
        if raw is not None:
            room = MAX_PROVENANCE_LEN - len(text) - len(";keys=")
            if room > 0:
                text += ";keys=" + raw[:room]
        return text
