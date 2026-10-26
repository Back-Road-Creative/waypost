"""EvidenceBundle v1 and v2: the analytics evidence the nightly volume decision reads.

The bundle carries observations only: no recommendation and no threshold. The
consumer side (``volume_decide``) validates it here and refuses anything it does
not understand, with a reason, rather than guessing. The same golden fixtures
(``tests/fixtures/evidence_bundle_v1.json``, ``..._v2.json``) are committed in the
producing repo.

v1 short rows are YouTube only and carry ``day7_views``. v2 short rows carry a
``platform`` ("youtube" or "facebook"), a ``post_format`` and a ``day7`` object of
raw day-7 counters (``views``, ``reel_total_plays``, ``reel_initial_plays``,
``watch_time_seconds``). The producer only reports what it measured; Waypost owns
``REACH_METRIC``, the table that says which counter is "reach" on each platform.
Every parsed ``ShortRecord`` (v1 and v2) carries ``day7_views`` = that reach value,
so readers written for YouTube keep working. A v2 row with no reach value is listed
in ``EvidenceBundle.unmeasured``, not in ``shorts``. ``longforms`` is identical in v1
and v2 (YouTube only).

Pure module: no I/O. Timestamps stay as the ISO strings the producer wrote (so a
bundle round-trips byte-for-byte through ``bundle_to_dict``); ``parse_ts`` gives
the naive-UTC ``datetime`` the controller compares.
"""

import statistics
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

SCHEMA_VERSION = 2
SUPPORTED_VERSIONS = (1, 2)
ANALYTICS_STATUSES = ("ok", "scope_missing", "no_token", "error")
SHORT_SOURCES = ("analytics_daily", "snapshot")
PLATFORMS = ("youtube", "facebook")
POST_FORMATS = ("short", "reel", "video")
DAY7_COUNTERS = (
    "views",
    "reel_total_plays",
    "reel_initial_plays",
    "watch_time_seconds",
)
# Which day-7 counter is "reach", per (platform, post_format). A YouTube view and a
# Facebook Reel play are different counters and are never pooled. ``None`` format on
# Facebook has no reach. Waypost decides this; the producer only reports counters.
REACH_METRIC: dict[tuple[str, str | None], str] = {
    ("youtube", "short"): "views",
    ("youtube", "video"): "views",
    ("youtube", "reel"): "views",
    ("youtube", None): "views",
    ("facebook", "reel"): "reel_total_plays",
    ("facebook", "video"): "views",
}


class EvidenceRefused(ValueError):
    """The bundle is not one this reader understands; ``str(exc)`` says why."""


@dataclass(frozen=True)
class Health:
    analytics_status: str
    n_registered: int
    n_with_series: int
    excluded: Any = field(default_factory=list)
    detail: str | None = None


@dataclass(frozen=True)
class ShortRecord:
    video_id: str
    project: str
    region: str | None
    platform: str
    posted_at: str
    weekday: int
    hour_local: int
    day7_views: float  # the reach value (see REACH_METRIC)
    source: str
    post_format: str | None = None
    day7: dict[str, float | None] = field(default_factory=dict)
    reach_metric: str = "views"


@dataclass(frozen=True)
class LongformRecord:
    video_id: str
    project: str
    region: str | None
    posted_at: str
    duration_sec: float
    day7_views: float
    avg_view_percentage: float


@dataclass(frozen=True)
class EvidenceBundle:
    schema_version: int
    as_of: str
    health: Health
    shorts: list[ShortRecord]
    longforms: list[LongformRecord]
    unmeasured: list[dict[str, str]] = field(default_factory=list)


