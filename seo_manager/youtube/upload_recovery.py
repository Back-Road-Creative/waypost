"""Resume or reconcile an interrupted upload; never insert the same video twice blind.

``videos.insert`` is not idempotent: retrying it after a timeout can publish a
second copy. So an upload is journalled before its first byte is sent, and an
interruption whose outcome is unknown is settled by looking the video up in
the channel's uploads playlist (exact title and description, sent no earlier
than the attempt) before anything is sent again. See ``docs/transport-retry.md``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..fsio import atomic_write_json
from ..quota.manager import Outcome, QuotaExceededError, QuotaManager
from ..transport import RetryPolicy, TransportError, retry_read

#: A video absent this long after its attempt started was not created.
RECONCILE_GRACE = timedelta(minutes=15)
#: Clock difference tolerated between this host and YouTube's ``publishedAt``.
CLOCK_SKEW = timedelta(minutes=10)
#: Uploads-playlist pages read per lookup (50 items each, newest first).
MAX_PAGES = 4


class UploadState(StrEnum):
    IN_FLIGHT = "in_flight"  # bytes may have been sent; outcome not yet known
    UPLOADED = "uploaded"
    NOT_UPLOADED = "not_uploaded"  # refused or never started: safe to send again
    UNKNOWN = "unknown"  # interrupted and not found yet: reconcile before sending


class UploadOutcomeUnknown(Exception):
    """The upload may or may not exist. Nothing is sent again until it is settled."""

    def __init__(self, message: str, *, key: str, video_path: str):
        super().__init__(message)
        self.key = key
        self.video_path = video_path


@dataclass(frozen=True)
class UploadRecord:
    key: str
    state: UploadState
    started_at: datetime | None = None  # None: unreadable record, time unknown
    video_id: str | None = None
    error: str | None = None


def upload_key(video_path: str, body: dict[str, Any]) -> str:
    """Identity of one upload: the exact request body plus the file's name and size."""
    path = Path(video_path)
    material = json.dumps(
        [path.name, path.stat().st_size, body], sort_keys=True, default=str
    )
    return hashlib.sha256(material.encode()).hexdigest()


class UploadJournal:
    """One JSON record per upload key, written atomically."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> UploadRecord | None:
        """The record, or None. An unreadable record is UNKNOWN, never absent."""
        path = self._path(key)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            started = raw.get("started_at")
            return UploadRecord(
                key=key,
                state=UploadState(raw["state"]),
                started_at=datetime.fromisoformat(started) if started else None,
                video_id=raw.get("video_id"),
                error=raw.get("error"),
            )
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            return UploadRecord(key, UploadState.UNKNOWN, error=f"unreadable: {exc}")

    def put(self, record: UploadRecord) -> None:
        data = asdict(record)
        data["state"] = record.state.value
        data["started_at"] = (
            record.started_at.isoformat() if record.started_at else None
        )
        atomic_write_json(self._path(record.key), data)


def metered(
    quota: QuotaManager | None,
    operation: str,
    call: Callable[[], Any],
    brand_id: str | None = None,
) -> Any:
    """Run one API request against a quota reservation (settled either way)."""
    if quota is None:
        return call()
    reservation = quota.reserve(operation, brand_id=brand_id)
    try:
        result = call()
    except BaseException:
        quota.settle(reservation, Outcome.FAILED)
        raise
    quota.settle(reservation, Outcome.USED)
    return result


def _published(item: dict[str, Any]) -> datetime | None:
    value = (item.get("snippet") or {}).get("publishedAt")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def find_upload(
    youtube: Any,
    *,
    title: str,
    description: str,
    since: datetime | None,
    key: str,
    video_path: str,
    quota: QuotaManager | None = None,
    brand_id: str | None = None,
    policy: RetryPolicy | None = None,
    sleep: Callable[[float], None] | None = None,
) -> str | None:
    """The ID of this channel's upload with exactly ``title`` and ``description``.

    ``None`` when no upload sent since ``since`` (less ``CLOCK_SKEW``) matches.
    Raises ``UploadOutcomeUnknown`` when the lookup fails or more than one
    upload matches: a guess could publish a duplicate or report the wrong one.
    """
    extra = {} if sleep is None else {"sleep": sleep}

    def read(operation: str, request: Callable[[], Any]) -> Any:
        return retry_read(
            lambda: metered(quota, operation, lambda: request().execute(), brand_id),
            policy=policy,
            **extra,
        )

    floor = None if since is None else since - CLOCK_SKEW
    matches: list[str] = []
    try:
        channels = read(
            "channels.list",
            lambda: youtube.channels().list(part="contentDetails", mine=True),
        )
        uploads = channels["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
        token = None
        for _ in range(MAX_PAGES):
            page_args = {"part": "snippet", "playlistId": uploads, "maxResults": 50}
            if token:
                page_args["pageToken"] = token
            page = read(
                "playlistItems.list",
                lambda args=page_args: youtube.playlistItems().list(**args),
            )
            older = False
            for item in page.get("items", []):
                snippet = item.get("snippet") or {}
                at = _published(item)
                if floor is not None and (at is None or at < floor):
                    older = True
                    continue
                if (
                    snippet.get("title") == title
                    and snippet.get("description") == description
                ):
                    matches.append(snippet["resourceId"]["videoId"])
            token = page.get("nextPageToken")
            if older or not token:
                break
    except (
        TransportError,
        QuotaExceededError,
        OSError,
        KeyError,
        IndexError,
        TypeError,
    ) as exc:
        raise UploadOutcomeUnknown(
            f"reconcile lookup failed: {exc}", key=key, video_path=video_path
        ) from exc
    if len(matches) > 1:
        raise UploadOutcomeUnknown(
            f"{len(matches)} uploads match: {', '.join(matches)}",
            key=key,
            video_path=video_path,
        )
    return matches[0] if matches else None
