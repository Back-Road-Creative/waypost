"""Title evidence: YouTube reach rows in, an ``EvidenceSnapshot`` out.

``select_title`` consumes an ``EvidenceSnapshot``; nothing built one. This
module turns collected ``youtube_reach_combined`` observations (impressions and
thumbnail click-through rate per video, day and traffic source) into per-template
rates, given a map of video id to the template and format that titled it.

Rules, all measured on live data rather than assumed:

* ``video_thumbnail_impressions_ctr`` is a fraction of that row's impressions,
  so a row's clicks are ``round(ctr * impressions)`` and a group's rate is
  ``sum(clicks) / sum(impressions)``. A ctr is never clamped; a group whose
  rate still exceeds 1 is dropped and counted, never shipped.
* Only two ``traffic_source_type`` codes are title evidence (``TRAFFIC_CODES``).
  Every other code is ignored, not an error.
* A subject the collector shortened to a digest cannot be parsed: the row is
  skipped and counted.
* A missing or ``None`` value is unknown, never zero.

Pure apart from ``title_evidence_from_store``, which only reads the store.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from seo_manager.analytics import metric_source
from seo_manager.analytics.evidence import parse_ts
from seo_manager.analytics.title_selection import (
    DEFAULT_POLICY,
    FORMATS,
    TRAFFIC_SOURCES,
    EvidenceSnapshot,
    TemplateObservation,
)
from seo_manager.collection.contracts import Observation
from seo_manager.collection.store import CollectionStore
from seo_manager.collection.youtube_reach import COMBINED_SOURCE

IMPRESSIONS_METRIC = "video_thumbnail_impressions"
CTR_METRIC = "video_thumbnail_impressions_ctr"

# YouTube Reporting API dimension doc, traffic_source_type:
# https://developers.google.com/youtube/reporting/v1/reports/dimensions
# 3 = "Browse features", 5 = "YouTube search", 24 = "Shorts" (a swipe in the
# Shorts feed, which shows no thumbnails). Only 5 and 3 are mapped; 24 and
# every other code is ignored.
TRAFFIC_CODES: Mapping[str, str] = {"5": "search", "3": "browse"}

# video_id, traffic_source_type, traffic_source_detail, operating_system, device_type
_SUBJECT_PARTS = 5


class TitleEvidenceRefused(ValueError):
    """The input is not one this module can use; ``str(exc)`` says why."""


@dataclass(frozen=True)
class TitleAttribution:
    """The template and format that titled one video."""

    template_id: str
    format: str


@dataclass(frozen=True)
class TitleEvidenceResult:
    snapshot: EvidenceSnapshot
    rows_used: int = 0
    rows_unattributed: int = 0
    rows_unparseable_subject: int = 0
    rows_other_traffic: int = 0
    rows_unknown_value: int = 0
    rows_out_of_window: int = 0
    groups_dropped_rate_over_1: int = 0


def _known(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def title_evidence(
    observations: Iterable[Observation],
    attribution: Mapping[str, TitleAttribution],
    as_of: datetime,
    *,
    max_age_days: int = DEFAULT_POLICY.max_evidence_age_days,
) -> TitleEvidenceResult:
    if as_of.tzinfo is None:
        raise TitleEvidenceRefused("as_of must be timezone-aware")
    oldest = as_of - timedelta(days=max_age_days)
    pairs: dict[tuple[str, datetime, datetime], dict[str, Any]] = {}
    for o in observations:
        if o.source != COMBINED_SOURCE or o.metric not in (
            IMPRESSIONS_METRIC,
            CTR_METRIC,
        ):
            continue
        pairs.setdefault((o.subject, o.window_start, o.window_end), {})[o.metric] = (
            o.value
        )

    counts = dict.fromkeys(
        (
            "unattributed",
            "unparseable",
            "other_traffic",
            "unknown_value",
            "out_of_window",
        ),
        0,
    )
    used = 0
    # (template_id, format, traffic) -> [clicks, impressions, start, end]
    groups: dict[tuple[str, str, str], list[Any]] = {}
    for (subject, start, end), metrics in pairs.items():
        parts = subject.split("|")
        if len(parts) != _SUBJECT_PARTS:
            counts["unparseable"] += 1
            continue
        traffic = TRAFFIC_CODES.get(parts[1])
        if traffic is None:
            counts["other_traffic"] += 1
            continue
        attr = attribution.get(parts[0])
        if attr is None:
            counts["unattributed"] += 1
            continue
        impressions = _known(metrics.get(IMPRESSIONS_METRIC))
        ctr = _known(metrics.get(CTR_METRIC))
        if impressions is None or ctr is None or impressions <= 0:
            counts["unknown_value"] += 1
            continue
        if end < oldest or end > as_of:
            counts["out_of_window"] += 1
            continue
        used += 1
        g = groups.setdefault(
            (attr.template_id, attr.format, traffic), [0, 0, start, end]
        )
        g[0] += round(ctr * impressions)
        g[1] += round(impressions)
        g[2], g[3] = min(g[2], start), max(g[3], end)

    over = 0
    out: list[TemplateObservation] = []
    for (template_id, fmt, traffic), (clicks, n, start, end) in sorted(groups.items()):
        if n <= 0:
            counts["unknown_value"] += 1
            continue
        rate = clicks / n
        if rate > 1:
            over += 1
            continue
        out.append(
            TemplateObservation(
                template_id=template_id,
                format=fmt,
                traffic_source=traffic,
                metric_source=metric_source.YOUTUBE_THUMBNAIL_IMPRESSION_CTR,
                rate=rate,
                n=n,
                window_start=start.isoformat(),
                window_end=end.isoformat(),
            )
        )
    return TitleEvidenceResult(
        snapshot=EvidenceSnapshot(as_of=as_of.isoformat(), observations=tuple(out)),
        rows_used=used,
        rows_unattributed=counts["unattributed"],
        rows_unparseable_subject=counts["unparseable"],
        rows_other_traffic=counts["other_traffic"],
        rows_unknown_value=counts["unknown_value"],
        rows_out_of_window=counts["out_of_window"],
        groups_dropped_rate_over_1=over,
    )


def title_evidence_from_store(
    store: CollectionStore,
    tenant: str,
    attribution: Mapping[str, TitleAttribution],
    as_of: datetime,
) -> TitleEvidenceResult:
    return title_evidence(
        store.latest(tenant, source=COMBINED_SOURCE), attribution, as_of
    )


_OBS_FIELDS = (
    "template_id",
    "format",
    "traffic_source",
    "metric_source",
    "rate",
    "n",
    "window_start",
    "window_end",
)


def snapshot_to_dict(snapshot: EvidenceSnapshot) -> dict[str, Any]:
    return {
        "as_of": snapshot.as_of,
        "observations": [
            {f: getattr(o, f) for f in _OBS_FIELDS} for o in snapshot.observations
        ],
    }


def _text(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise TitleEvidenceRefused(f"observation: {key} must be a non-empty string")
    return value


def _iso(row: Mapping[str, Any], key: str) -> str:
    value = _text(row, key)
    try:
        parse_ts(value)
    except ValueError as exc:
        raise TitleEvidenceRefused(f"observation: {key} is not ISO") from exc
    return value


def snapshot_from_dict(data: Any) -> EvidenceSnapshot:
    if not isinstance(data, Mapping):
        raise TitleEvidenceRefused("snapshot: not an object")
    as_of = _iso(data, "as_of")
    rows = data.get("observations")
    if not isinstance(rows, list):
        raise TitleEvidenceRefused("snapshot: observations must be a list")
    out = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise TitleEvidenceRefused("observation: not an object")
        fmt, traffic = _text(row, "format"), _text(row, "traffic_source")
        if fmt not in FORMATS or traffic not in TRAFFIC_SOURCES:
            raise TitleEvidenceRefused("observation: unknown format or traffic_source")
        rate, n = row.get("rate"), row.get("n")
        if isinstance(rate, bool) or not isinstance(rate, int | float):
            raise TitleEvidenceRefused("observation: rate must be a number")
        if isinstance(n, bool) or not isinstance(n, int):
            raise TitleEvidenceRefused("observation: n must be an integer")
        out.append(
            TemplateObservation(
                template_id=_text(row, "template_id"),
                format=fmt,
                traffic_source=traffic,
                metric_source=_text(row, "metric_source"),
                rate=float(rate),
                n=n,
                window_start=_iso(row, "window_start"),
                window_end=_iso(row, "window_end"),
            )
        )
    return EvidenceSnapshot(as_of=as_of, observations=tuple(out))
