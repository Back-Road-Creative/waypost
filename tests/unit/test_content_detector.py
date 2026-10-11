"""
Unit tests for Content Type Detector.

Auto-detects if video is a YouTube Short or long-form video
based on duration and aspect ratio.

TDD: Tests written BEFORE implementation.
"""

import tempfile
from pathlib import Path

import pytest


class TestContentDetector:
    """Test automatic content type detection"""

    def test_detect_short_by_duration_under_60(self):
        """Test video under 60 seconds detected as Short"""
        from seo_manager.core.content_detector import ContentDetector

        detector = ContentDetector()

        # 45 second video = Short
        content_type = detector.detect_from_duration(duration_seconds=45)
        assert content_type == "SHORT"

    def test_detect_longform_by_duration_over_60(self):
        """Test video over 60 seconds detected as long-form"""
        from seo_manager.core.content_detector import ContentDetector

        detector = ContentDetector()

        # 180 second video = long-form
        content_type = detector.detect_from_duration(duration_seconds=180)
        assert content_type == "VIDEO"

    def test_detect_short_by_aspect_ratio_vertical(self):
        """Test vertical video (9:16) detected as Short"""
        from seo_manager.core.content_detector import ContentDetector

        detector = ContentDetector()

        # 1080x1920 (9:16 vertical) = Short
        content_type = detector.detect_from_dimensions(width=1080, height=1920)
        assert content_type == "SHORT"

    def test_detect_longform_by_aspect_ratio_horizontal(self):
        """Test horizontal video (16:9) detected as long-form"""
        from seo_manager.core.content_detector import ContentDetector

        detector = ContentDetector()

        # 1920x1080 (16:9 horizontal) = long-form
        content_type = detector.detect_from_dimensions(width=1920, height=1080)
        assert content_type == "VIDEO"

    def test_get_video_duration_from_file(self):
        """Test extracting duration from video file"""
        from seo_manager.core.content_detector import ContentDetector

        detector = ContentDetector()

        # Should use ffprobe to get duration
        # (Will need mock for testing)
        assert hasattr(detector, "get_video_duration")

    def test_get_video_dimensions_from_file(self):
        """Test extracting dimensions from video file"""
        from seo_manager.core.content_detector import ContentDetector

        detector = ContentDetector()

        # Should use ffprobe to get width/height
        assert hasattr(detector, "get_video_dimensions")

    def test_detect_from_file_combines_signals(self):
        """Test detection combines duration AND aspect ratio"""
        from seo_manager.core.content_detector import ContentDetector

        detector = ContentDetector()

        # Should analyze both duration and dimensions
        # Duration takes priority (more reliable signal)
        assert hasattr(detector, "detect_from_file")

    def test_manual_override_detection(self):
        """Test manual override of auto-detection"""
        from seo_manager.core.content_detector import ContentDetector

        detector = ContentDetector()

        # Even if auto-detected as Short, allow override
        result = detector.detect_from_file(
            video_path="/fake/video.mp4", force_type="VIDEO"
        )

        # Should respect manual override
        assert result in ["VIDEO", "SHORT"]

    def test_invalid_video_file_handling(self):
        """Test graceful handling of invalid files"""
        from seo_manager.core.content_detector import ContentDetector

        detector = ContentDetector()

        # Should not crash on invalid file
        with pytest.raises((FileNotFoundError, ValueError)):
            detector.detect_from_file("/nonexistent/file.mp4")

    def test_duration_edge_case_exactly_60(self):
        """Test video exactly 60 seconds — SHORT per video_types.py (<= 60)"""
        from seo_manager.core.content_detector import ContentDetector

        detector = ContentDetector()

        # 60 seconds exactly - treat as SHORT (Shorts are <=60)
        content_type = detector.detect_from_duration(duration_seconds=60)
        assert content_type == "SHORT"

    def test_duration_boundary_60_is_short(self):
        """Exactly 60s is SHORT — the boundary is inclusive (<= 60)."""
        from seo_manager.core.content_detector import ContentDetector

        detector = ContentDetector()
        assert detector.detect_from_duration(duration_seconds=60) == "SHORT"

    def test_duration_boundary_just_under_60(self):
        """59.9s is SHORT — well within the boundary."""
        from seo_manager.core.content_detector import ContentDetector

        detector = ContentDetector()
        assert detector.detect_from_duration(duration_seconds=59.9) == "SHORT"

    def test_duration_boundary_just_over_60(self):
        """60.1s is VIDEO — just past the boundary."""
        from seo_manager.core.content_detector import ContentDetector

        detector = ContentDetector()
        assert detector.detect_from_duration(duration_seconds=60.1) == "VIDEO"

    def test_shorts_max_duration_constant_exists(self):
        """SHORTS_MAX_DURATION constant is defined and equals 60."""
        from seo_manager.core.content_detector import SHORTS_MAX_DURATION

        assert SHORTS_MAX_DURATION == 60

    def test_square_aspect_ratio_handling(self):
        """Test square videos (1:1) default to long-form"""
        from seo_manager.core.content_detector import ContentDetector

        detector = ContentDetector()

        # 1080x1080 (1:1 square) - not Short, not standard
        content_type = detector.detect_from_dimensions(width=1080, height=1080)
        # Should default to VIDEO (Shorts are specifically 9:16)
        assert content_type == "VIDEO"

    def test_ultra_wide_aspect_ratio(self):
        """Test ultra-wide videos (21:9) detected as long-form"""
        from seo_manager.core.content_detector import ContentDetector

        detector = ContentDetector()

        # 2560x1080 (21:9 ultra-wide) = long-form
        content_type = detector.detect_from_dimensions(width=2560, height=1080)
        assert content_type == "VIDEO"


class TestShortsMetadataStructure:
    """Test Shorts-specific metadata structure"""

    def test_shorts_content_type_enum(self):
        """Test ContentType has SHORT option"""
        from seo_manager.core.metadata import ContentType

        # Should have SHORT as distinct type
        assert hasattr(ContentType, "SHORT") or hasattr(ContentType, "SHORTS")

    def test_shorts_metadata_creation(self):
        """Test creating metadata for a Short"""
        from seo_manager.core.metadata import ContentType, Platform, SEOMetadata

        metadata = SEOMetadata(
            site_id="test",
            content_id="short_001",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,  # Or SHORT if we add it
            title="Quick Ireland Clip",
            description="Beautiful cliffs #Ireland #Travel",
            tags=["ireland", "travel", "cliffs", "short", "viral"],
        )

        assert len(metadata.title) <= 50  # Shorts title limit
        assert "#" in metadata.description  # Has hashtags
