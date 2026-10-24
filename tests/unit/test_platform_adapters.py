"""
Unit tests for Platform Adapters.

Tests cover:
- BasePlatformAdapter interface
- YouTubeAdapter constraints and formatting
- Tag formatting (comma-separated)
"""

import pytest

from seo_manager.core.metadata import Platform
from seo_manager.platforms.base import BasePlatformAdapter
from seo_manager.platforms.youtube import YouTubeAdapter


class TestYouTubeAdapter:
    """Test YouTube platform adapter"""

    @pytest.fixture
    def adapter(self):
        return YouTubeAdapter()

    def test_platform_type(self, adapter):
        """Test platform type is YOUTUBE"""
        assert adapter.platform_type == Platform.YOUTUBE

    def test_title_limit(self, adapter):
        """Test YouTube title limit"""
        limit = adapter.get_title_limit()
        # YouTube recommends 70 chars for mobile
        assert limit == 70

    def test_description_limit(self, adapter):
        """Test YouTube description limit"""
        limit = adapter.get_description_limit()
        # YouTube allows 5000 chars
        assert limit == 5000

    def test_tag_limit(self, adapter):
        """Test YouTube tag limit"""
        limit = adapter.get_tag_limit()
        # Typically 15 tags for ~500 char limit
        assert limit == 15

    def test_format_tags_comma_separated(self, adapter):
        """Test YouTube tags are comma-separated and lowercase"""
        tags = ["AI", "Machine Learning", "Python", "Programming"]

        formatted = adapter.format_tags(tags)

        assert formatted == "ai, machine learning, python, programming"
        # No hashtags
        assert "#" not in formatted

    def test_validate_title(self, adapter):
        """Test title validation"""
        valid_title = "A" * 70
        invalid_title = "A" * 71

        assert adapter.validate_title(valid_title) is True
        assert adapter.validate_title(invalid_title) is False

    def test_validate_description(self, adapter):
        """Test description validation"""
        valid_desc = "A" * 5000
        invalid_desc = "A" * 5001

        assert adapter.validate_description(valid_desc) is True
        assert adapter.validate_description(invalid_desc) is False

    def test_validate_tags(self, adapter):
        """Test tag count validation"""
        valid_tags = ["tag"] * 15
        invalid_tags = ["tag"] * 16

        assert adapter.validate_tags(valid_tags) is True
        assert adapter.validate_tags(invalid_tags) is False


class TestBasePlatformAdapter:
    """Test base adapter interface"""

    def test_all_adapters_implement_interface(self):
        """Test that all adapters properly implement the base interface"""
        adapters = [YouTubeAdapter()]

        for adapter in adapters:
            # All required methods should exist
            assert hasattr(adapter, "platform_type")
            assert hasattr(adapter, "get_title_limit")
            assert hasattr(adapter, "get_description_limit")
            assert hasattr(adapter, "get_tag_limit")
            assert hasattr(adapter, "format_tags")
            assert hasattr(adapter, "validate_title")
            assert hasattr(adapter, "validate_description")
            assert hasattr(adapter, "validate_tags")

            # Test they return expected types
            assert isinstance(adapter.platform_type, Platform)
            assert isinstance(adapter.get_title_limit(), int)
            assert isinstance(adapter.get_description_limit(), int)
            assert isinstance(adapter.get_tag_limit(), int)
