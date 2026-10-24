"""
Unit tests for AnalyticsTracker.

Tests cover:
- Performance metric tracking
- Event aggregation
- Summary generation
- Top performer analysis
- CTR calculation
- Date range filtering
"""

import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from seo_manager.analytics.tracker import AnalyticsTracker
from seo_manager.core.events import EventType, SEOEvent
from seo_manager.storage.event_store import SEOEventStore


class TestAnalyticsTracker:
    """Test AnalyticsTracker functionality"""

    @pytest.fixture
    def temp_storage(self):
        """Create temporary storage"""
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)

    @pytest.fixture
    def store(self, temp_storage):
        """Create event store"""
        return SEOEventStore(storage_path=temp_storage)

    @pytest.fixture
    def tracker(self, store):
        """Create analytics tracker"""
        return AnalyticsTracker(store=store, site_id="test_site")

    def test_tracker_initialization(self, tracker):
        """Test tracker initializes correctly"""
        assert tracker.site_id == "test_site"
        assert tracker.store is not None

    def test_track_generation(self, tracker):
        """Test tracking metadata generation event"""
        from seo_manager.core.metadata import ContentType, Platform, SEOMetadata

        metadata = SEOMetadata(
            site_id="test_site",
            content_id="content_001",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,
            title="Test Title",
            description="Test description",
            tags=["test", "video"],
            quality_score=0.85,
            keyword_density=0.05,
            readability_score=72.5,
        )

        event_id = tracker.track_generation(metadata)

        assert event_id is not None
        assert isinstance(event_id, str)

    def test_track_performance(self, tracker):
        """Test tracking performance metrics"""
        event_id = tracker.track_performance(
            content_id="video_001",
            views=1250,
            clicks=87,
            impressions=5000,
            engagement_rate=0.15,
        )

        assert event_id is not None

    def test_get_summary_empty(self, tracker):
        """Test getting summary with no data"""
        summary = tracker.get_summary()

        assert summary is not None
        assert summary["content_metrics"]["total_content_generated"] == 0
        assert summary["performance_metrics"]["total_views"] == 0

    def test_get_summary_with_data(self, tracker):
        """Test summary aggregation with data"""
        from seo_manager.core.metadata import ContentType, Platform, SEOMetadata

        # Add generation events
        for i in range(3):
            metadata = SEOMetadata(
                site_id="test_site",
                content_id=f"content_{i}",
                platform=Platform.YOUTUBE,
                content_type=ContentType.VIDEO,
                title=f"Title {i}",
                description="Description",
                tags=["test"],
                quality_score=0.8 + (i * 0.05),
            )
            tracker.track_generation(metadata)

        # Add performance events
        tracker.track_performance("content_0", views=100, clicks=10, impressions=500)
        tracker.track_performance("content_1", views=200, clicks=20, impressions=600)

        # Get summary
        summary = tracker.get_summary()

        assert summary["content_metrics"]["total_content_generated"] == 3
        assert summary["content_metrics"]["avg_quality_score"] > 0
        assert summary["performance_metrics"]["total_views"] == 300
        assert summary["performance_metrics"]["total_clicks"] == 30

    def test_ctr_calculation(self, tracker):
        """Test CTR (click-through rate) calculation"""
        tracker.track_performance(
            content_id="video_001", views=1000, clicks=50, impressions=2500
        )

        summary = tracker.get_summary()

        # CTR = clicks / impressions = 50 / 2500 = 0.02
        assert summary["performance_metrics"]["avg_ctr"] == pytest.approx(
            0.02, rel=0.01
        )

    def test_get_content_performance(self, tracker):
        """Test getting performance for specific content"""
        # Track performance for same content multiple times
        tracker.track_performance("video_123", views=100, clicks=5, impressions=500)
        tracker.track_performance("video_123", views=150, clicks=8, impressions=600)

        performance = tracker.get_content_performance("video_123")

        assert performance["content_id"] == "video_123"
        assert performance["total_views"] == 250  # 100 + 150
        assert performance["total_clicks"] == 13  # 5 + 8
        assert performance["total_impressions"] == 1100  # 500 + 600
        assert "ctr" in performance

    def test_top_performers_carry_the_click_rate_provenance(self, tracker):
        """Provenance travels with the value; an absent source reads unknown."""
        from seo_manager.analytics import metric_source

        card = metric_source.YOUTUBE_CARD_CLICK_RATE
        tracker.track_performance(
            "card_video",
            views=1000,
            clicks=40,
            impressions=800,
            ctr=0.05,
            ctr_source=card,
        )
        tracker.track_performance("legacy_video", views=500)
        by_id = {p["content_id"]: p for p in tracker.get_summary()["top_performers"]}

        assert by_id["card_video"]["ctr_source"] == card
        assert by_id["legacy_video"]["ctr_source"] == metric_source.UNKNOWN
        # Unmeasured stays unmeasured — never a fabricated 0.
        assert by_id["legacy_video"]["ctr"] is None

    def test_get_top_performers(self, tracker):
        """Test getting top performing content"""
        from seo_manager.core.metadata import ContentType, Platform, SEOMetadata

        # Add content with varying performance
        contents = [
            ("content_001", 1000, 50, 2000),
            ("content_002", 500, 25, 1000),
            ("content_003", 2000, 100, 4000),
        ]

        for content_id, views, clicks, impressions in contents:
            # Generate metadata
            metadata = SEOMetadata(
                site_id="test_site",
                content_id=content_id,
                platform=Platform.YOUTUBE,
                content_type=ContentType.VIDEO,
                title=f"Title {content_id}",
                description="Desc",
                tags=["test"],
            )
            tracker.track_generation(metadata)

            # Track performance
            tracker.track_performance(
                content_id, views=views, clicks=clicks, impressions=impressions
            )

        # Get summary with top performers
        summary = tracker.get_summary()
        top = summary.get("top_performers", [])

        assert len(top) > 0
        # Top performer should be content_003 (2000 views)
        assert top[0]["content_id"] == "content_003"
        assert top[0]["views"] == 2000

    def test_date_range_filtering(self, tracker):
        """Test filtering analytics by date range"""
        from seo_manager.core.metadata import ContentType, Platform, SEOMetadata

        # Add events with different dates
        metadata1 = SEOMetadata(
            site_id="test_site",
            content_id="old_content",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,
            title="Old",
            description="Desc",
            tags=["test"],
            generated_at="2026-01-01T10:00:00+00:00",
        )

        metadata2 = SEOMetadata(
            site_id="test_site",
            content_id="new_content",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,
            title="New",
            description="Desc",
            tags=["test"],
            generated_at="2026-01-16T10:00:00+00:00",
        )

        # Manually create events with specific timestamps
        from uuid import uuid4

        event1 = SEOEvent(
            event_id=str(uuid4()),
            event_type=EventType.METADATA_GENERATED,
            site_id="test_site",
            content_id="old_content",
            timestamp="2026-01-01T10:00:00+00:00",
            data=metadata1.to_dict(),
        )

        event2 = SEOEvent(
            event_id=str(uuid4()),
            event_type=EventType.METADATA_GENERATED,
            site_id="test_site",
            content_id="new_content",
            timestamp="2026-01-16T10:00:00+00:00",
            data=metadata2.to_dict(),
        )

        tracker.store.append_event(event1)
        tracker.store.append_event(event2)

        # Get summary for January 2026 only
        summary = tracker.get_summary(
            start_date="2026-01-15T00:00:00", end_date="2026-01-17T23:59:59"
        )

        # Should only include new_content
        assert summary["content_metrics"]["total_content_generated"] == 1

    def test_content_performance_no_data(self, tracker):
        """Test getting performance for content with no data"""
        performance = tracker.get_content_performance("nonexistent")

        assert "error" in performance
        assert performance["content_id"] == "nonexistent"

    def test_average_quality_score(self, tracker):
        """Test average quality score calculation"""
        from seo_manager.core.metadata import ContentType, Platform, SEOMetadata

        scores = [0.70, 0.85, 0.92]

        for i, score in enumerate(scores):
            metadata = SEOMetadata(
                site_id="test_site",
                content_id=f"content_{i}",
                platform=Platform.LINKEDIN,
                content_type=ContentType.BLOG_POST,
                title="Title",
                description="Desc",
                tags=["test"],
                quality_score=score,
            )
            tracker.track_generation(metadata)

        summary = tracker.get_summary()

        # Average should be (0.70 + 0.85 + 0.92) / 3 = 0.823...
        assert summary["content_metrics"]["avg_quality_score"] == pytest.approx(
            0.82, abs=0.01
        )
