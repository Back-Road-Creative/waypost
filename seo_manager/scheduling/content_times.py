"""Pure account-scoped timing advice; never reserves a slot or authorizes posting.

Inputs must come from a trusted account-aware analytics adapter. The older
``ShortRecord`` has no account ID and is deliberately not accepted here.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal, TypedDict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from seo_manager.analytics.evidence import REACH_METRIC
from seo_manager.analytics.volume_policy import BLIND_DAYS, PROBATION_MIN_N, SETTLE_DAYS
from seo_manager.scheduling.shorts_times import rank_hours

PRODUCER_VERSION = "account-content-times/1"
MIN_SAMPLES = PROBATION_MIN_N
# Explicit producer policy, not claimed user preferences or learned thresholds.
MAX_POST_AGE = timedelta(days=90)
MAX_EVIDENCE_AGE = timedelta(days=BLIND_DAYS)
HORIZON_DAYS = 7
MAX_OBSERVATIONS = 10_000
MAX_OCCUPIED = 10_000
SUPPORTED = frozenset(
    {
        ("facebook", "video"),
        ("facebook", "reel"),
        ("youtube", "short"),
        ("youtube", "video"),
    }
)

Reason = Literal[
    "invalid_request",
    "unsupported_content",
    "no_evidence",
    "invalid_evidence",
    "incompatible_evidence",
    "duplicate_evidence",
    "future_evidence",
    "stale_evidence",
    "unsettled_evidence",
    "insufficient_evidence",
    "no_free_slot",
]


@dataclass(frozen=True)
class Observation:
    post_id: str
    platform: str
    account_id: str
    content_format: str
    metric: str
    posted_at: datetime
    observed_at: datetime
    day7_value: float


@dataclass(frozen=True)
class Request:
    platform: str
    account_id: str
    content_format: str
    timezone: str
    now: datetime
    observations: tuple[Observation, ...]
    occupied_slots: tuple[datetime, ...]


@dataclass(frozen=True)
class Available:
    proposed_at: datetime
    platform: str
    account_id: str
    content_format: str
    timezone: str
    evidence_sha256: str
    sample_count: int
    evidence_age_seconds: int
    producer_version: str = PRODUCER_VERSION
    basis: Literal["measured_account_history"] = "measured_account_history"
    reason: Literal["best_measured_hour"] = "best_measured_hour"


@dataclass(frozen=True)
class Unavailable:
    reason: Reason
    producer_version: str = PRODUCER_VERSION
    data_requirement: str = (
        "Provide fresh, settled account-scoped observations with matching "
        "platform, content format and day-seven metric."
    )


class _Refused(ValueError):
    def __init__(self, reason: Reason):
        self.reason = reason
        super().__init__(reason)


def _need(condition: bool, reason: Reason) -> None:
    if not condition:
        raise _Refused(reason)


def _text(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 200
        and value == value.strip()
        and all(ord(c) >= 32 for c in value)
    )


def _instant(value: datetime, reason: Reason) -> datetime:
    _need(isinstance(value, datetime) and value.utcoffset() is not None, reason)
    result = value.astimezone(UTC)
    # ZoneInfo allows constructing imaginary spring-forward wall times.
    _need(result.astimezone(value.tzinfo) == value, reason)
    return result


@dataclass(frozen=True)
class _Score:
    weekday: int
    hour_local: int
    day7_views: float


class _Normalized(TypedDict):
    post_id: str
    posted_at: str
    observed_at: str
    day7_value: float


def _evidence(
    req: Request, zone: ZoneInfo, now: datetime
) -> tuple[list[_Score], str, list[tuple[int, datetime]]]:
    metric = REACH_METRIC[(req.platform, req.content_format)]
    seen: set[str] = set()
    scores: list[_Score] = []
    normalized: list[_Normalized] = []
    observed_times: list[tuple[int, datetime]] = []
    for row in req.observations:
        _need(isinstance(row, Observation), "invalid_evidence")
        _need(_text(row.post_id), "invalid_evidence")
        _need(
            (row.platform, row.account_id, row.content_format, row.metric)
            == (req.platform, req.account_id, req.content_format, metric),
            "incompatible_evidence",
        )
        _need(row.post_id not in seen, "duplicate_evidence")
        seen.add(row.post_id)
        _need(
            type(row.day7_value) in (int, float)
            and math.isfinite(row.day7_value)
            and row.day7_value >= 0,
            "invalid_evidence",
        )
        posted = _instant(row.posted_at, "invalid_evidence")
        observed = _instant(row.observed_at, "invalid_evidence")
        _need(posted <= now and observed <= now, "future_evidence")
        _need(
            now - posted <= MAX_POST_AGE and now - observed <= MAX_EVIDENCE_AGE,
            "stale_evidence",
        )
        _need(observed - posted >= timedelta(days=SETTLE_DAYS), "unsettled_evidence")
        local = posted.astimezone(zone)
        scores.append(_Score(local.weekday(), local.hour, float(row.day7_value)))
        observed_times.append((local.hour, observed))
        normalized.append(
            dict(
                post_id=row.post_id,
                posted_at=posted.isoformat(),
                observed_at=observed.isoformat(),
                day7_value=float(row.day7_value),
            )
        )
    document = dict(
        producer_version=PRODUCER_VERSION,
        platform=req.platform,
        account_id=req.account_id,
        content_format=req.content_format,
        timezone=req.timezone,
        metric=metric,
        observations=sorted(normalized, key=lambda row: row["post_id"]),
    )
    raw = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return scores, digest, observed_times


def _candidate(day: date, hour: int, zone: ZoneInfo) -> datetime | None:
    wall = datetime.combine(day, time(hour))
    first = wall.replace(tzinfo=zone, fold=0)
    second = wall.replace(tzinfo=zone, fold=1)
    # Reject both repeated and imaginary local hours. No guessed DST fold.
    if first.utcoffset() != second.utcoffset():
        return None
    utc = first.astimezone(UTC)
    return utc if utc.astimezone(zone).replace(tzinfo=None) == wall else None


def recommend_time(req: Request) -> Available | Unavailable:
    """Choose the best measured hour's next free instant in seven local dates.

    Ranked hours are tried best-first, then dates earliest-first. Occupancy is exact
    instant equality, not an invented spacing rule. All evidence is checked before
    scoring; one incompatible or stale record refuses the entire request. Malformed
    input returns a typed unavailable reason. No I/O, current clock or randomness.
    """
    try:
        _need(isinstance(req, Request), "invalid_request")
        _need(
            req.platform in ("facebook", "youtube")
            and _text(req.account_id)
            and _text(req.content_format)
            and _text(req.timezone),
            "invalid_request",
        )
        now = _instant(req.now, "invalid_request")
        zone = ZoneInfo(req.timezone)
        _need(
            isinstance(req.observations, tuple)
            and len(req.observations) <= MAX_OBSERVATIONS
            and isinstance(req.occupied_slots, tuple)
            and len(req.occupied_slots) <= MAX_OCCUPIED,
            "invalid_request",
        )
        occupied = {_instant(slot, "invalid_request") for slot in req.occupied_slots}
        _need((req.platform, req.content_format) in SUPPORTED, "unsupported_content")
        _need(bool(req.observations), "no_evidence")
        scores, digest, observed = _evidence(req, zone, now)
        ranked = rank_hours(scores, need=MIN_SAMPLES)
        _need(bool(ranked), "insufficient_evidence")
        today = now.astimezone(zone).date()
        for hour in ranked:
            for offset in range(HORIZON_DAYS):
                candidate = _candidate(today + timedelta(days=offset), hour.hour, zone)
                if candidate is None or candidate <= now or candidate in occupied:
                    continue
                return Available(
                    candidate,
                    req.platform,
                    req.account_id,
                    req.content_format,
                    req.timezone,
                    digest,
                    hour.n,
                    int(
                        (
                            now - min(t for h, t in observed if h == hour.hour)
                        ).total_seconds()
                    ),
                )
        return Unavailable("no_free_slot")
    except _Refused as exc:
        if exc.reason == "unsupported_content":
            return Unavailable(
                exc.reason,
                data_requirement=(
                    "A validated account-scoped day-seven metric contract and "
                    "compatible observations are required for this format; "
                    "photo evidence is not supported yet. Video/YouTube "
                    "history cannot substitute for photo evidence."
                ),
            )
        return Unavailable(exc.reason)
    except (ValueError, TypeError, OverflowError, ZoneInfoNotFoundError):
        return Unavailable("invalid_request")
