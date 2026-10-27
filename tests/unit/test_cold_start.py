"""
Unit tests for Cold Start Scenarios.

Tests the system's ability to generate optimized SEO metadata
when NO historical analytics data exists (first video on new channel).

Critical Requirements:
- Must work with zero historical videos
- Must still follow YouTube best practices
- Must generate quality metadata without pattern learning
- Must track first video for future pattern building

TDD: Tests written BEFORE implementation.
"""

import shutil
import tempfile
from pathlib import Path

import pytest

from seo_manager import SEOManager
from seo_manager.core.metadata import ContentType
from seo_manager.platforms import YouTubeAdapter


class TestColdStartSEO:
    """Test SEO generation with no historical data"""

    @pytest.fixture
    def empty_manager(self):
        """Create manager with no historical data"""
        temp_dir = tempfile.mkdtemp()
        manager = SEOManager(
            site_id="brand_new_channel",
            platform=YouTubeAdapter(),
            storage_path=Path(temp_dir),
        )
        yield manager
        shutil.rmtree(temp_dir)

    def test_generate_metadata_zero_history(self, empty_manager):
        """Test generating metadata with zero historical videos"""
        # Should work even with no analytics
        metadata = empty_manager.generate(
            content_type=ContentType.VIDEO,
            topic="My First YouTube Video",
            body="This is my first video on this channel",
            existing_tags=["first", "introduction"],
        )

        assert metadata is not None
        assert len(metadata.title) > 0
        assert len(metadata.description) > 0
        assert len(metadata.tags) > 0

    def test_cold_start_uses_best_practices(self, empty_manager):
        """Test cold start falls back to industry best practices"""
        metadata = empty_manager.generate(
            content_type=ContentType.VIDEO,
            topic="Travel Video Ireland",
            body="Travel content",
            existing_tags=["travel", "ireland"],
        )

        # Should follow best practices even without data
        assert 30 <= len(metadata.title) <= 80  # Reasonable title length
        # Substantial description = the hook names the topic and the closing line
        # is present. A fixed byte floor would measure padding, not content, and
        # no AI-disclosure boilerplate is added (nothing published is AI-generated).
        assert "travel video ireland" in metadata.description.lower()
        assert "Subscribe for more episodes!" in metadata.description
        assert "AI DISCLOSURE" not in metadata.description
        assert len(metadata.tags) >= 5  # Decent tag count

    def test_cold_start_quality_score(self, empty_manager):
        """Test quality score works without historical comparison"""
        metadata = empty_manager.generate(
            content_type=ContentType.VIDEO,
            topic="Quality Test Video",
            body="Test content for quality scoring",
            existing_tags=["test", "quality"],
        )

        # Should still calculate quality score
        assert metadata.quality_score is not None
        assert 0.0 <= metadata.quality_score <= 1.0

    def test_cold_start_keyword_extraction(self, empty_manager):
        """Test keyword extraction works without analytics"""
        metadata = empty_manager.generate(
            content_type=ContentType.VIDEO,
            topic="Python Programming Tutorial",
            body="Learn Python programming from scratch with this comprehensive tutorial",
            existing_tags=[],
        )

        # Should extract keywords from content
        assert len(metadata.tags) > 0
        # Should include relevant keywords
        tags_lower = [t.lower() for t in metadata.tags]
        assert (
            any("python" in t for t in tags_lower)
            or any("programming" in t for t in tags_lower)
            or any("tutorial" in t for t in tags_lower)
        )

    def test_optimizer_with_no_data(self, empty_manager):
        """Test SEO optimizer gracefully handles no historical data"""
        from seo_manager.analytics.seo_optimizer import SEOOptimizer

        optimizer = SEOOptimizer(empty_manager)

        # Should not crash when analyzing empty data
        patterns = optimizer.analyze_patterns()

        assert patterns is not None
        assert "tags" in patterns
        assert "title_lengths" in patterns

    def test_optimizer_fallback_to_defaults(self, empty_manager):
        """Test optimizer uses sensible defaults without data"""
        from seo_manager.analytics.seo_optimizer import SEOOptimizer

        optimizer = SEOOptimizer(empty_manager)

        # Get optimal title length without data
        optimal_min, optimal_max = optimizer.get_optimal_title_length()

        # Should return sensible defaults (YouTube best practices)
        assert optimal_min == 50
        assert optimal_max == 70

    def test_optimizer_top_tags_empty(self, empty_manager):
        """Test getting top tags when no data exists"""
        from seo_manager.analytics.seo_optimizer import SEOOptimizer

        optimizer = SEOOptimizer(empty_manager)

        top_tags = optimizer.get_top_performing_tags(10)

        # Should return empty list, not crash
        assert top_tags == []

    def test_first_video_creates_baseline(self, empty_manager):
        """Test first video creates baseline for future analysis"""
        # Generate and save first video
        metadata = empty_manager.generate(
            content_type=ContentType.VIDEO,
            topic="First Video",
            body="Content",
            existing_tags=["first"],
        )

        event_id = empty_manager.save(metadata)
        assert event_id is not None

        # Track performance (simulate)
        empty_manager.analytics.track_performance(
            content_id=metadata.content_id,
            views=100,
            impressions=1000,
            clicks=10,
            engagement_rate=0.05,
        )

        # Should now have 1 data point
        analytics = empty_manager.analytics.get_summary()
        assert analytics["content_metrics"]["total_content_generated"] >= 1

    def test_recommendations_without_data(self, empty_manager):
        """Test recommendations work without historical data"""
        from seo_manager.analytics.seo_optimizer import SEOOptimizer

        optimizer = SEOOptimizer(empty_manager)

        metadata = empty_manager.generate(
            content_type=ContentType.VIDEO, topic="Test", body="Test", existing_tags=[]
        )

        # Generate recommendations without patterns
        recommendations = optimizer.generate_recommendations(metadata.content_id)

        # Should provide generic best practices recommendations
        assert isinstance(recommendations, list)

    def test_predict_performance_no_baseline(self, empty_manager):
        """Test performance prediction without baseline data"""
        from seo_manager.analytics.seo_optimizer import SEOOptimizer

        optimizer = SEOOptimizer(empty_manager)

        metadata = empty_manager.generate(
            content_type=ContentType.VIDEO,
            topic="Test Video",
            body="Content",
            existing_tags=["test"],
        )

        # Should return neutral/conservative predictions
        predictions = optimizer.predict_performance(metadata)

        assert predictions["estimated_views"] >= 0
        assert predictions["estimated_ctr"] >= 0.0
        # No history: the numbers are placeholders and the state says so.
        assert predictions["evidence_state"] == "no_history"
        assert predictions["heuristic_score"] is None

    def test_cold_start_validation_works(self, empty_manager):
        """Test best practices validation works without analytics"""
        from seo_manager.validation.youtube_best_practices import (
            YouTubeBestPracticesValidator,
        )

        validator = YouTubeBestPracticesValidator()

        metadata = empty_manager.generate(
            content_type=ContentType.VIDEO,
            topic="Test Video Title",
            body="Test content",
            existing_tags=["test"],
        )

        # Validation should work independently of analytics
        validation = validator.generate_validation_report(metadata)

        assert validation is not None
        assert "score" in validation
        assert "issues" in validation

    def test_industry_standard_tags_fallback(self, empty_manager):
        """Test system uses industry standard tags when no data"""
        metadata = empty_manager.generate(
            content_type=ContentType.VIDEO,
            topic="Travel Video",
            body="Travel content about exploring new places",
            existing_tags=[],
        )

        # Should extract relevant keywords from content
        assert len(metadata.tags) >= 3
        # Tags should be relevant to content
        tags_text = " ".join(metadata.tags).lower()
        assert (
            "travel" in tags_text or "exploring" in tags_text or "places" in tags_text
        )

    def test_cold_start_message_to_user(self, empty_manager):
        """Test system informs user about cold start mode"""
        from seo_manager.analytics.seo_optimizer import SEOOptimizer

        optimizer = SEOOptimizer(empty_manager)
        patterns = optimizer.analyze_patterns()

        # Should indicate no data available
        # (Implementation will add messaging)
        assert patterns["tags"] == []
        assert patterns["title_lengths"] == []


