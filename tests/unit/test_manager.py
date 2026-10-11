"""
Unit tests for SEOManager orchestrator.

Tests cover:
- Manager initialization with site and platform
- Metadata generation (end-to-end)
- Metadata saving to event store
- Retrieval operations
- Site isolation
- Platform-specific behavior
"""

import shutil
import tempfile
from pathlib import Path

import pytest

from seo_manager.core.manager import SEOManager
from seo_manager.core.metadata import ContentType, Platform
from seo_manager.platforms.blog import BlogAdapter
from seo_manager.platforms.youtube import YouTubeAdapter


class TestSEOManager:
    """Test SEOManager orchestrator"""

    @pytest.fixture
    def temp_storage(self):
        """Create temporary storage directory"""
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)

    @pytest.fixture
    def youtube_manager(self, temp_storage):
        """Create YouTube SEO manager"""
        return SEOManager(
            site_id="test_youtube_site",
            platform=YouTubeAdapter(),
            storage_path=temp_storage,
        )

    @pytest.fixture
    def blog_manager(self, temp_storage):
        """Create Blog SEO manager"""
        return SEOManager(
            site_id="test_blog_site", platform=BlogAdapter(), storage_path=temp_storage
        )

    def test_manager_initialization(self, youtube_manager):
        """Test manager initializes correctly"""
        assert youtube_manager.site_id == "test_youtube_site"
        assert youtube_manager.platform.platform_type == Platform.YOUTUBE
        assert youtube_manager.store is not None

    def test_generate_youtube_metadata(self, youtube_manager):
        """Test generating YouTube metadata end-to-end"""
        metadata = youtube_manager.generate(
            content_type=ContentType.VIDEO,
            topic="Understanding AI Safety",
            body="This video discusses AI safety concerns and solutions.",
            existing_tags=["ai", "safety"],
        )

        # Should return SEOMetadata
        assert metadata.site_id == "test_youtube_site"
        assert metadata.platform == Platform.YOUTUBE
        assert metadata.content_type == ContentType.VIDEO
        assert metadata.title is not None
        assert len(metadata.title) <= 70  # YouTube limit
        assert metadata.description is not None
        assert len(metadata.tags) > 0
        # Quality score should be calculated
        assert metadata.quality_score is not None
        assert 0.0 <= metadata.quality_score <= 1.0

    def test_generate_blog_metadata(self, blog_manager):
        """Test generating blog metadata end-to-end"""
        metadata = blog_manager.generate(
            content_type=ContentType.BLOG_POST,
            topic="Python Best Practices",
            body="A comprehensive guide to Python programming best practices...",
            existing_tags=["python", "programming"],
        )

        assert metadata.site_id == "test_blog_site"
        assert metadata.platform == Platform.BLOG
        assert metadata.content_type == ContentType.BLOG_POST
        assert len(metadata.title) <= 60  # Blog SERP limit
        assert len(metadata.description) <= 160  # Meta description limit

    def test_save_metadata(self, youtube_manager):
        """Test saving metadata to event store"""
        metadata = youtube_manager.generate(
            content_type=ContentType.VIDEO, topic="AI Testing", body="Test content"
        )

        event_id = youtube_manager.save(metadata)

        assert event_id is not None
        assert isinstance(event_id, str)

    def test_get_metadata(self, youtube_manager):
        """Test retrieving saved metadata"""
        # Generate and save
        metadata = youtube_manager.generate(
            content_type=ContentType.VIDEO, topic="Test Topic", body="Test body"
        )
        youtube_manager.save(metadata)

        # Retrieve
        retrieved = youtube_manager.get_metadata(metadata.content_id)

        assert retrieved is not None
        assert retrieved.content_id == metadata.content_id
        assert retrieved.title == metadata.title

    def test_site_isolation(self, temp_storage):
        """Test that different sites have isolated data"""
        youtube_mgr = SEOManager(
            site_id="site_youtube", platform=YouTubeAdapter(), storage_path=temp_storage
        )

        blog_mgr = SEOManager(
            site_id="site_blog", platform=BlogAdapter(), storage_path=temp_storage
        )

        # Generate metadata for each
        youtube_meta = youtube_mgr.generate(
            content_type=ContentType.VIDEO, topic="YouTube Video", body="Video content"
        )
        youtube_mgr.save(youtube_meta)

        blog_meta = blog_mgr.generate(
            content_type=ContentType.BLOG_POST, topic="Blog Post", body="Blog content"
        )
        blog_mgr.save(blog_meta)

        # Each manager should only see its own data
        youtube_retrieved = youtube_mgr.get_metadata(youtube_meta.content_id)
        blog_retrieved = blog_mgr.get_metadata(blog_meta.content_id)

        assert youtube_retrieved is not None
        assert blog_retrieved is not None
        assert youtube_retrieved.site_id == "site_youtube"
        assert blog_retrieved.site_id == "site_blog"

        # Cross-site queries should return None
        assert youtube_mgr.get_metadata(blog_meta.content_id) is None
        assert blog_mgr.get_metadata(youtube_meta.content_id) is None

    def test_platform_specific_limits_enforced(self, youtube_manager, blog_manager):
        """Test that platform limits are enforced"""
        long_topic = "A" * 100

        # YouTube (70 char limit)
        youtube_meta = youtube_manager.generate(
            content_type=ContentType.VIDEO, topic=long_topic, body="Test"
        )
        assert len(youtube_meta.title) <= 70

        # Blog (60 char limit)
        blog_meta = blog_manager.generate(
            content_type=ContentType.BLOG_POST, topic=long_topic, body="Test"
        )
        assert len(blog_meta.title) <= 60

    def test_get_metadata_nonexistent(self, youtube_manager):
        """Test retrieving non-existent metadata returns None"""
        result = youtube_manager.get_metadata("nonexistent_content_id")

        assert result is None

    def test_generate_with_transcript(self, youtube_manager):
        """Test generating metadata with transcript data"""
        transcript = [
            {"speaker": "Host", "text": "Welcome to our podcast about AI"},
            {"speaker": "Guest", "text": "AI is transforming technology"},
        ]

        metadata = youtube_manager.generate(
            content_type=ContentType.VIDEO, topic="AI Podcast", transcript=transcript
        )

        assert metadata is not None
        # Keywords should be extracted from transcript
        assert len(metadata.tags) > 0
        # At least some keywords should be present
        assert any(
            tag in ["ai", "podcast", "technology", "transforming"]
            for tag in metadata.tags
        )

    def test_base_tags_inclusion(self, youtube_manager):
        """Test that base tags are included in generated metadata"""
        metadata = youtube_manager.generate(
            content_type=ContentType.VIDEO,
            topic="Test Topic",
            body="Test content",
            base_tags=["podcast", "technology"],
        )

        # Base tags should be in final tag list
        assert "podcast" in metadata.tags
        assert "technology" in metadata.tags

    def test_quality_metrics_calculated(self, youtube_manager):
        """Test that quality metrics are automatically calculated"""
        metadata = youtube_manager.generate(
            content_type=ContentType.VIDEO,
            topic="Python Programming Tutorial",
            body="Learn Python programming from scratch. This tutorial covers basics.",
        )

        # Quality score should be calculated
        assert metadata.quality_score is not None
        assert isinstance(metadata.quality_score, float)
        assert 0.0 <= metadata.quality_score <= 1.0

        # Keyword density should be calculated
        assert metadata.keyword_density is not None
        assert isinstance(metadata.keyword_density, float)

        # Readability should be calculated
        assert metadata.readability_score is not None
        assert isinstance(metadata.readability_score, float)

    def test_content_id_generation(self, youtube_manager):
        """Test that content_id is auto-generated if not provided"""
        metadata = youtube_manager.generate(
            content_type=ContentType.VIDEO, topic="Test Video", body="Content"
        )

        # Should have generated content_id
        assert metadata.content_id is not None
        assert isinstance(metadata.content_id, str)
        assert len(metadata.content_id) > 0
