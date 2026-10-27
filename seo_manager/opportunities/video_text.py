"""Fetch the current public title and description of videos. Read-only.

One ``videos.list`` call per chunk of up to 50 ids, through an injected read
wrapper (default ``retry_read``; idempotent reads only). An id the API does not
return is ``None``, never an empty text. Failures are recorded by exception
type only: a message could echo a token.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from seo_manager.opportunities.contracts import VideoText
from seo_manager.transport import (
    AuthError,
    QuotaExhaustedError,
    TransportError,
    retry_read,
)

CHUNK = 50


@dataclass(frozen=True)
class VideoTextFetch:
    """Texts by video id (``None`` = unavailable) and the first error's type name."""

    texts: dict[str, VideoText | None]
    error_class: str | None = None

    @property
    def state(self) -> str:
        """``fetched``; ``partial`` when an error left some texts unread; ``error``."""
        if self.error_class is None:
            return "fetched"
        return "partial" if any(self.texts.values()) else "error"


def _text(item: dict) -> VideoText | None:
    snippet = item.get("snippet") or {}
    title, description = snippet.get("title"), snippet.get("description")
    if not isinstance(title, str) or not isinstance(description, str):
        return None
    return VideoText(title, description)


def fetch_video_text(
    youtube: Any,
    video_ids: Iterable[str],
    *,
    read: Callable[[Callable[[], dict]], dict] = retry_read,
) -> VideoTextFetch:
    """Return the text of each id; an unreachable id is ``None``."""
    ids = list(dict.fromkeys(video_ids))
    texts: dict[str, VideoText | None] = dict.fromkeys(ids)
    error_class: str | None = None
    for start in range(0, len(ids), CHUNK):
        chunk = ids[start : start + CHUNK]

        def call(chunk: list[str] = chunk) -> dict:
            return (
                youtube.videos()
                .list(part="snippet", id=",".join(chunk), maxResults=CHUNK)
                .execute()
            )

        try:
            response = read(call)
        except (QuotaExhaustedError, AuthError) as exc:
            return VideoTextFetch(texts, error_class or type(exc).__name__)
        except TransportError as exc:
            error_class = error_class or type(exc).__name__
            continue
        for item in response.get("items") or []:
            vid = item.get("id")
            if vid in texts:
                texts[vid] = _text(item)
    return VideoTextFetch(texts, error_class)
