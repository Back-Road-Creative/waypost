"""
Simplified tests for YouTube Analytics sync (without Google API mocks).

Tests core logic without requiring Google API libraries.
"""

import builtins
import importlib
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from seo_manager import SEOManager
from seo_manager.platforms import YouTubeAdapter


class TestYouTubeAnalyticsSyncLogic:
    """Test YouTube sync logic without API dependencies"""

    @pytest.fixture
    def temp_storage(self):
        """Create temporary storage"""
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)

    @pytest.fixture
    def manager(self, temp_storage):
        """Create SEO manager"""
        return SEOManager(
            site_id="test_youtube", platform=YouTubeAdapter(), storage_path=temp_storage
        )

    def test_youtube_analytics_module_exists(self):
        """Test that YouTube analytics module can be imported"""
        from seo_manager.analytics import youtube_sync

        assert youtube_sync is not None
        assert hasattr(youtube_sync, "YouTubeAnalyticsSync")

    def test_manual_performance_tracking(self, manager):
        """Test manually tracking YouTube performance (API-free)"""
        # This is what the API sync would do internally
        event_id = manager.analytics.track_performance(
            content_id="episode_042",
            views=1250,
            clicks=87,
            impressions=5000,
            engagement_rate=0.15,
            likes=100,
            comments=50,
        )

        assert event_id is not None

        # Verify tracking
        performance = manager.analytics.get_content_performance("episode_042")
        assert performance["total_views"] == 1250

    def test_batch_manual_sync(self, manager):
        """Test batch sync pattern (manual simulation)"""
        # Simulate what API sync would do
        video_data = [
            ("episode_001", 1000, 50, 2500),
            ("episode_002", 1500, 75, 3000),
            ("episode_003", 2000, 100, 4000),
        ]

        for content_id, views, likes, comments in video_data:
            engagement_rate = (likes + comments) / views if views else 0

            manager.analytics.track_performance(
                content_id=content_id,
                views=views,
                engagement_rate=engagement_rate,
                likes=likes,
                comments=comments,
            )

        # Verify all tracked
        summary = manager.analytics.get_summary()
        assert summary["performance_metrics"]["total_views"] == 4500  # 1000+1500+2000

    def test_engagement_rate_calculation(self):
        """Test engagement rate calculation logic"""

        # Logic that YouTube sync would use
        def calculate_engagement(stats):
            views = int(stats.get("viewCount", 0))
            if views == 0:
                return 0.0
            likes = int(stats.get("likeCount", 0))
            comments = int(stats.get("commentCount", 0))
            return round((likes + comments) / views, 4)

        # Test calculation
        stats = {"viewCount": "1000", "likeCount": "100", "commentCount": "50"}

        engagement = calculate_engagement(stats)
        assert engagement == 0.15  # (100 + 50) / 1000

    def test_youtube_sync_dependencies_are_installed(self):
        """The Google libraries must be importable, not optional.

        This replaced a test that passed either way: it asserted an ImportError
        when the libraries were missing and did nothing when they were present,
        so a service with YouTube analytics entirely unavailable looked exactly
        like a healthy one. Reaching the credentials check proves the imports
        resolved for real.
        """
        from seo_manager.analytics.youtube_sync import YouTubeAnalyticsSync

        with pytest.raises(FileNotFoundError):
            YouTubeAnalyticsSync(credentials_path="does-not-exist.json")

    def test_missing_google_oauth_library_fails_loudly(self, monkeypatch):
        """A missing Google dependency must raise, not degrade quietly.

        Regression guard for the silent degradation this replaced: the module
        wrapped its Google imports in `try/except ImportError`, set
        GOOGLE_APIS_AVAILABLE = False and print()ed a warning nothing reads, so
        an absent dependency was indistinguishable from a healthy import. CI
        installs from pyproject.toml, which did not declare
        google-auth-oauthlib, so analytics sync was unavailable in every single
        run and the suite stayed green.

        Blocking the import here rather than relying on the environment means
        this cannot pass by accident on a machine that has the package.
        """
        real_import = builtins.__import__

        def no_oauthlib(name, *args, **kwargs):
            if name == "google_auth_oauthlib" or name.startswith(
                "google_auth_oauthlib."
            ):
                raise ImportError(f"blocked import of {name}")
            return real_import(name, *args, **kwargs)

        for module in (
            "seo_manager.analytics.youtube_sync",
            "google_auth_oauthlib",
            "google_auth_oauthlib.flow",
        ):
            monkeypatch.delitem(sys.modules, module, raising=False)
        monkeypatch.setattr(builtins, "__import__", no_oauthlib)

        with pytest.raises(ImportError, match="google_auth_oauthlib"):
            importlib.import_module("seo_manager.analytics.youtube_sync")
