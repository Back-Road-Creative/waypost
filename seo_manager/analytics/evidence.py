"""EvidenceBundle v1: the analytics evidence the nightly volume decision reads.

The bundle carries observations only: no recommendation and no threshold. The
consumer side (``volume_decide``) validates it here and refuses anything it does
not understand, with a reason, rather than guessing. The same golden fixture
(``tests/fixtures/evidence_bundle_v1.json``) is committed in the producing repo.

Pure module: no I/O. Timestamps stay as the ISO strings the producer wrote (so a
bundle round-trips byte-for-byte through ``bundle_to_dict``); ``parse_ts`` gives
the naive-UTC ``datetime`` the controller compares.
"""

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

SCHEMA_VERSION = 1
ANALYTICS_STATUSES = ("ok", "scope_missing", "no_token", "error")
SHORT_SOURCES = ("analytics_daily", "snapshot")


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
    day7_views: float
    source: str


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


def parse_bundle(raw: Any) -> EvidenceBundle:
    """Validate ``raw`` (a decoded JSON object) into an ``EvidenceBundle``."""
    if not isinstance(raw, Mapping):
        raise EvidenceRefused("bundle: not a JSON object")
    version = raw.get("schema_version")
    if version != SCHEMA_VERSION or isinstance(version, bool):
        raise EvidenceRefused(
            f"schema_version {version!r} is not supported (this reader: "
            f"{SCHEMA_VERSION})"
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
    shorts = []
    for i, s in enumerate(_rows(raw, "shorts")):
        w = f"shorts[{i}]"
        if _text(s, "platform", w) != "youtube":
            raise EvidenceRefused(f"{w}: platform must be 'youtube'")
        source = _text(s, "source", w)
        if source not in SHORT_SOURCES:
            raise EvidenceRefused(f"{w}: source {source!r} not in {SHORT_SOURCES}")
        shorts.append(
            ShortRecord(
                _text(s, "video_id", w),
                _text(s, "project", w),
                _opt_text(s, "region", w),
                "youtube",
                _ts(s, "posted_at", w),
                _int(s, "weekday", w, 0, 6),
                _int(s, "hour_local", w, 0, 23),
                _num(s, "day7_views", w),
                source,
            )
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
    return EvidenceBundle(version, as_of, health, shorts, longforms)


def bundle_to_dict(bundle: EvidenceBundle) -> dict[str, Any]:
    return asdict(bundle)
