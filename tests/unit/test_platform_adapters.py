"""
Unit tests for Platform Adapters.

Tests cover:
- BasePlatformAdapter interface
- YouTubeAdapter constraints and formatting
- BlogAdapter constraints and formatting
- TwitterAdapter constraints and formatting
- Tag formatting (comma-separated vs hashtags vs slugs)
"""

import pytest

from seo_manager.core.metadata import Platform
from seo_manager.platforms.base import BasePlatformAdapter
from seo_manager.platforms.blog import BlogAdapter
from seo_manager.platforms.twitter import TwitterAdapter
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


class TestBlogAdapter:
    """Test Blog platform adapter"""

    @pytest.fixture
    def adapter(self):
        return BlogAdapter()

    def test_platform_type(self, adapter):
        """Test platform type is BLOG"""
        assert adapter.platform_type == Platform.BLOG

    def test_title_limit(self, adapter):
        """Test blog title limit (SERP optimized)"""
        limit = adapter.get_title_limit()
        # SEO best practice: 50-60 chars for Google SERP
        assert limit == 60

    def test_description_limit(self, adapter):
        """Test blog meta description limit"""
        limit = adapter.get_description_limit()
        # Meta description: 150-160 chars
        assert limit == 160

    def test_tag_limit(self, adapter):
        """Test blog tag limit"""
        limit = adapter.get_tag_limit()
        # 5-10 tags is best practice
        assert limit == 10

    def test_format_tags_slugified(self, adapter):
        """Test blog tags are slugified (lowercase, hyphenated)"""
        tags = ["Machine Learning", "AI Technology", "Python 3.11"]

        formatted = adapter.format_tags(tags)

        assert isinstance(formatted, list)
        assert "machine-learning" in formatted
        assert "ai-technology" in formatted
        assert "python-3-11" in formatted or "python-311" in formatted

    def test_slugify_method(self, adapter):
        """Test slugification utility"""
        assert adapter._slugify("Machine Learning") == "machine-learning"
        assert adapter._slugify("AI & ML") == "ai-ml"
        assert adapter._slugify("Python 3.11") == "python-311"
        assert adapter._slugify("  Extra  Spaces  ") == "extra-spaces"


class TestTwitterAdapter:
    """Test Twitter/X platform adapter"""

    @pytest.fixture
    def adapter(self):
        return TwitterAdapter()

    def test_platform_type(self, adapter):
        """Test platform type is TWITTER"""
        assert adapter.platform_type == Platform.TWITTER

    def test_title_limit(self, adapter):
        """Test Twitter title limit (for thread first tweet)"""
        limit = adapter.get_title_limit()
        # Leave room for link preview
        assert limit == 50

    def test_description_limit(self, adapter):
        """Test Twitter character limit"""
        limit = adapter.get_description_limit()
        # Twitter has 280 char limit
        assert limit == 280

    def test_tag_limit(self, adapter):
        """Test Twitter hashtag limit"""
        limit = adapter.get_tag_limit()
        # Best practice: 1-2 hashtags, max 3
        assert limit == 3

    def test_format_tags_hashtags(self, adapter):
        """Test Twitter tags are formatted as hashtags"""
        tags = ["ai", "machine learning", "python"]

        formatted = adapter.format_tags(tags)

        # Should be space-separated hashtags
        assert formatted == "#ai #machinelearning #python"
        # No spaces in hashtags
        assert "#machine learning" not in formatted


class TestBasePlatformAdapter:
    """Test base adapter interface"""

    def test_all_adapters_implement_interface(self):
        """Test that all adapters properly implement the base interface"""
        adapters = [YouTubeAdapter(), BlogAdapter(), TwitterAdapter()]

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
