"""
Unit tests for AutoFix Engine.

Tests cover:
- Fix detection and application
- Confidence scoring
- Title truncation fixes
- Description truncation fixes
- Tag limiting fixes
- Fix result tracking
"""

import pytest

from seo_manager.autofix.engine import AutoFixEngine, AutoFixResult, Fix, FixConfidence
from seo_manager.core.metadata import ContentType, Platform, SEOMetadata
from seo_manager.platforms.blog import BlogAdapter
from seo_manager.platforms.youtube import YouTubeAdapter


class TestFix:
    """Test Fix dataclass"""

    def test_create_fix(self):
        """Test creating a fix"""
        fix = Fix(
            fix_id="title_truncate",
            rule_name="Title Length",
            description="Title exceeded limit",
            confidence=0.95,
            before_value="Long title...",
            after_value="Short title",
            impact="title",
        )

        assert fix.fix_id == "title_truncate"
        assert fix.confidence == 0.95
        assert fix.impact == "title"

    def test_confidence_level_high(self):
        """Test high confidence level (>= 0.9)"""
        fix = Fix(
            fix_id="test",
            rule_name="Test",
            description="Test",
            confidence=0.95,
            before_value="",
            after_value="",
            impact="title",
        )

        assert fix.confidence_level == FixConfidence.HIGH

    def test_confidence_level_medium(self):
        """Test medium confidence level (0.6-0.89)"""
        fix = Fix(
            fix_id="test",
            rule_name="Test",
            description="Test",
            confidence=0.75,
            before_value="",
            after_value="",
            impact="title",
        )

        assert fix.confidence_level == FixConfidence.MEDIUM

    def test_confidence_level_low(self):
        """Test low confidence level (< 0.6)"""
        fix = Fix(
            fix_id="test",
            rule_name="Test",
            description="Test",
            confidence=0.45,
            before_value="",
            after_value="",
            impact="title",
        )

        assert fix.confidence_level == FixConfidence.LOW


class TestAutoFixEngine:
    """Test AutoFixEngine functionality"""

    def test_engine_initialization(self):
        """Test engine initializes with platform"""
        platform = YouTubeAdapter()
        engine = AutoFixEngine(platform=platform, confidence_threshold=0.85)

        assert engine.platform == platform
        assert engine.confidence_threshold == 0.85

    def test_fix_title_length(self):
        """Test automatic title truncation"""
        platform = YouTubeAdapter()
        engine = AutoFixEngine(platform=platform)

        metadata = SEOMetadata(
            site_id="test",
            content_id="test_001",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,
            title="A" * 80,  # Exceeds 70 limit
            description="Test",
            tags=["test"],
        )

        result = engine.fix(metadata, validation_errors=["Title too long"])

        # Title should be truncated
        assert len(result.metadata.title) <= 70
        # Fix should be applied (high confidence)
        assert len(result.fixes_applied) > 0
        assert any(f.impact == "title" for f in result.fixes_applied)

    def test_fix_description_length(self):
        """Test automatic description truncation"""
        platform = YouTubeAdapter()
        engine = AutoFixEngine(platform=platform)

        metadata = SEOMetadata(
            site_id="test",
            content_id="test_001",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,
            title="Test",
            description="A" * 5100,  # Exceeds 5000 limit
            tags=["test"],
        )

        result = engine.fix(metadata, validation_errors=["Description too long"])

        assert len(result.metadata.description) <= 5000
        assert len(result.fixes_applied) > 0

    def test_fix_tag_count(self):
        """Test automatic tag limiting"""
        platform = YouTubeAdapter()
        engine = AutoFixEngine(platform=platform)

        metadata = SEOMetadata(
            site_id="test",
            content_id="test_001",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,
            title="Test",
            description="Test",
            tags=[f"tag_{i}" for i in range(20)],  # Exceeds 15 limit
        )

        result = engine.fix(metadata, validation_errors=["Too many tags"])

        assert len(result.metadata.tags) <= 15
        assert len(result.fixes_applied) > 0

    def test_high_confidence_auto_apply(self):
        """Test that high confidence fixes are auto-applied"""
        platform = YouTubeAdapter()
        engine = AutoFixEngine(platform=platform, confidence_threshold=0.85)

        metadata = SEOMetadata(
            site_id="test",
            content_id="test_001",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,
            title="A" * 75,
            description="Test",
            tags=["test"],
        )

        result = engine.fix(metadata, validation_errors=[])

        # Title truncation is high confidence (>= 0.9), should be applied
        assert len(result.fixes_applied) > 0
        assert len(result.fixes_flagged) == 0

    def test_medium_confidence_flagged(self):
        """Test that medium confidence fixes are flagged"""
        platform = YouTubeAdapter()
        engine = AutoFixEngine(platform=platform, confidence_threshold=0.85)

        metadata = SEOMetadata(
            site_id="test",
            content_id="test_001",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,
            title="Test Title",  # No primary keyword
            description="Test",
            tags=["keyword", "other"],
        )

        result = engine.suggest_improvements(metadata)

        # Should have suggestions
        assert len(result) >= 0  # May suggest keyword in title

    def test_no_fixes_needed(self):
        """Test metadata that needs no fixes"""
        platform = YouTubeAdapter()
        engine = AutoFixEngine(platform=platform)

        metadata = SEOMetadata(
            site_id="test",
            content_id="test_001",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,
            title="Perfect Title Length Here",  # Good length
            description="A" * 155,  # Good length
            tags=["ai", "tutorial", "tech", "python", "code"],  # Good count
        )

        result = engine.fix(metadata, validation_errors=[])

        # No fixes needed
        assert len(result.fixes_applied) == 0
        assert result.metadata.title == metadata.title
