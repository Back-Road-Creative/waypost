"""
Unit tests for SEO Validation.

Tests cover:
- Platform-specific validation rules
- Title length validation
- Description length validation
- Tag count validation
- Keyword presence validation
- ValidationResult dataclass
"""

import pytest

from seo_manager.core.metadata import ContentType, Platform, SEOMetadata
from seo_manager.platforms.blog import BlogAdapter
from seo_manager.platforms.youtube import YouTubeAdapter
from seo_manager.validation.validators import PlatformValidator, ValidationResult


class TestValidationResult:
    """Test ValidationResult dataclass"""

    def test_create_valid_result(self):
        """Test creating valid validation result"""
        from seo_manager.core.metadata import ContentType, Platform, SEOMetadata

        metadata = SEOMetadata(
            site_id="test",
            content_id="test_001",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,
            title="Test",
            description="Test",
            tags=["test"],
        )

        result = ValidationResult(
            is_valid=True, errors=[], warnings=[], metadata=metadata
        )

        assert result.is_valid is True
        assert len(result.errors) == 0
        assert len(result.warnings) == 0

    def test_create_invalid_result(self):
        """Test creating invalid validation result with errors"""
        from seo_manager.core.metadata import ContentType, Platform, SEOMetadata

        metadata = SEOMetadata(
            site_id="test",
            content_id="test_001",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,
            title="A" * 100,  # Too long
            description="Test",
            tags=["test"],
        )

        result = ValidationResult(
            is_valid=False,
            errors=["Title exceeds 70 character limit"],
            warnings=[],
            metadata=metadata,
        )

        assert result.is_valid is False
        assert len(result.errors) == 1


class TestPlatformValidator:
    """Test PlatformValidator functionality"""

    def test_validate_youtube_valid_metadata(self):
        """Test validating valid YouTube metadata"""
        adapter = YouTubeAdapter()
        validator = PlatformValidator(adapter)

        metadata = SEOMetadata(
            site_id="test",
            content_id="test_001",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,
            title="Great AI Tutorial",  # 18 chars, within 70 limit
            description="Learn AI fundamentals in this comprehensive guide",
            tags=["ai", "tutorial", "machine-learning"],
        )

        result = validator.validate(metadata)

        assert result.is_valid is True
        assert len(result.errors) == 0

    def test_validate_youtube_title_too_long(self):
        """Test detecting title that exceeds limit"""
        adapter = YouTubeAdapter()
        validator = PlatformValidator(adapter)

        metadata = SEOMetadata(
            site_id="test",
            content_id="test_001",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,
            title="A" * 75,  # Exceeds 70 char limit
            description="Test description",
            tags=["test"],
        )

        result = validator.validate(metadata)

        assert result.is_valid is False
        assert any("title" in err.lower() and "70" in err for err in result.errors)

    def test_validate_youtube_description_too_long(self):
        """Test detecting description that exceeds limit"""
        adapter = YouTubeAdapter()
        validator = PlatformValidator(adapter)

        metadata = SEOMetadata(
            site_id="test",
            content_id="test_001",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,
            title="Test",
            description="A" * 5001,  # Exceeds 5000 limit
            tags=["test"],
        )

        result = validator.validate(metadata)

        assert result.is_valid is False
        assert any(
            "description" in err.lower() and "5000" in err for err in result.errors
        )

    def test_validate_youtube_too_many_tags(self):
        """Test detecting too many tags"""
        adapter = YouTubeAdapter()
        validator = PlatformValidator(adapter)

        metadata = SEOMetadata(
            site_id="test",
            content_id="test_001",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,
            title="Test",
            description="Test description",
            tags=[f"tag_{i}" for i in range(20)],  # Exceeds 15 limit
        )

        result = validator.validate(metadata)

        assert result.is_valid is False
        assert any("tag" in err.lower() and "15" in err for err in result.errors)

    def test_validate_blog_title_limit(self):
        """Test blog title limit (60 chars for SERP)"""
        adapter = BlogAdapter()
        validator = PlatformValidator(adapter)

        metadata = SEOMetadata(
            site_id="test",
            content_id="test_001",
            platform=Platform.BLOG,
            content_type=ContentType.BLOG_POST,
            title="A" * 65,  # Exceeds 60 char limit
            description="Test",
            tags=["test"],
        )

        result = validator.validate(metadata)

        assert result.is_valid is False
        assert any("60" in err for err in result.errors)

    def test_validate_multiple_errors(self):
        """Test detection of multiple validation errors"""
        adapter = YouTubeAdapter()
        validator = PlatformValidator(adapter)

        metadata = SEOMetadata(
            site_id="test",
            content_id="test_001",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,
            title="A" * 80,  # Too long
            description="B" * 5100,  # Too long
            tags=[f"tag_{i}" for i in range(20)],  # Too many
        )

        result = validator.validate(metadata)

        assert result.is_valid is False
        # Should have 3 errors (title, description, tags)
        assert len(result.errors) >= 3

    def test_warnings_vs_errors(self):
        """Test that warnings don't invalidate metadata"""
        adapter = BlogAdapter()
        validator = PlatformValidator(adapter)

        metadata = SEOMetadata(
            site_id="test",
            content_id="test_001",
            platform=Platform.BLOG,
            content_type=ContentType.BLOG_POST,
            title="Short",  # Valid but suboptimal (< 50 chars)
            description="Also short",  # Valid but suboptimal
            tags=["test"],  # Valid but few tags
            quality_score=0.45,  # Low but not an error
        )

        result = validator.validate(metadata)

        # Should be valid (no hard errors)
        assert result.is_valid is True
        # But should have warnings
        assert len(result.warnings) > 0