def parse_ts(value: str) -> datetime:
    """An ISO timestamp as a naive UTC ``datetime`` (aware values are converted)."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _need(obj: Mapping[str, Any], key: str, where: str) -> Any:
    if not isinstance(obj, Mapping) or key not in obj:
        raise EvidenceRefused(f"{where}: missing {key!r}")
    return obj[key]


def _text(obj: Mapping[str, Any], key: str, where: str) -> str:
    v = _need(obj, key, where)
    if not isinstance(v, str) or not v:
        raise EvidenceRefused(f"{where}: {key} must be a non-empty string")
    return v


def _opt_text(obj: Mapping[str, Any], key: str, where: str) -> str | None:
    v = _need(obj, key, where)
    if v is not None and not isinstance(v, str):
        raise EvidenceRefused(f"{where}: {key} must be a string or null")
    return v


def _int(obj: Mapping[str, Any], key: str, where: str, lo: int, hi: int) -> int:
    v = _need(obj, key, where)
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise EvidenceRefused(f"{where}: {key} must be an integer in [{lo}, {hi}]")
    return v


def _num(obj: Mapping[str, Any], key: str, where: str) -> float:
    v = _need(obj, key, where)
    if not isinstance(v, (int, float)) or isinstance(v, bool) or v != v or v < 0:
        raise EvidenceRefused(f"{where}: {key} must be a non-negative number")
    return v


def _ts(obj: Mapping[str, Any], key: str, where: str) -> str:
    v = _text(obj, key, where)
    try:
        parse_ts(v)
    except ValueError as exc:
        raise EvidenceRefused(f"{where}: {key} is not an ISO timestamp: {v!r}") from exc
    return v


def _rows(raw: Mapping[str, Any], key: str) -> list[Any]:
    rows = _need(raw, key, "bundle")
    if not isinstance(rows, list):
        raise EvidenceRefused(f"bundle: {key} must be a list")
    return rows


def _short_v1(s: Mapping[str, Any], w: str) -> ShortRecord:
    if _text(s, "platform", w) != "youtube":
        raise EvidenceRefused(f"{w}: platform must be 'youtube'")
    source = _text(s, "source", w)
    if source not in SHORT_SOURCES:
        raise EvidenceRefused(f"{w}: source {source!r} not in {SHORT_SOURCES}")
    views = _num(s, "day7_views", w)
    return ShortRecord(
        _text(s, "video_id", w),
        _text(s, "project", w),
        _opt_text(s, "region", w),
        "youtube",
        _ts(s, "posted_at", w),
        _int(s, "weekday", w, 0, 6),
        _int(s, "hour_local", w, 0, 23),
        views,
        source,
        None,
        {"views": views},
        "views",
    )


def _day7(s: Mapping[str, Any], w: str) -> dict[str, float | None]:
    d = _need(s, "day7", w)
    if not isinstance(d, Mapping):
        raise EvidenceRefused(f"{w}: day7 must be an object")
    out: dict[str, float | None] = {}
    for name in d:
        if name not in DAY7_COUNTERS:
            raise EvidenceRefused(f"{w}: day7 counter {name!r} not in {DAY7_COUNTERS}")
        out[name] = None if d[name] is None else _num(d, name, f"{w}.day7")
    return out


def _short_v2(s: Mapping[str, Any], w: str) -> tuple[ShortRecord | None, str]:
    """The record, or ``(None, reason)`` when the row has no reach value."""
    platform = _text(s, "platform", w)
    if platform not in PLATFORMS:
        raise EvidenceRefused(f"{w}: platform {platform!r} not in {PLATFORMS}")
    fmt = _opt_text(s, "post_format", w)
    if fmt is not None and fmt not in POST_FORMATS:
        raise EvidenceRefused(f"{w}: post_format {fmt!r} not in {POST_FORMATS}")
    source = _text(s, "source", w)
    if source not in SHORT_SOURCES:
        raise EvidenceRefused(f"{w}: source {source!r} not in {SHORT_SOURCES}")
    base = (
        _text(s, "video_id", w),
        _text(s, "project", w),
        _opt_text(s, "region", w),
        platform,
        _ts(s, "posted_at", w),
        _int(s, "weekday", w, 0, 6),
        _int(s, "hour_local", w, 0, 23),
    )
    day7 = _day7(s, w)
    metric = REACH_METRIC.get((platform, fmt))
    if metric is None:
        return None, f"no reach metric for {platform}/{fmt}"
    reach = day7.get(metric)
    if reach is None:
        return None, f"{metric} not measured"
    return ShortRecord(*base, reach, source, fmt, day7, metric), ""


def parse_bundle(raw: Any) -> EvidenceBundle:
    """Validate ``raw`` (a decoded JSON object) into an ``EvidenceBundle``."""
    if not isinstance(raw, Mapping):
        raise EvidenceRefused("bundle: not a JSON object")
    version = raw.get("schema_version")
    if version not in SUPPORTED_VERSIONS or isinstance(version, bool):
        raise EvidenceRefused(
            f"schema_version {version!r} is not supported (this reader: "
            f"{', '.join(map(str, SUPPORTED_VERSIONS))})"
        )
    as_of = _ts(raw, "as_of", "bundle")
    h = _need(raw, "health", "bundle")
    status = _text(h, "analytics_status", "health") if isinstance(h, Mapping) else ""
    if not isinstance(h, Mapping):
        raise EvidenceRefused("bundle: health must be an object")
    if status not in ANALYTICS_STATUSES:
        raise EvidenceRefused(
            f"health: analytics_status {status!r} not in {ANALYTICS_STATUSES}"
        )
    big = 10**9
    health = Health(
        status,
        _int(h, "n_registered", "health", 0, big),
        _int(h, "n_with_series", "health", 0, big),
        _need(h, "excluded", "health"),
        _opt_text(h, "detail", "health"),
    )
    shorts: list[ShortRecord] = []
    unmeasured: list[dict[str, str]] = []
    for i, s in enumerate(_rows(raw, "shorts")):
        w = f"shorts[{i}]"
        if version == 1:
            shorts.append(_short_v1(s, w))
            continue
        rec, why = _short_v2(s, w)
        if rec is not None:
            shorts.append(rec)
        else:
            unmeasured.append(
                {
                    "video_id": _text(s, "video_id", w),
                    "platform": _text(s, "platform", w),
                    "reason": str(why),
                }
            )
    longforms = []
    for i, x in enumerate(_rows(raw, "longforms")):
        w = f"longforms[{i}]"
        longforms.append(
            LongformRecord(
                _text(x, "video_id", w),
                _text(x, "project", w),
                _opt_text(x, "region", w),
                _ts(x, "posted_at", w),
                _num(x, "duration_sec", w),
                _num(x, "day7_views", w),
                _num(x, "avg_view_percentage", w),
            )
        )
    return EvidenceBundle(version, as_of, health, shorts, longforms, unmeasured)


def bundle_to_dict(bundle: EvidenceBundle) -> dict[str, Any]:
    """A v1 bundle back to its wire form; a v2 bundle as Waypost's parsed form."""
    out = asdict(bundle)
    if bundle.schema_version == 1:
        out.pop("unmeasured")
        for row in out["shorts"]:
            for k in ("post_format", "day7", "reach_metric"):
                row.pop(k)
    return out


def platform_reach_summary(bundle: EvidenceBundle) -> dict[str, dict[str, Any]]:
    """Per platform: the reach metric, settled short count and median day-7 reach.

    Platforms never pool: a platform whose rows use more than one reach metric
    (Facebook reels vs videos) gets one ``platform/metric`` entry per metric. A
    platform with only unmeasured rows reports ``metric`` and ``median_day7`` null.
    """
    groups: dict[tuple[str, str | None], list[float]] = {}
    for s in bundle.shorts:
        groups.setdefault((s.platform, s.reach_metric), []).append(s.day7_views)
    for u in bundle.unmeasured:
        if not any(p == u["platform"] for p, _ in groups):
            groups[(u["platform"], None)] = []
    per_platform: dict[str, int] = {}
    for p, _ in groups:
        per_platform[p] = per_platform.get(p, 0) + 1
    out: dict[str, dict[str, Any]] = {}
    for (p, m), vals in sorted(
        groups.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")
    ):
        key = p if per_platform[p] == 1 else f"{p}/{m}"
        out[key] = {
            "metric": m,
            "n": len(vals),
            "median_day7": statistics.median(vals) if vals else None,
        }
    return out
