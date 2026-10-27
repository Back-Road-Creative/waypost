"""Read-only collection of YouTube search terms per video (Analytics API v2).

Each video's top ``YT_SEARCH`` referrers (``insightTrafficSourceDetail``) become
observations. The list is the top referrers only, never the whole set, and an
unseen term is unknown, not zero. See ``docs/youtube-search-terms.md``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from seo_manager.analytics.continuous_optimization_daemon import SyncOutcome
from seo_manager.collection.contracts import (
    MAX_IDENTIFIER_LEN,
    MAX_PROVENANCE_LEN,
    Checkpoint,
    Observation,
)
from seo_manager.transport import (
    AuthError,
    InvalidRequestError,
    QuotaExhaustedError,
    TransientError,
    TransportError,
    retry_read,
)

SOURCE = "youtube_analytics_search_terms"
STREAM = "search_terms"
_ZONE = ZoneInfo("America/Los_Angeles")
_METRICS = ("views", "estimatedMinutesWatched")
_METRIC_NAMES = ("views", "estimated_minutes_watched")


def collection_window(
    now: datetime, window_days: int, settle_days: int
) -> tuple[date, date]:
    """Pacific ``(start, end)`` dates: end = today - settle_days, inclusive span."""
    end = now.astimezone(_ZONE).date() - timedelta(days=settle_days)
    return end - timedelta(days=window_days - 1), end


def _pacific_midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=_ZONE)


def _number(value: object) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value


class YouTubeSearchTermsAdapter:
    """Collects per-video search-term demand through an existing Analytics client."""

    source = SOURCE
    stream = STREAM

    def __init__(
        self,
        analytics: Any,
        video_ids: Sequence[str],
        *,
        read: Callable[[Callable[[], dict]], dict] | None = None,
        max_results: int = 25,
        window_days: int = 28,
        settle_days: int = 3,
    ) -> None:
        """``analytics`` is a built Analytics v2 resource or a ``YouTubeAnalyticsSync``."""
        inner = getattr(analytics, "youtube_analytics", None)
        if inner is not None:
            read = read or analytics._read
            analytics = inner
        if max_results < 1 or window_days < 1 or settle_days < 0:
            raise ValueError("max_results/window_days must be >= 1, settle_days >= 0")
        self._analytics = analytics
        self._video_ids = tuple(video_ids)
        self._read = read or retry_read
        self.max_results = max_results
        self.window_days = window_days
        self.settle_days = settle_days

    def collect(
        self, tenant: str, checkpoint: Checkpoint | None, now: datetime
    ) -> tuple[Sequence[Observation], Checkpoint | None, SyncOutcome]:
        """Return (observations, checkpoint advance or None, outcome). Never writes."""
        if not self._video_ids:
            return (), None, SyncOutcome.EMPTY
        start, end = collection_window(now, self.window_days, self.settle_days)
        if checkpoint is not None and checkpoint.cursor >= end.isoformat():
            return (), checkpoint, SyncOutcome.EMPTY
        observations: list[Observation] = []
        failed = 0
        last_error: TransportError | None = None
        for video_id in self._video_ids:
            try:
                response = self._read(self._query(video_id, start, end))
            except (QuotaExhaustedError, AuthError):
                raise
            except (TransientError, InvalidRequestError) as exc:
                failed += 1
                last_error = exc
                continue
            observations.extend(
                self._observations(tenant, video_id, response, start, end, now)
            )
        if failed == len(self._video_ids) and last_error is not None:
            raise last_error
        if failed:
            # Hold the checkpoint so the next run retries this window.
            return tuple(observations), None, SyncOutcome.PARTIAL
        advance = Checkpoint(tenant, SOURCE, STREAM, end.isoformat(), now)
        return tuple(observations), advance, SyncOutcome.FRESH

    def _query(self, video_id: str, start: date, end: date) -> Callable[[], dict]:
        def run() -> dict:
            return (
                self._analytics.reports()
                .query(
                    ids="channel==MINE",
                    startDate=start.isoformat(),
                    endDate=end.isoformat(),
                    metrics=",".join(_METRICS),
                    dimensions="insightTrafficSourceDetail",
                    filters=f"video=={video_id};insightTrafficSourceType==YT_SEARCH",
                    sort="-views",
                    maxResults=self.max_results,
                )
                .execute()
            )

        return run

    def _observations(
        self,
        tenant: str,
        video_id: str,
        response: dict,
        start: date,
        end: date,
        now: datetime,
    ) -> list[Observation]:
        w_start = _pacific_midnight(start)
        w_end = _pacific_midnight(end + timedelta(days=1))
        rows = [r for r in (response.get("rows") or []) if r and str(r[0]).strip()]

        def obs(subject: str, metric: str, value: Any, prov: str | None) -> Observation:
            return Observation(
                tenant, SOURCE, subject, metric, w_start, w_end, value, now, prov
            )

        out: list[Observation] = []
        for row in rows:
            term = str(row[0])
            subject, prov = f"{video_id}|{term}", None
            if len(subject) > MAX_IDENTIFIER_LEN:
                digest = hashlib.sha256(term.encode("utf-8")).hexdigest()[:32]
                subject = f"{video_id}|sha256:{digest}"
                prov = f"term={term}"[:MAX_PROVENANCE_LEN]
            for i, name in enumerate(_METRIC_NAMES, start=1):
                value = _number(row[i]) if i < len(row) else None
                out.append(obs(subject, name, value, prov))
        at_cap = len(rows) >= self.max_results
        out.append(
            obs(
                video_id,
                "search_term_rows",
                len(rows),
                f"maxResults={self.max_results};at_cap={str(at_cap).lower()}",
            )
        )
        return out
