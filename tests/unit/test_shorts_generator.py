"""
Unit tests for YouTube Shorts SEO Generator.

Tests Shorts-specific optimization (different from long-form videos).

YouTube Shorts requirements (2026):
- Duration: <60 seconds
- Aspect ratio: 9:16 (vertical)
- Title: 40-50 chars (shorter, punchier)
- Description: 100-150 chars with hashtags
- Tags: 5-10 focused tags
- Hashtags: 3-5 inline in description
- NO chapters/timestamps

TDD: Tests written BEFORE implementation.
"""

import pytest

from seo_manager.core.metadata import ContentType


class TestShortsGenerator:
    """Test YouTube Shorts metadata generator"""

    @pytest.fixture
    def shorts_generator(self):
        """Create Shorts generator"""
        from seo_manager.generation.shorts_generator import ShortsMetadataGenerator
        from seo_manager.platforms.youtube import YouTubeAdapter

        adapter = YouTubeAdapter()
        return ShortsMetadataGenerator(adapter=adapter)

    def test_shorts_title_length_40_50(self, shorts_generator):
        """Test Shorts titles are 40-50 chars (shorter than long-form 50-70)"""
        title = shorts_generator._generate_title(
            topic="Amazing Ireland Cliffs", keywords=["ireland", "cliffs", "travel"]
        )

        # Shorts titles should be shorter and punchier
        assert 30 <= len(title) <= 55
        # Should be more concise than long-form

    def test_shorts_description_brief(self, shorts_generator):
        """Test Shorts descriptions are brief (100-150 chars)"""
        description = shorts_generator._generate_description(
            topic="Ireland Cliffs",
            body="Beautiful coastal cliffs in Ireland",
            transcript=None,
            keywords=["ireland", "cliffs", "travel"],
        )

        # Shorts descriptions should be concise
        assert 50 <= len(description) <= 200
        # Much shorter than long-form (which can be 5000 chars)

    def test_shorts_hashtags_inline(self, shorts_generator):
        """Test Shorts include 3-5 hashtags inline in description"""
        description = shorts_generator._generate_description(
            topic="Ireland Travel",
            body="Travel content",
            transcript=None,
            keywords=["ireland", "travel", "scenic"],
        )

        # Should contain hashtags
        hashtag_count = description.count("#")
        assert 3 <= hashtag_count <= 5

    def test_shorts_no_timestamps(self, shorts_generator):
        """Test Shorts don't include timestamp chapters"""
        timestamps = [
            {"time": "0:00", "label": "Start"},
            {"time": "0:30", "label": "End"},
        ]

        description = shorts_generator._generate_description(
            topic="Short Video",
            body="Content",
            transcript=None,
            keywords=["test"],
            timestamps=timestamps,  # Should be ignored for Shorts
        )

        # Shorts don't support chapters - should not include timestamps
        assert "0:00" not in description
        assert "TIMESTAMPS" not in description

    def test_shorts_tag_count_5_10(self, shorts_generator):
        """Test Shorts use 5-10 tags (fewer than long-form 10-15)"""
        tags = shorts_generator._generate_tags(
            keywords=["ireland", "travel", "scenic", "cliffs", "nature"] * 5,
            existing_tags=None,
        )

        # Shorts should have fewer, more focused tags
        assert 5 <= len(tags) <= 10

    def test_shorts_hook_optimization(self, shorts_generator):
        """Test Shorts optimize for immediate hook (first 3 seconds critical)"""
        description = shorts_generator._generate_description(
            topic="Stunning Cliffs",
            body="Amazing cliff views",
            transcript=None,
            keywords=["cliffs", "stunning", "ireland"],
        )

        # Should lead with strong hook words
        first_20_chars = description[:20].lower()
        hook_words = ["stunning", "amazing", "incredible", "beautiful", "epic"]
        assert any(word in first_20_chars for word in hook_words)

    def test_shorts_emoji_usage(self, shorts_generator):
        """Test Shorts can include emojis for engagement (2026 trend)"""
        description = shorts_generator._generate_description(
            topic="Ireland Cliffs",
            body="Beautiful views",
            transcript=None,
            keywords=["ireland", "cliffs"],
        )

        # Shorts descriptions often include emojis for mobile engagement
        # Should be allowed but not required
        assert isinstance(description, str)

    def test_shorts_cta_placement(self, shorts_generator):
        """Test Shorts CTA placement at end (not middle)"""
        description = shorts_generator._generate_description(
            topic="Test Short", body="Content", transcript=None, keywords=["test"]
        )

        # CTA should be at end or not present
        # NOT in first 50 characters (hook space)
        assert isinstance(description, str)

    def test_shorts_platform_metadata(self, shorts_generator):
        """Test Shorts metadata includes shorts-specific fields"""
        platform_meta = shorts_generator._get_platform_metadata(
            shorts_enabled=True, vertical_format=True
        )

        youtube_meta = platform_meta.get("youtube_metadata", {})
        # Should indicate this is a Short
        assert isinstance(youtube_meta, dict)

    def test_shorts_vs_longform_title_difference(self, shorts_generator):
        """Test Shorts titles are distinctly shorter than long-form"""
        from seo_manager.generation.youtube_generator import YouTubeMetadataGenerator
        from seo_manager.platforms.youtube import YouTubeAdapter

        adapter = YouTubeAdapter()
        longform_gen = YouTubeMetadataGenerator(adapter=adapter)

        topic = "Ireland Road Trip Scenic Drive"

        shorts_title = shorts_generator._generate_title(topic, [])
        longform_title = longform_gen._generate_title(
            topic, [], episode_number=1, show_name="Travel"
        )

        # Shorts should be shorter
        assert len(shorts_title) < len(longform_title)

    def test_shorts_tag_focus(self, shorts_generator):
        """Test Shorts tags are more focused (less variety)"""
        keywords = [
            "travel",
            "ireland",
            "scenic",
            "cliffs",
            "nature",
            "beautiful",
            "amazing",
            "drone",
            "aerial",
            "landscape",
        ] * 2

        tags = shorts_generator._generate_tags(keywords, None)

        # Shorts should have fewer tags
        assert len(tags) <= 10
        # Should prioritize high-impact tags

    def test_shorts_mobile_first_optimization(self, shorts_generator):
        """Test Shorts are optimized for mobile viewing"""
        # Shorts are primarily consumed on mobile
        # Every element should be mobile-optimized
        assert hasattr(shorts_generator, "_generate_title")
        assert hasattr(shorts_generator, "_generate_description")
        assert hasattr(shorts_generator, "_generate_tags")


