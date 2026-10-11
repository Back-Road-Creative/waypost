"""Provenance tags for click-rate metrics.

A click rate is meaningless without its denominator, and YouTube has two that
answer different questions. ``cardClickRate`` asks: of the viewers ALREADY
watching, how many clicked the in-player info card — it cannot see thumbnails.
``impressionClickThroughRate`` asks: of the people shown the thumbnail, how
many clicked — the only rate that measures a thumbnail, and one the Analytics
API does not expose (Studio-UI only; see ANALYTICS_CTR_LIMITATION in
``youtube_sync.py``). Its tag exists anyway, so a Studio CSV ingest has a name
to declare and thumbnail analysis has something to require.

Every click rate crossing a module boundary carries one of these. An absent
tag is ``UNKNOWN`` and is never assumed to be thumbnail data.
"""

from typing import Optional

YOUTUBE_CARD_CLICK_RATE = "youtube_card_click_rate"
YOUTUBE_THUMBNAIL_IMPRESSION_CTR = "youtube_thumbnail_impression_ctr"
UNKNOWN = "unknown"

_KNOWN = frozenset({YOUTUBE_CARD_CLICK_RATE, YOUTUBE_THUMBNAIL_IMPRESSION_CTR, UNKNOWN})
# The allowlist is the guard: a source is thumbnail evidence only by being
# named here, so any metric added elsewhere is refused by default.
_THUMBNAIL_SOURCES = frozenset({YOUTUBE_THUMBNAIL_IMPRESSION_CTR})


def normalize(source: Optional[str]) -> str:
    """Absent or unrecognized provenance reads as UNKNOWN."""
    return source if source in _KNOWN else UNKNOWN


def is_thumbnail_click_through(source: Optional[str]) -> bool:
    """True only for rates whose denominator is thumbnail impressions."""
    return source in _THUMBNAIL_SOURCES
