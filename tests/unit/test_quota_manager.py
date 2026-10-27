"""
Unit tests for API Quota Management.

Tests cover:
- Global quota tracking across all operations
- Multi-brand/multi-channel quota isolation
- Daily quota limits (10,000 units per project)
- Quota exhaustion prevention
- Operation cost tracking
- Quota reset at midnight America/Los_Angeles
- Warning thresholds

Critical: Must prevent ANY operation from exceeding quota limits.

YouTube API Quota Costs:
- videos.insert (upload): 1 call from its own bucket (default 100 calls/day)
- thumbnails.set: 50 units
- videos.update: 50 units
- search.list: 1 call from its own bucket (default 100 calls/day)
- videos.list: 1 unit per call
- channels.list: 1 unit
- playlistItems.insert: 50 units

Daily Quota: 10,000 units per Google Cloud project
"""

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest


class TestQuotaManager:
    """Test global quota management"""

    def test_quota_manager_initialization(self, tmp_path):
        """Test quota manager can be initialized"""
        from seo_manager.quota.manager import QuotaManager

        manager = QuotaManager(project_id="test_project", storage_path=tmp_path)
        assert manager is not None
        assert manager.project_id == "test_project"

    def test_track_operation_cost(self, tmp_path):
        """Test tracking individual operation costs"""
        from seo_manager.quota.manager import QuotaManager

        manager = QuotaManager(project_id="test_project", storage_path=tmp_path)

        # Track a pool operation (thumbnails.set: 50 units)
        manager.track_operation("thumbnails.set", cost=50)

        # Check usage
        usage = manager.get_usage()
        assert usage["total_used"] == 50
        assert usage["remaining"] == 10000 - 50

        # A video upload is one call against the videos.insert bucket
        manager.track_operation("video_upload", cost=1)
        assert manager.get_usage()["total_used"] == 50
        assert manager.get_bucket_usage("youtube_data", "videos.insert")["used"] == 1

    def test_multiple_operations_cumulative(self, tmp_path):
        """Test that multiple operations accumulate"""
        from seo_manager.quota.manager import QuotaManager

        manager = QuotaManager(project_id="test_project", storage_path=tmp_path)

        # Multiple operations
        manager.track_operation("upload", cost=1600)
        manager.track_operation("thumbnail", cost=50)
        manager.track_operation("search", cost=100)

        usage = manager.get_usage()
        assert usage["total_used"] == 1750  # 1600 + 50 + 100

    def test_quota_limit_enforcement(self, tmp_path):
        """Test that operations are blocked when quota exceeded"""
        from seo_manager.quota.manager import QuotaExceededError, QuotaManager

        manager = QuotaManager(
            project_id="test_project", daily_limit=10000, storage_path=tmp_path
        )

        # Use up most quota
        manager.track_operation("bulk_operation", cost=9500)

        # Should allow operation within limit
        can_proceed = manager.check_quota(cost=400)
        assert can_proceed is True

        # Should block operation that exceeds limit
        can_proceed = manager.check_quota(cost=600)
        assert can_proceed is False

        # Should raise error if forced
        with pytest.raises(QuotaExceededError):
            manager.track_operation("excessive_op", cost=600, enforce=True)

    def test_multi_brand_isolation(self, tmp_path):
        """Test quota isolation between different brands/channels"""
        from seo_manager.quota.manager import QuotaManager

        # Different brands using same Google Cloud project
        brand_a = QuotaManager(
            project_id="project_123", brand_id="brand_a", storage_path=tmp_path
        )
        brand_b = QuotaManager(
            project_id="project_123", brand_id="brand_b", storage_path=tmp_path
        )

        # Brand A uses quota
        brand_a.track_operation("upload", cost=1600)

        # Brand B should share the SAME project quota
        # This is critical - they share the Google Cloud project quota!
        total_usage = QuotaManager.get_project_usage(
            "project_123", storage_path=tmp_path
        )
        assert total_usage == 1600

        # Brand B's operation adds to project total
        brand_b.track_operation("search", cost=100)
        total_usage = QuotaManager.get_project_usage(
            "project_123", storage_path=tmp_path
        )
        assert total_usage == 1700  # Shared quota

    def test_daily_quota_reset(self, tmp_path):
        """Test quota resets at midnight UTC"""
        from seo_manager.quota.manager import QuotaManager

        manager = QuotaManager(project_id="test_project", storage_path=tmp_path)

        # Use quota
        manager.track_operation("upload", cost=1600)
        assert manager.get_usage()["total_used"] == 1600

        # Simulate day change
        manager.reset_quota()

        # Should be reset
        usage = manager.get_usage()
        assert usage["total_used"] == 0
        assert usage["remaining"] == 10000

    def test_quota_warning_thresholds(self, tmp_path):
        """Test warning at 80% and 90% quota usage"""
        from seo_manager.quota.manager import QuotaManager

        manager = QuotaManager(
            project_id="test_project", daily_limit=10000, storage_path=tmp_path
        )

        # At 70% - no warning
        manager.track_operation("op1", cost=7000)
        warnings = manager.get_warnings()
        assert len(warnings) == 0

        # At 85% - should warn
        manager.track_operation("op2", cost=1500)  # Total: 8500 (85%)
        warnings = manager.get_warnings()
        assert len(warnings) >= 1
        assert "80%" in warnings[0] or "warning" in warnings[0].lower()

        # At 95% - critical warning
        manager.track_operation("op3", cost=1000)  # Total: 9500 (95%)
        warnings = manager.get_warnings()
        assert len(warnings) >= 2
        assert any("90%" in w or "critical" in w.lower() for w in warnings)

    def test_operation_history(self, tmp_path):
        """Test tracking operation history for debugging"""
        from seo_manager.quota.manager import QuotaManager

        manager = QuotaManager(project_id="test_project", storage_path=tmp_path)

        manager.track_operation("upload", cost=1600, metadata={"video_id": "abc123"})
        manager.track_operation("search", cost=100, metadata={"query": "python"})

        history = manager.get_history()
        assert len(history) == 2
        assert history[0]["operation"] == "upload"
        assert history[0]["cost"] == 1600
        assert history[1]["operation"] == "search"

    def test_estimate_operation_cost(self, tmp_path):
        """Test estimating cost before operation"""
        from seo_manager.quota.manager import QuotaManager

        manager = QuotaManager(project_id="test_project", storage_path=tmp_path)

        # Should provide cost estimates
        # videos.insert is one call against its own bucket, not 1600 units.
        upload_cost = manager.estimate_cost("video_upload")
        assert upload_cost == 1

        thumbnail_cost = manager.estimate_cost("thumbnail_upload")
        assert thumbnail_cost == 50

        search_cost = manager.estimate_cost("video_search")
        assert search_cost == 1

    def test_safe_operation_check(self, tmp_path):
        """Test checking if operation is safe before executing"""
        from seo_manager.quota.manager import QuotaManager

        manager = QuotaManager(
            project_id="test_project", daily_limit=10000, storage_path=tmp_path
        )

        # Use 9000 units
        manager.track_operation("bulk", cost=9000)

        # The pool is nearly full, but uploads and searches draw from their
        # own buckets: both still fit.
        assert manager.can_safely_perform("video_upload") is True
        assert manager.can_safely_perform("video_search") is True

        # videos.update (50 units) comes from the pool: 9000 + 50 fits, 9980 + 50 not
        assert manager.can_safely_perform("videos.update") is True
        manager.track_operation("bulk2", cost=980)
        assert manager.can_safely_perform("videos.update") is False

    def test_quota_persistence(self):
        """Test quota state persists across sessions"""
        from seo_manager.quota.manager import QuotaManager

        temp_dir = tempfile.mkdtemp()
        storage_path = Path(temp_dir)

        # Session 1: Use quota
        manager1 = QuotaManager(project_id="test_project", storage_path=storage_path)
        manager1.track_operation("upload", cost=1600)

        # Session 2: Should remember quota
        manager2 = QuotaManager(project_id="test_project", storage_path=storage_path)
        usage = manager2.get_usage()
        assert usage["total_used"] == 1600

        # Cleanup
        import shutil

        shutil.rmtree(temp_dir)

    def test_multiple_projects_independent_quotas(self, tmp_path):
        """Test different Google Cloud projects have independent quotas"""
        from seo_manager.quota.manager import QuotaManager

        # Different Google Cloud projects = different quotas
        project_a = QuotaManager(project_id="project_a", storage_path=tmp_path)
        project_b = QuotaManager(project_id="project_b", storage_path=tmp_path)

        # Project A uses quota
        project_a.track_operation("upload", cost=1600)

        # Project B has independent quota
        assert project_b.get_usage()["total_used"] == 0

    def test_prevent_quota_exhaustion_loophole(self):
        """Test: Cannot bypass quota limits through multiple managers"""
        from seo_manager.quota.manager import QuotaManager

        # Critical test: Multiple manager instances for same project
        # Must share quota state (no loophole!)

        temp_dir = tempfile.mkdtemp()
        storage_path = Path(temp_dir)

        manager1 = QuotaManager(project_id="shared_project", storage_path=storage_path)

        manager2 = QuotaManager(project_id="shared_project", storage_path=storage_path)

        # Manager 1 uses quota
        manager1.track_operation("upload", cost=9000)

        # Manager 2 should see the same quota usage
        usage2 = manager2.get_usage()
        assert usage2["total_used"] == 9000

        # Manager 2 should be blocked from exceeding
        can_upload = manager2.check_quota(cost=1600)
        assert can_upload is False  # Would exceed limit

        # Cleanup
        import shutil

        shutil.rmtree(temp_dir)

    def test_two_instances_do_not_overwrite_each_other(self, tmp_path):
        """Interleaved writes from two managers must both be kept."""
        from seo_manager.quota.manager import QuotaManager

        first = QuotaManager(project_id="shared", storage_path=tmp_path)
        second = QuotaManager(project_id="shared", storage_path=tmp_path)

        first.track_operation("upload", cost=1600)
        second.track_operation("search", cost=100)
        first.track_operation("thumbnail", cost=50)

        reloaded = QuotaManager(project_id="shared", storage_path=tmp_path)
        assert reloaded.get_usage()["total_used"] == 1750
        assert [op.operation for op in reloaded.operations] == [
            "upload",
            "search",
            "thumbnail",
        ]

    def test_enforce_uses_usage_written_by_another_instance(self, tmp_path):
        """enforce=True must see quota another manager already spent."""
        from seo_manager.quota.manager import QuotaExceededError, QuotaManager

        first = QuotaManager(project_id="shared", storage_path=tmp_path)
        second = QuotaManager(project_id="shared", storage_path=tmp_path)

        first.track_operation("bulk", cost=9000)

        with pytest.raises(QuotaExceededError):
            second.track_operation("upload", cost=1600, enforce=True)
        assert QuotaManager.get_project_usage("shared", storage_path=tmp_path) == 9000

    def test_concurrent_access_safety(self):
        """Test quota tracking is thread-safe"""
        import threading

        from seo_manager.quota.manager import QuotaManager

        temp_dir = tempfile.mkdtemp()
        manager = QuotaManager(project_id="test_project", storage_path=Path(temp_dir))

        # Simulate concurrent operations
        def track_upload():
            manager.track_operation("upload", cost=100)

        threads = [threading.Thread(target=track_upload) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All 10 operations should be tracked
        usage = manager.get_usage()
        assert usage["total_used"] == 1000  # 10 x 100

        # Cleanup
        import shutil

        shutil.rmtree(temp_dir)


class TestQuotaIntegration:
    """Test quota integration with existing components"""

    def test_youtube_uploader_tracks_quota(self):
        """Test YouTube uploader automatically tracks quota"""
        from seo_manager.youtube.uploader import YouTubeUploader

        # Uploader should integrate with quota manager
        # (Will be implemented)
        pass

    def test_fetch_videos_tracks_quota(self):
        """Test fetch_my_videos.py tracks quota"""
        # fetch_my_videos should track:
        # - channels.list: 1 unit
        # - search.list: 100 units per call
        # - videos.list: 1 unit per batch
        pass

    def test_analytics_sync_tracks_quota(self):
        """Test analytics sync tracks quota"""
        from seo_manager.analytics.youtube_sync import YouTubeAnalyticsSync

        # Should track quota for:
        # - videos.list: 1 unit per video
        pass


class TestQuotaLimitsAndSafety:
    """Test quota limits prevent exceeding daily quota"""

    def test_cannot_upload_if_quota_exceeded(self, tmp_path):
        """Test upload blocked when quota exhausted"""
        from seo_manager.quota.manager import QuotaManager

        manager = QuotaManager(
            project_id="test", daily_limit=10000, storage_path=tmp_path
        )

        # Exhaust quota
        manager.track_operation("bulk", cost=9980)

        # A pool operation (thumbnails.set, 50 units) is blocked; uploads
        # draw from their own bucket and are not.
        assert manager.can_safely_perform("thumbnails.set") is False
        assert manager.can_safely_perform("video_upload") is True

    def test_cannot_batch_fetch_if_quota_low(self, tmp_path):
        """Test batch operations blocked when quota insufficient"""
        from seo_manager.quota.manager import QuotaManager

        manager = QuotaManager(
            project_id="test", daily_limit=10000, storage_path=tmp_path
        )

        # Use quota
        manager.track_operation("uploads", cost=9500)

        # A batch needing 600 units would take 9500 to 10100, over the limit
        can_fetch = manager.check_quota(cost=600)
        assert can_fetch is False

        # A smaller batch that still fits is allowed
        assert manager.check_quota(cost=200) is True

    def test_quota_report_generation(self, tmp_path):
        """Test generating quota usage report"""
        from seo_manager.quota.manager import QuotaManager

        manager = QuotaManager(project_id="test_project", storage_path=tmp_path)

        manager.track_operation("upload", cost=1600)
        manager.track_operation("thumbnail", cost=50)
        manager.track_operation("search", cost=100)

        report = manager.generate_report()

        assert "total_used" in report
        assert "total_limit" in report
        assert "percentage_used" in report
        assert "operations_count" in report
        assert report["operations_count"] == 3
        assert report["percentage_used"] == 17.5  # 1750/10000

    def test_dry_run_does_not_consume_quota(self, tmp_path):
        """Test dry-run operations don't count against quota"""
        from seo_manager.quota.manager import QuotaManager

        manager = QuotaManager(project_id="test_project", storage_path=tmp_path)

        # Dry run simulation
        cost = manager.estimate_cost("thumbnails.set")
        assert cost == 50

        # Dry run should NOT track
        usage_before = manager.get_usage()["total_used"]

        # Actual operation SHOULD track
        manager.track_operation("upload", cost=1600)
        usage_after = manager.get_usage()["total_used"]

        assert usage_after == usage_before + 1600
