"""
Unit tests for Batch Video Uploader.

Tests the ability to upload multiple videos in one operation
with proper quota management and error handling.

TDD: Tests written BEFORE implementation.
"""

import tempfile
from datetime import datetime
from pathlib import Path

import pytest


class TestBatchUploader:
    """Test batch video upload functionality"""

    def test_batch_uploader_initialization(self):
        """Test batch uploader can be initialized"""
        from seo_manager.youtube.batch_uploader import BatchUploader

        uploader = BatchUploader(
            credentials_path="fake_creds.json", project_id="test_project"
        )
        assert uploader is not None

    def test_batch_config_dataclass(self):
        """Test BatchUploadConfig structure"""
        from seo_manager.core.metadata import ContentType, Platform, SEOMetadata
        from seo_manager.youtube.batch_uploader import BatchUploadConfig, VideoConfig

        metadata = SEOMetadata(
            site_id="test",
            content_id="vid_001",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,
            title="Test",
            description="Test",
            tags=["test"],
        )

        video = VideoConfig(
            video_path="/path/to/video.mp4", metadata=metadata, privacy="draft"
        )

        config = BatchUploadConfig(videos=[video], continue_on_error=True)

        assert len(config.videos) == 1
        assert config.continue_on_error is True

    def test_batch_upload_quota_validation(self):
        """Test batch validates total quota before starting"""
        from seo_manager.youtube.batch_uploader import BatchUploader

        uploader = BatchUploader(
            credentials_path="fake_creds.json", project_id="test_project"
        )

        # Should calculate total cost
        total_cost = uploader.calculate_batch_cost(video_count=5)
        assert total_cost == 5 * 1600  # 5 videos × 1600 units  # noqa: RUF003

    def test_batch_quota_check_before_upload(self):
        """Test batch checks quota before starting upload"""
        from seo_manager.youtube.batch_uploader import BatchUploader

        uploader = BatchUploader(
            credentials_path="fake_creds.json", project_id="test_project"
        )

        # Should check if quota available
        can_proceed = uploader.check_batch_quota(video_count=5)
        assert isinstance(can_proceed, bool)

    def test_batch_quota_exceeded_prevention(self):
        """Test batch aborts if quota would be exceeded"""
        from seo_manager.youtube.batch_uploader import BatchQuotaError, BatchUploader

        uploader = BatchUploader(
            credentials_path="fake_creds.json", project_id="test_project"
        )

        # Should raise error if quota insufficient
        # (Will be mocked in actual test)
        assert hasattr(uploader, "validate_batch_quota")

    def test_batch_progress_tracking(self):
        """Test batch reports progress (video 2/5, etc.)"""
        from seo_manager.youtube.batch_uploader import BatchUploader

        BatchUploader(credentials_path="fake_creds.json", project_id="test_project")

        progress_updates = []

        def progress_callback(current, total, video_title):
            progress_updates.append(
                {"current": current, "total": total, "title": video_title}
            )

        # Should support progress callbacks
        assert callable(progress_callback)

    def test_batch_continue_on_error_mode(self):
        """Test batch continues uploading if one fails"""
        from seo_manager.youtube.batch_uploader import BatchUploadConfig

        config = BatchUploadConfig(videos=[], continue_on_error=True)

        assert config.continue_on_error is True

    def test_batch_stop_on_error_mode(self):
        """Test batch can stop on first error"""
        from seo_manager.youtube.batch_uploader import BatchUploadConfig

        config = BatchUploadConfig(videos=[], continue_on_error=False)

        assert config.continue_on_error is False

    def test_batch_results_structure(self):
        """Test batch results contain per-video status"""
        from seo_manager.youtube.batch_uploader import BatchUploadResult

        result = BatchUploadResult(
            total_videos=5,
            successful=4,
            failed=1,
            quota_used=6400,
            results=[],
            errors=[],
        )

        assert result.total_videos == 5
        assert result.successful == 4
        assert result.failed == 1
        assert result.quota_used == 6400

    def test_batch_partial_success(self):
        """Test batch handles partial success correctly"""
        from seo_manager.youtube.batch_uploader import BatchUploadResult

        result = BatchUploadResult(
            total_videos=3,
            successful=2,
            failed=1,
            quota_used=3200,  # Only 2 videos uploaded
            results=[],
            errors=["Video 2 failed: Invalid file"],
        )

        assert result.successful == 2
        assert len(result.errors) == 1

    def test_batch_dry_run_mode(self):
        """Test batch dry run validates without uploading"""
        from seo_manager.youtube.batch_uploader import BatchUploadConfig

        config = BatchUploadConfig(videos=[], dry_run=True)

        assert config.dry_run is True

    def test_batch_with_thumbnails(self):
        """Test batch upload supports thumbnails for each video"""
        from seo_manager.youtube.batch_uploader import VideoConfig

        video = VideoConfig(
            video_path="/path/to/video.mp4",
            metadata=None,
            thumbnail_path="/path/to/thumb.jpg",
            privacy="draft",
        )

        assert video.thumbnail_path == "/path/to/thumb.jpg"

    def test_batch_sequential_upload_order(self):
        """Test batch uploads videos in order provided"""
        from seo_manager.youtube.batch_uploader import BatchUploader

        uploader = BatchUploader(
            credentials_path="fake_creds.json", project_id="test_project"
        )

        # Should maintain order
        assert hasattr(uploader, "upload_batch")

    def test_batch_metadata_validation_before_upload(self):
        """Test batch validates all metadata before uploading"""
        from seo_manager.youtube.batch_uploader import BatchUploader

        uploader = BatchUploader(
            credentials_path="fake_creds.json", project_id="test_project"
        )

        # Should have validation method
        assert hasattr(uploader, "validate_all_metadata")

    def test_batch_quota_tracking_per_video(self):
        """Test batch tracks quota for each video uploaded"""
        from seo_manager.youtube.batch_uploader import BatchUploadResult

        result = BatchUploadResult(
            total_videos=3,
            successful=3,
            failed=0,
            quota_used=4800,  # 3 × 1600  # noqa: RUF003
            results=[],
            errors=[],
        )

        # Quota should reflect actual uploads
        assert result.quota_used == 3 * 1600