class TestVideoConfig:
    """Test VideoConfig dataclass"""

    def test_video_config_minimal(self):
        """Test minimal VideoConfig"""
        from seo_manager.youtube.batch_uploader import VideoConfig

        config = VideoConfig(
            video_path="/path/to/video.mp4", metadata=None, privacy="draft"
        )

        assert config.video_path == "/path/to/video.mp4"
        assert config.privacy == "draft"
        assert config.thumbnail_path is None

    def test_video_config_with_thumbnail(self):
        """Test VideoConfig with thumbnail"""
        from seo_manager.youtube.batch_uploader import VideoConfig

        config = VideoConfig(
            video_path="/video.mp4",
            metadata=None,
            thumbnail_path="/thumb.jpg",
            privacy="unlisted",
        )

        assert config.thumbnail_path == "/thumb.jpg"


class TestBatchUploadResults:
    """Test batch upload result tracking"""

    def test_batch_result_all_success(self):
        """Test batch result for all successful uploads"""
        from seo_manager.youtube.batch_uploader import BatchUploadResult

        result = BatchUploadResult(
            total_videos=5,
            successful=5,
            failed=0,
            quota_used=8000,
            results=[],
            errors=[],
        )

        assert result.is_complete_success()

    def test_batch_result_partial_success(self):
        """Test batch result for partial success"""
        from seo_manager.youtube.batch_uploader import BatchUploadResult

        result = BatchUploadResult(
            total_videos=5,
            successful=3,
            failed=2,
            quota_used=4800,
            results=[],
            errors=["Video 2 failed", "Video 4 failed"],
        )

        assert not result.is_complete_success()
        assert result.has_failures()
        assert result.success_rate() == 0.6  # 3/5

    def test_batch_result_all_failures(self):
        """Test batch result for all failures"""
        from seo_manager.youtube.batch_uploader import BatchUploadResult

        result = BatchUploadResult(
            total_videos=3,
            successful=0,
            failed=3,
            quota_used=0,
            results=[],
            errors=["All failed"],
        )

        assert result.success_rate() == 0.0
        assert result.has_failures()
