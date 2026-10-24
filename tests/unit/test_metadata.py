"""
Unit tests for SEOMetadata dataclass.

Tests cover:
- Dataclass creation and immutability
- Serialization (to_dict / from_dict)
- Validation and field constraints
- Default values
"""

from datetime import datetime

import pytest

from seo_manager.core.metadata import ContentType, Platform, SEOMetadata


class TestSEOMetadata:
    """Test SEOMetadata dataclass"""

    def test_create_basic_metadata(self):
        """Test creating basic SEO metadata"""
        metadata = SEOMetadata(
            site_id="test_site",
            content_id="test_content_001",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,
            title="Test Video Title",
            description="Test description for video",
            tags=["test", "video", "seo"],
        )

        assert metadata.site_id == "test_site"
        assert metadata.content_id == "test_content_001"
        assert metadata.platform == Platform.YOUTUBE
        assert metadata.content_type == ContentType.VIDEO
        assert metadata.title == "Test Video Title"
        assert metadata.description == "Test description for video"
        assert metadata.tags == ["test", "video", "seo"]

    def test_required_fields_validation(self):
        """Test that required fields must be provided"""
        with pytest.raises(TypeError):
            # Missing required fields should raise TypeError
            SEOMetadata(
                site_id="test_site",
                platform=Platform.YOUTUBE,
                # Missing content_id, content_type, title, description, tags
            )

    def test_optional_fields_defaults(self):
        """Test that optional fields have correct defaults"""
        metadata = SEOMetadata(
            site_id="test_site",
            content_id="test_001",
            platform=Platform.LINKEDIN,
            content_type=ContentType.BLOG_POST,
            title="Test Title",
            description="Test description",
            tags=["test"],
        )

        assert metadata.url is None
        assert metadata.canonical_url is None
        assert metadata.thumbnail_url is None
        assert metadata.author is None
        assert metadata.published_date is None
        assert metadata.youtube_metadata == {}
        assert metadata.blog_metadata == {}
        assert metadata.social_metadata == {}
        assert metadata.quality_score is None
        assert metadata.keyword_density is None
        assert metadata.readability_score is None
        assert metadata.validation_status == "pending"
        assert metadata.validation_errors == []
        assert metadata.fixes_applied == []
        assert metadata.generated_by == "seo_manager"
        assert metadata.version == 1

    def test_generated_at_timestamp(self):
        """Test that generated_at is auto-populated with ISO timestamp"""
        metadata = SEOMetadata(
            site_id="test_site",
            content_id="test_001",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,
            title="Test",
            description="Test",
            tags=["test"],
        )

        # Check it's a valid ISO 8601 timestamp
        assert metadata.generated_at is not None
        # Should be parseable as datetime
        parsed = datetime.fromisoformat(metadata.generated_at.replace("Z", "+00:00"))
        assert isinstance(parsed, datetime)

    def test_immutability(self):
        """Test that metadata is immutable (frozen dataclass)"""
        metadata = SEOMetadata(
            site_id="test_site",
            content_id="test_001",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,
            title="Original Title",
            description="Original description",
            tags=["test"],
        )

        # Attempting to modify should raise FrozenInstanceError
        with pytest.raises(Exception):  # dataclasses.FrozenInstanceError  # noqa: B017
            metadata.title = "Modified Title"

    def test_to_dict_serialization(self):
        """Test serialization to dict"""
        metadata = SEOMetadata(
            site_id="test_site",
            content_id="test_001",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,
            title="Test Title",
            description="Test description",
            tags=["ai", "seo"],
            quality_score=0.85,
            keyword_density=0.05,
        )

        data = metadata.to_dict()

        assert isinstance(data, dict)
        assert data["site_id"] == "test_site"
        assert data["content_id"] == "test_001"
        assert data["platform"] == "youtube"  # Enum converted to string
        assert data["content_type"] == "video"  # Enum converted to string
        assert data["title"] == "Test Title"
        assert data["tags"] == ["ai", "seo"]
        assert data["quality_score"] == 0.85

    def test_from_dict_deserialization(self):
        """Test deserialization from dict"""
        data = {
            "site_id": "test_site",
            "content_id": "test_001",
            "platform": "youtube",
            "content_type": "video",
            "title": "Test Title",
            "description": "Test description",
            "tags": ["ai", "seo"],
            "quality_score": 0.85,
            "generated_at": "2026-01-16T12:00:00.000000",
            "generated_by": "seo_manager",
            "version": 1,
            "validation_status": "valid",
            "validation_errors": [],
            "fixes_applied": [],
            "youtube_metadata": {},
            "blog_metadata": {},
            "social_metadata": {},
        }

        metadata = SEOMetadata.from_dict(data)

        assert metadata.site_id == "test_site"
        assert metadata.platform == Platform.YOUTUBE
        assert metadata.content_type == ContentType.VIDEO
        assert metadata.title == "Test Title"
        assert metadata.quality_score == 0.85

    def test_roundtrip_serialization(self):
        """Test that to_dict -> from_dict roundtrip preserves data"""
        original = SEOMetadata(
            site_id="test_site",
            content_id="test_001",
            platform=Platform.LINKEDIN,
            content_type=ContentType.BLOG_POST,
            title="Test Title",
            description="Test description",
            tags=["ai", "seo"],
            quality_score=0.92,
            keyword_density=0.03,
            readability_score=68.5,
            blog_metadata={"categories": ["Tech"], "series": "AI Series"},
        )

        # Serialize and deserialize
        data = original.to_dict()
        restored = SEOMetadata.from_dict(data)

        assert restored.site_id == original.site_id
        assert restored.content_id == original.content_id
        assert restored.platform == original.platform
        assert restored.content_type == original.content_type
        assert restored.title == original.title
        assert restored.description == original.description
        assert restored.tags == original.tags
        assert restored.quality_score == original.quality_score
        assert restored.keyword_density == original.keyword_density
        assert restored.readability_score == original.readability_score
        assert restored.blog_metadata == original.blog_metadata

    def test_with_fixes_creates_new_instance(self):
        """Test that with_fixes() creates new instance (immutable pattern)"""
        original = SEOMetadata(
            site_id="test_site",
            content_id="test_001",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,
            title="Test Title",
            description="Test description",
            tags=["test"],
            fixes_applied=[],
        )

        # Apply fixes (should create new instance)
        fixed = original.with_fixes(["title_truncated", "tags_optimized"])

        # Original should be unchanged
        assert original.fixes_applied == []

        # New instance should have fixes
        assert fixed.fixes_applied == ["title_truncated", "tags_optimized"]

        # Other fields should be identical
        assert fixed.site_id == original.site_id
        assert fixed.title == original.title

    def test_platform_enum_values(self):
        """Test Platform enum has expected values"""
        assert Platform.YOUTUBE.value == "youtube"
        assert Platform.INSTAGRAM.value == "instagram"
        assert Platform.TIKTOK.value == "tiktok"
        assert Platform.LINKEDIN.value == "linkedin"

    def test_content_type_enum_values(self):
        """Test ContentType enum has expected values"""
        assert ContentType.VIDEO.value == "video"
        assert ContentType.BLOG_POST.value == "blog_post"
        assert ContentType.SOCIAL_POST.value == "social_post"
        assert ContentType.PODCAST.value == "podcast"
        assert ContentType.TRAIL_GUIDE.value == "trail_guide"

    def test_platform_specific_metadata(self):
        """Test platform-specific metadata dictionaries"""
        # YouTube metadata
        youtube_meta = SEOMetadata(
            site_id="test_site",
            content_id="vid_001",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,
            title="Test",
            description="Test",
            tags=["test"],
            youtube_metadata={
                "category": "Science & Technology",
                "playlist": "Season 1",
                "visibility": "public",
            },
        )

        assert youtube_meta.youtube_metadata["category"] == "Science & Technology"
        assert youtube_meta.blog_metadata == {}

        # Blog metadata
        blog_meta = SEOMetadata(
            site_id="test_site",
            content_id="post_001",
            platform=Platform.LINKEDIN,
            content_type=ContentType.BLOG_POST,
            title="Test",
            description="Test",
            tags=["test"],
            blog_metadata={"categories": ["Technology", "AI"], "series": "AI Basics"},
        )

        assert blog_meta.blog_metadata["categories"] == ["Technology", "AI"]
        assert blog_meta.youtube_metadata == {}

    def test_validation_status_tracking(self):
        """Test validation status and errors tracking"""
        metadata = SEOMetadata(
            site_id="test_site",
            content_id="test_001",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,
            title="Test",
            description="Test",
            tags=["test"],
            validation_status="invalid",
            validation_errors=[
                "Title exceeds 70 character limit",
                "Missing primary keyword in title",
            ],
        )

        assert metadata.validation_status == "invalid"
        assert len(metadata.validation_errors) == 2
        assert "Title exceeds 70 character limit" in metadata.validation_errors

    def test_quality_metrics(self):
        """Test quality score metrics"""
        metadata = SEOMetadata(
            site_id="test_site",
            content_id="test_001",
            platform=Platform.LINKEDIN,
            content_type=ContentType.BLOG_POST,
            title="Test Title",
            description="Test description",
            tags=["test"],
            quality_score=0.87,
            keyword_density=0.045,
            readability_score=72.3,
        )

        assert 0.0 <= metadata.quality_score <= 1.0
        assert 0.0 <= metadata.keyword_density <= 1.0
        assert 0.0 <= metadata.readability_score <= 100.0
