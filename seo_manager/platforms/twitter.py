"""
Twitter/X Platform Adapter.

Implements Twitter-specific SEO constraints and hashtag formatting.
"""

from typing import List

from ..core.metadata import Platform
from .base import BasePlatformAdapter


class TwitterAdapter(BasePlatformAdapter):
    """
    Twitter/X platform adapter for SEO optimization.

    Twitter SEO Guidelines:
    - Character limit: 280 total
    - Hashtags: 1-2 recommended, max 3
    - Format: Inline hashtags with # prefix
    """

    @property
    def platform_type(self) -> Platform:
        return Platform.TWITTER

    def get_title_limit(self) -> int:
        """
        Twitter title limit (for thread starters).

        Returns:
            50 characters (leave room for link preview)
        """
        return 50

    def get_description_limit(self) -> int:
        """
        Twitter post character limit.

        Returns:
            280 characters (platform maximum)
        """
        return 280

    def get_tag_limit(self) -> int:
        """
        Twitter hashtag limit.

        Returns:
            3 hashtags (best practice for engagement)
        """
        return 3

    def format_tags(self, tags: List[str]) -> str:
        """
        Format tags as Twitter hashtags.

        Twitter uses hashtags with # prefix, no spaces allowed.

        Args:
            tags: List of tag strings

        Returns:
            Space-separated hashtags

        Example:
            ["ai", "machine learning"] -> "#ai #machinelearning"
        """
        limited_tags = tags[: self.get_tag_limit()]

        # Convert to hashtags (remove spaces and special chars)
        hashtags = []
        for tag in limited_tags:
            # Remove spaces and special characters
            clean_tag = tag.replace(" ", "").replace("-", "").replace("_", "")
            hashtags.append(f"#{clean_tag.lower()}")

        return " ".join(hashtags)
