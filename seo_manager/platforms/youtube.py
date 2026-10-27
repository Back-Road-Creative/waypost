"""
YouTube Platform Adapter.

Implements YouTube-specific SEO constraints and formatting rules.
This is a STANDALONE implementation that does NOT depend on or
interfere with any existing YouTube integration.
"""

from ..core.metadata import Platform
from .base import BasePlatformAdapter

# ---------------------------------------------------------------------------
# One home for YouTube's numbers. Three kinds, never mixed:
#
#   API LIMITS    -- the Data API rejects the upload. Hard, documented.
#   STYLE ADVICE  -- editorial guidance (mobile display, tag habits). Nobody
#                    measured these on this channel; they are never to be
#                    presented as a measured effect or as an API rule.
#   MEASURED      -- associations from the channel's own analytics; those live
#                    in `analytics.seo_optimizer` and always carry their n.
#
# The optimizer, the best-practices validator and the adapter import these;
# `analytics.title_selection` is deliberately independent of the optimizer and
# pins its own cap equal to YOUTUBE_TITLE_API_MAX_CHARS in a test.
# ---------------------------------------------------------------------------

#: Data API: a title over this is rejected with a 400.
YOUTUBE_TITLE_API_MAX_CHARS = 100
#: Data API: tags are bounded by total characters, NOT by count.
YOUTUBE_TAGS_API_MAX_CHARS = 500

#: Style: titles up to this many characters show in full on mobile.
YOUTUBE_TITLE_DISPLAY_CHARS = 70
#: Style: a title under this reads as thin. A habit, not a measurement.
YOUTUBE_TITLE_STYLE_MIN_CHARS = 50
#: Style: a (min, max) tag-count habit. Not an API cap.
YOUTUBE_TAG_COUNT_STYLE_RANGE = (10, 15)


class YouTubeAdapter(BasePlatformAdapter):
    """
    YouTube platform adapter for SEO optimization.

    YouTube SEO Guidelines:
    - Titles: 100 chars is the API limit; 70 is a mobile-display style cap
      that generation honours (`get_title_limit`)
    - Descriptions: 5000 chars maximum
    - Tags: 500 chars in total is the API limit; 15 tags is a style cap
      (`get_tag_limit`)
    - Format: Comma-separated, case-insensitive
    """

    @property
    def platform_type(self) -> Platform:
        return Platform.YOUTUBE

    def get_title_limit(self) -> int:
        """
        Title length generation targets (a STYLE cap, not the API limit).

        Returns:
            70 characters (shows in full on mobile). The API rejects only
            above `get_title_api_limit()`.
        """
        return YOUTUBE_TITLE_DISPLAY_CHARS

    def get_title_api_limit(self) -> int:
        """The YouTube Data API's hard title limit (100 characters)."""
        return YOUTUBE_TITLE_API_MAX_CHARS

    def get_tag_char_limit(self) -> int:
        """The YouTube Data API's hard limit: 500 characters across all tags."""
        return YOUTUBE_TAGS_API_MAX_CHARS

    def get_description_limit(self) -> int:
        """
        YouTube description limit.

        Returns:
            5000 characters (platform maximum)
        """
        return 5000

    def get_tag_limit(self) -> int:
        """
        Tag count generation targets (a STYLE cap, not an API rule).

        Returns:
            15 tags. The API bounds tags by characters, see
            `get_tag_char_limit()`.
        """
        return YOUTUBE_TAG_COUNT_STYLE_RANGE[1]

    def format_tags(self, tags: list[str]) -> str:
        """
        Format tags for YouTube.

        YouTube uses comma-separated tags (not hashtags).
        Tags are case-insensitive.

        Args:
            tags: List of tag strings

        Returns:
            Comma-separated tag string

        Example:
            ["AI", "Machine Learning"] -> "ai, machine learning"
        """
        # Lowercase and limit to tag_limit
        limited_tags = tags[: self.get_tag_limit()]
        lowercase_tags = [tag.lower() for tag in limited_tags]

        return ", ".join(lowercase_tags)
