"""
Blog/Hugo Platform Adapter.

Implements blog-specific SEO constraints optimized for Google SERP
and static site generators like Hugo.
"""

import re
from typing import List

from ..core.metadata import Platform
from .base import BasePlatformAdapter


class BlogAdapter(BasePlatformAdapter):
    """
    Blog/Hugo platform adapter for SEO optimization.

    Blog SEO Guidelines:
    - Titles: 60 chars for Google SERP display
    - Meta Descriptions: 160 chars for SERP snippet
    - Tags: 5-10 taxonomic keywords
    - Format: Slugified (lowercase, hyphenated) for URLs
    """

    @property
    def platform_type(self) -> Platform:
        return Platform.BLOG

    def get_title_limit(self) -> int:
        """
        Blog title limit.

        Returns:
            60 characters (optimal for Google SERP display)
        """
        return 60

    def get_description_limit(self) -> int:
        """
        Blog meta description limit.

        Returns:
            160 characters (optimal for Google SERP snippet)
        """
        return 160

    def get_tag_limit(self) -> int:
        """
        Blog tag limit.

        Returns:
            10 tags (SEO best practice for taxonomy)
        """
        return 10

    def format_tags(self, tags: List[str]) -> List[str]:
        """
        Format tags for blog/Hugo.

        Blog platforms expect slugified tags for URL generation.
        Example: "Machine Learning" -> "machine-learning"

        Args:
            tags: List of tag strings

        Returns:
            List of slugified tag strings

        Example:
            ["AI Tech", "Machine Learning"] -> ["ai-tech", "machine-learning"]
        """
        limited_tags = tags[: self.get_tag_limit()]
        return [self._slugify(tag) for tag in limited_tags]

    def _slugify(self, text: str) -> str:
        """
        Convert text to URL-safe slug.

        Args:
            text: Raw text

        Returns:
            Slugified text (lowercase, hyphenated, alphanumeric only)

        Examples:
            "Machine Learning" -> "machine-learning"
            "AI & ML" -> "ai-ml"
            "Python 3.11" -> "python-311"
        """
        # Lowercase
        text = text.lower().strip()

        # Remove special characters (keep alphanumeric, spaces, hyphens)
        text = re.sub(r"[^\w\s-]", "", text)

        # Replace spaces and underscores with hyphens
        text = re.sub(r"[\s_]+", "-", text)

        # Remove multiple consecutive hyphens
        text = re.sub(r"-+", "-", text)

        # Strip leading/trailing hyphens
        text = text.strip("-")

        return text