class TestProgressiveImprovement:
    """Test that system improves as data accumulates"""

    @pytest.fixture
    def progressive_manager(self):
        """Manager that accumulates data"""
        temp_dir = tempfile.mkdtemp()
        manager = SEOManager(
            site_id="growing_channel",
            platform=YouTubeAdapter(),
            storage_path=Path(temp_dir),
        )
        yield manager
        shutil.rmtree(temp_dir)

    def test_improvement_with_one_video(self, progressive_manager):
        """Test optimization improves after first video"""
        from seo_manager.analytics.seo_optimizer import SEOOptimizer

        optimizer = SEOOptimizer(progressive_manager)

        # Before: No data
        patterns_before = optimizer.analyze_patterns()
        assert len(patterns_before["tags"]) == 0

        # Add one video with performance
        metadata = progressive_manager.generate(
            content_type=ContentType.VIDEO,
            topic="First Video",
            body="Content",
            existing_tags=["test", "first"],
        )
        progressive_manager.save(metadata)
        progressive_manager.analytics.track_performance(
            content_id=metadata.content_id, views=500, engagement_rate=0.1
        )

        # After: Has patterns
        optimizer_after = SEOOptimizer(progressive_manager)
        patterns_after = optimizer_after.analyze_patterns()

        # Should now have some patterns
        # (May still be limited with just 1 video)
        assert isinstance(patterns_after, dict)

    def test_improvement_with_five_videos(self, progressive_manager):
        """Test optimization quality improves with more videos"""
        from seo_manager.analytics.seo_optimizer import SEOOptimizer

        # Add 5 videos with varying performance
        for i in range(5):
            metadata = progressive_manager.generate(
                content_type=ContentType.VIDEO,
                topic=f"Video {i + 1}",
                body="Content",
                existing_tags=["travel", "scenic"],
            )
            progressive_manager.save(metadata)
            progressive_manager.analytics.track_performance(
                content_id=metadata.content_id,
                views=(i + 1) * 100,  # Varying performance
                engagement_rate=0.05 + (i * 0.02),
            )

        # Analyze with 5 videos
        optimizer = SEOOptimizer(progressive_manager)
        optimizer.analyze_patterns()

        # Should have meaningful patterns now
        top_tags = optimizer.get_top_performing_tags(5)
        assert len(top_tags) > 0  # Should have identified top tags

    def test_confidence_increases_with_data(self, progressive_manager):
        """Test the evidence behind a prediction grows with data"""
        from seo_manager.analytics.seo_optimizer import SEOOptimizer

        # Generate test metadata
        test_metadata = progressive_manager.generate(
            content_type=ContentType.VIDEO,
            topic="Test",
            body="Test",
            existing_tags=["test"],
        )

        # Prediction with no data
        optimizer_empty = SEOOptimizer(progressive_manager)
        pred_empty = optimizer_empty.predict_performance(test_metadata)
        assert pred_empty["evidence_state"] == "no_history"
        samples_before = pred_empty["evidence_sample_size"]

        # Add 5 videos with performance
        for i in range(5):
            metadata = progressive_manager.generate(
                content_type=ContentType.VIDEO,
                topic=f"Video {i}",
                body="Content",
                existing_tags=["test"],
            )
            progressive_manager.save(metadata)
            progressive_manager.analytics.track_performance(
                content_id=metadata.content_id, views=100, engagement_rate=0.05
            )

        # Prediction with data
        optimizer_with_data = SEOOptimizer(progressive_manager)
        pred_with_data = optimizer_with_data.predict_performance(test_metadata)
        samples_after = pred_with_data["evidence_sample_size"]

        # The evidence base grows with data (a state, not a probability)
        assert samples_after >= samples_before
