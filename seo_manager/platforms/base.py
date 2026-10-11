"""
Base Platform Adapter interface.

Defines the contract that all platform adapters must implement.
"""

from abc import ABC, abstractmethod
from typing import List

from ..core.metadata import Platform


class BasePlatformAdapter(ABC):
    """
    Abstract base class for platform-specific adapters.

    Each platform (YouTube, Blog, Twitter, etc.) implements this interface
    to provide platform-specific constraints, formatting, and validation.
    """

    @property
    @abstractmethod
    def platform_type(self) -> Platform:
        """Return the platform enum value"""
        pass

    @abstractmethod
    def get_title_limit(self) -> int:
        """Return maximum title length (characters)"""
        pass

    @abstractmethod
    def get_description_limit(self) -> int:
        """Return maximum description length (characters)"""
        pass

    @abstractmethod
    def get_tag_limit(self) -> int:
        """Return maximum number of tags"""
        pass

    @abstractmethod
    def format_tags(self, tags: List[str]):
        """
        Format tags according to platform convention.

        Returns:
            Platform-specific tag format (string or list)

        Examples:
        - YouTube: "ai, machine learning, podcast" (comma-separated)
        - Instagram: "#ai #machinelearning #podcast" (hashtags)
        - Blog: ['ai', 'machine-learning', 'podcast'] (slugified list)
        """
        pass

    def validate_title(self, title: str) -> bool:
        """
        Check if title meets platform requirements.

        Args:
            title: Title text

        Returns:
            True if valid, False otherwise
        """
        return len(title) <= self.get_title_limit()

    def validate_description(self, description: str) -> bool:
        """
        Check if description meets platform requirements.

        Args:
            description: Description text

        Returns:
            True if valid, False otherwise
        """
        return len(description) <= self.get_description_limit()

    def validate_tags(self, tags: List[str]) -> bool:
        """
        Check if tag count/format is valid.

        Args:
            tags: List of tags

        Returns:
            True if valid, False otherwise
        """
        return len(tags) <= self.get_tag_limit()
