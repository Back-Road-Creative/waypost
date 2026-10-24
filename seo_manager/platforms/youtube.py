"""
YouTube Platform Adapter.

Implements YouTube-specific SEO constraints and formatting rules.
This is a STANDALONE implementation that does NOT depend on or
interfere with any existing YouTube integration.
"""

from ..core.metadata import Platform
from .base import BasePlatformAdapter


class YouTubeAdapter(BasePlatformAdapter):
    """
    YouTube platform adapter for SEO optimization.

    YouTube SEO Guidelines:
    - Titles: 70 chars recommended for mobile display
    - Descriptions: 5000 chars maximum
    - Tags: ~15 tags recommended (500 char total limit)
    - Format: Comma-separated, case-insensitive
    """

    @property
    def platform_type(self) -> Platform:
        return Platform.YOUTUBE

    def get_title_limit(self) -> int:
        """
        YouTube title limit.

        Returns:
            70 characters (recommended for mobile visibility)
        """
        return 70

    def get_description_limit(self) -> int:
        """
        YouTube description limit.

        Returns:
            5000 characters (platform maximum)
        """
        return 5000

    def get_tag_limit(self) -> int:
        """
        YouTube tag limit.

        Returns:
            15 tags (recommended for ~500 char limit)
        """
        return 15

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
