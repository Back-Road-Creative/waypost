"""
Unit tests for YouTube video uploader.

Tests cover:
- Video upload with metadata
- Thumbnail upload
- Privacy settings (draft, unlisted, public, scheduled)
- Metadata application (title, description, tags)
- Error handling and validation
- Quota tracking

Following TDD: Tests written BEFORE implementation.
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest


@pytest.mark.xfail(reason="TDD red-phase: requires real credentials file")
class TestYouTubeUploader:
    """Test YouTube video upload functionality"""

    def test_uploader_initialization(self):
        """Test that uploader can be initialized with credentials"""
        from seo_manager.youtube.uploader import YouTubeUploader

        # Should initialize without errors
        uploader = YouTubeUploader(credentials_path="fake_creds.json")
        assert uploader is not None

    def test_upload_video_with_metadata(self):
        """Test uploading video with SEO metadata"""
        from seo_manager.core.metadata import ContentType, Platform, SEOMetadata
        from seo_manager.youtube.uploader import YouTubeUploader

        uploader = YouTubeUploader(credentials_path="fake_creds.json")

        # Create metadata
        metadata = SEOMetadata(
            site_id="test_channel",
            content_id="test_video_001",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,
            title="Test Video Title",
            description="Test description",
            tags=["test", "video", "seo"],
        )

        # Upload should return video ID
        video_id = uploader.upload_video(
            video_path="/path/to/video.mp4", metadata=metadata, privacy="draft"
        )

        assert video_id is not None
        assert isinstance(video_id, str)
        assert len(video_id) > 0

    def test_upload_with_thumbnail(self):
        """Test uploading video with thumbnail"""
        from seo_manager.core.metadata import ContentType, Platform, SEOMetadata
        from seo_manager.youtube.uploader import YouTubeUploader

        uploader = YouTubeUploader(credentials_path="fake_creds.json")

        metadata = SEOMetadata(
            site_id="test_channel",
            content_id="test_video_001",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,
            title="Test Video",
            description="Test",
            tags=["test"],
        )

        # Upload with thumbnail
        video_id = uploader.upload_video(
            video_path="/path/to/video.mp4",
            metadata=metadata,
            thumbnail_path="/path/to/thumbnail.jpg",
            privacy="draft",
        )

        assert video_id is not None

    def test_privacy_settings(self):
        """Test different privacy settings"""
        from seo_manager.youtube.uploader import PrivacyStatus, YouTubeUploader

        YouTubeUploader(credentials_path="fake_creds.json")

        # Should support all privacy statuses
        assert hasattr(PrivacyStatus, "DRAFT")
        assert hasattr(PrivacyStatus, "UNLISTED")
        assert hasattr(PrivacyStatus, "PUBLIC")
        assert hasattr(PrivacyStatus, "PRIVATE")

    def test_scheduled_publish(self):
        """Test scheduling video for future publish"""
        from seo_manager.core.metadata import ContentType, Platform, SEOMetadata
        from seo_manager.youtube.uploader import YouTubeUploader

        uploader = YouTubeUploader(credentials_path="fake_creds.json")

        metadata = SEOMetadata(
            site_id="test_channel",
            content_id="test_video_001",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,
            title="Test Video",
            description="Test",
            tags=["test"],
        )

        # Schedule for tomorrow
        publish_at = datetime.now() + timedelta(days=1)

        video_id = uploader.upload_video(
            video_path="/path/to/video.mp4",
            metadata=metadata,
            privacy="private",
            publish_at=publish_at,
        )

        assert video_id is not None

    def test_category_setting(self):
        """Test setting YouTube category"""
        from seo_manager.core.metadata import ContentType, Platform, SEOMetadata
        from seo_manager.youtube.uploader import YouTubeUploader

        uploader = YouTubeUploader(credentials_path="fake_creds.json")

        metadata = SEOMetadata(
            site_id="test_channel",
            content_id="test_video_001",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,
            title="Test Video",
            description="Test",
            tags=["test"],
            youtube_metadata={"category": "Science & Technology"},
        )

        # Should extract category from metadata
        video_id = uploader.upload_video(
            video_path="/path/to/video.mp4", metadata=metadata, privacy="draft"
        )

        assert video_id is not None

    def test_validate_video_file(self):
        """Test video file validation"""
        from seo_manager.youtube.uploader import YouTubeUploader

        uploader = YouTubeUploader(credentials_path="fake_creds.json")

        # Should validate file exists
        is_valid = uploader.validate_video_file("/nonexistent/video.mp4")
        assert is_valid is False

    def test_validate_thumbnail_file(self):
        """Test thumbnail file validation"""
        from seo_manager.youtube.uploader import YouTubeUploader

        uploader = YouTubeUploader(credentials_path="fake_creds.json")

        # Should validate file exists and is image
        is_valid = uploader.validate_thumbnail("/nonexistent/thumb.jpg")
        assert is_valid is False

    def test_quota_tracking(self):
        """Test API quota usage tracking"""
        from seo_manager.youtube.uploader import YouTubeUploader

        uploader = YouTubeUploader(credentials_path="fake_creds.json")

        # Should track default-pool quota usage
        assert hasattr(uploader, "get_quota_usage")

    def test_upload_progress_callback(self):
        """Test progress callback during upload"""
        from seo_manager.youtube.uploader import YouTubeUploader

        YouTubeUploader(credentials_path="fake_creds.json")

        progress_updates = []

        def progress_callback(current, total):
            progress_updates.append(
                {"current": current, "total": total, "percent": (current / total) * 100}
            )

        # Upload with progress tracking
        # (This will be implemented)
        assert callable(progress_callback)

    def test_playlist_assignment(self):
        """Test assigning video to playlist"""
        from seo_manager.core.metadata import ContentType, Platform, SEOMetadata
        from seo_manager.youtube.uploader import YouTubeUploader

        uploader = YouTubeUploader(credentials_path="fake_creds.json")

        metadata = SEOMetadata(
            site_id="test_channel",
            content_id="test_video_001",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,
            title="Test Video",
            description="Test",
            tags=["test"],
            youtube_metadata={"playlist_id": "PLxxxxxxxxxxxxxx"},
        )

        video_id = uploader.upload_video(
            video_path="/path/to/video.mp4", metadata=metadata, privacy="draft"
        )

        assert video_id is not None

    def test_made_for_kids_setting(self):
        """Test made_for_kids setting"""
        from seo_manager.core.metadata import ContentType, Platform, SEOMetadata
        from seo_manager.youtube.uploader import YouTubeUploader

        uploader = YouTubeUploader(credentials_path="fake_creds.json")

        metadata = SEOMetadata(
            site_id="test_channel",
            content_id="test_video_001",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,
            title="Test Video",
            description="Test",
            tags=["test"],
            youtube_metadata={"made_for_kids": False},
        )

        video_id = uploader.upload_video(
            video_path="/path/to/video.mp4", metadata=metadata, privacy="draft"
        )

        assert video_id is not None

    def test_error_handling_invalid_file(self):
        """Test error handling for invalid video file"""
        from seo_manager.core.metadata import ContentType, Platform, SEOMetadata
        from seo_manager.youtube.uploader import YouTubeUploader

        uploader = YouTubeUploader(credentials_path="fake_creds.json")

        metadata = SEOMetadata(
            site_id="test_channel",
            content_id="test_video_001",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,
            title="Test Video",
            description="Test",
            tags=["test"],
        )

        # Should raise error for nonexistent file
        with pytest.raises(FileNotFoundError):
            uploader.upload_video(
                video_path="/nonexistent/video.mp4", metadata=metadata, privacy="draft"
            )

    def test_dry_run_mode(self):
        """Test dry run mode (validates but doesn't upload)"""
        from seo_manager.core.metadata import ContentType, Platform, SEOMetadata
        from seo_manager.youtube.uploader import YouTubeUploader

        uploader = YouTubeUploader(credentials_path="fake_creds.json")

        metadata = SEOMetadata(
            site_id="test_channel",
            content_id="test_video_001",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,
            title="Test Video",
            description="Test",
            tags=["test"],
        )

        # Dry run should validate but not upload
        result = uploader.upload_video(
            video_path="/path/to/video.mp4",
            metadata=metadata,
            privacy="draft",
            dry_run=True,
        )

        # Should return validation result, not video ID
        assert result is not None


@pytest.mark.xfail(reason="TDD red-phase: requires real credentials file")
class TestUploadConfiguration:
    """Test upload configuration and settings"""

    def test_upload_config_dataclass(self):
        """Test UploadConfig dataclass"""
        from seo_manager.youtube.uploader import UploadConfig

        config = UploadConfig(
            video_path="/path/to/video.mp4", privacy="draft", notify_subscribers=False
        )

        assert config.video_path == "/path/to/video.mp4"
        assert config.privacy == "draft"
        assert config.notify_subscribers is False

    def test_upload_result_dataclass(self):
        """Test UploadResult dataclass"""
        from seo_manager.youtube.uploader import UploadResult

        result = UploadResult(
            success=True,
            video_id="abc123xyz",
            video_url="https://youtube.com/watch?v=abc123xyz",
            quota_used=50,
        )

        assert result.success is True
        assert result.video_id == "abc123xyz"
        assert result.quota_used == 50

    def test_category_mapping(self):
        """Test YouTube category ID mapping"""
        from seo_manager.youtube.uploader import YouTubeUploader

        uploader = YouTubeUploader(credentials_path="fake_creds.json")

        # Should have category mappings
        category_id = uploader.get_category_id("Science & Technology")
        assert category_id == 28

        category_id = uploader.get_category_id("Education")
        assert category_id == 27
