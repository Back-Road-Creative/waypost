"""Edit only the description of a live YouTube video.

``videos.update(part="snippet")`` replaces the whole snippet: a body naming only the
description fails without a title and category, and one that leaves out ``tags``
deletes them. :func:`update_description` reads the snippet, sends back every writable
field unchanged with the new description, then reads the video again. If any field
other than the description moved, or the description did not land, it raises
:class:`DescriptionUpdateError`; it never reports a partial edit as done.

``expect_current`` binds the edit to the description the caller read: if the live
text changed since, nothing is written.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: Snippet fields YouTube sets itself; never sent back.
READ_ONLY = (
    "publishedAt",
    "channelId",
    "channelTitle",
    "thumbnails",
    "liveBroadcastContent",
    "localized",
)
#: Writable snippet fields the edit carries over and checks on read-back.
KEPT = ("title", "tags", "categoryId", "defaultLanguage", "defaultAudioLanguage")
#: YouTube's description limit, in bytes.
MAX_DESCRIPTION_BYTES = 5000


class DescriptionUpdateError(Exception):
    """The description was not changed, or not changed cleanly."""


def _check(description: str) -> None:
    if not description.strip():
        raise DescriptionUpdateError("the new description is empty")
    if "<" in description or ">" in description:
        raise DescriptionUpdateError("YouTube rejects '<' and '>' in a description")
    size = len(description.encode("utf-8"))
    if size > MAX_DESCRIPTION_BYTES:
        raise DescriptionUpdateError(
            f"the new description is {size} bytes; YouTube allows {MAX_DESCRIPTION_BYTES}"
        )


def read_snippet(youtube: Any, video_id: str) -> dict:
    """The video's live snippet; :class:`DescriptionUpdateError` when it is not found."""
    items = (
        youtube.videos().list(part="snippet", id=video_id).execute().get("items") or []
    )
    for item in items:
        if item.get("id") == video_id:
            return item.get("snippet") or {}
    raise DescriptionUpdateError(f"video {video_id} not found")


def update_description(
    youtube: Any,
    video_id: str,
    description: str,
    *,
    expect_current: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Set ``video_id``'s description and return ``{video_id, before, after, title, tags,
    categoryId, dry_run}``, ``after`` as YouTube returns it on read-back."""
    _check(description)
    before = read_snippet(youtube, video_id)
    current = before.get("description", "")
    if expect_current is not None and current != expect_current:
        raise DescriptionUpdateError(
            f"the description of {video_id} changed since it was read; nothing written"
        )
    snippet = {k: v for k, v in before.items() if k not in READ_ONLY}
    snippet["description"] = description
    result = {
        "video_id": video_id,
        "before": current,
        "after": description,
        "title": before.get("title"),
        "tags": before.get("tags", []),
        "categoryId": before.get("categoryId"),
        "dry_run": dry_run,
    }
    if dry_run:
        return result
    youtube.videos().update(
        part="snippet", body={"id": video_id, "snippet": snippet}
    ).execute()
    after = read_snippet(youtube, video_id)
    moved = [k for k in KEPT if after.get(k) != before.get(k)]
    if moved:
        raise DescriptionUpdateError(
            f"read-back of {video_id}: {', '.join(moved)} changed with the description"
        )
    if after.get("description") != description:
        raise DescriptionUpdateError(
            f"read-back of {video_id}: the new description did not land"
        )
    result["after"] = after["description"]
    return result


def build_client(token: Path) -> Any:
    """A YouTube Data v3 client from an authorized-user token file. The token must carry
    ``youtube`` or ``youtube.force-ssl`` scope; ``youtube.upload`` alone cannot edit."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    info = json.loads(Path(token).read_text(encoding="utf-8"))
    creds = Credentials.from_authorized_user_info(info, info.get("scopes"))
    return build("youtube", "v3", credentials=creds, cache_discovery=False)
