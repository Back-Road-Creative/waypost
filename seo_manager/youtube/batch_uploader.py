"""
Batch Video Uploader for YouTube.

Uploads multiple videos in a single operation with proper quota management,
progress tracking, and error handling.

Features:
- Batch upload multiple videos sequentially
- Quota validation before starting
- Progress callbacks (video X/Y)
- Error handling with continue_on_error mode
- Dry run mode for validation
- Per-video thumbnail support
- Comprehensive result tracking
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from ..core.metadata import SEOMetadata
from .uploader import YouTubeUploader


class BatchQuotaError(Exception):
    """Raised when batch upload would exceed quota limits"""

    pass


@dataclass
class VideoConfig:
    """
    Configuration for a single video in batch upload.

    Attributes:
        video_path: Path to video file
        metadata: SEO metadata for the video
        thumbnail_path: Optional custom thumbnail
        privacy: Privacy status ('private', 'public', 'unlisted')
    """

    video_path: str
    metadata: Optional[SEOMetadata]
    privacy: str = "private"
    thumbnail_path: Optional[str] = None


@dataclass
class BatchUploadConfig:
    """
    Configuration for batch upload operation.

    Attributes:
        videos: List of VideoConfig objects to upload
        continue_on_error: Continue uploading if one video fails
        dry_run: Validate without actually uploading
    """

    videos: List[VideoConfig]
    continue_on_error: bool = True
    dry_run: bool = False


@dataclass
class VideoUploadResult:
    """
    Result for a single video upload.

    Attributes:
        video_path: Path to video file
        success: Whether upload succeeded
        video_id: YouTube video ID (if successful)
        video_url: YouTube video URL (if successful)
        quota_used: Quota units used for this video
        error: Error message (if failed)
    """

    video_path: str
    success: bool
    video_id: Optional[str] = None
    video_url: Optional[str] = None
    quota_used: int = 0
    error: Optional[str] = None


@dataclass
class BatchUploadResult:
    """
    Result of batch upload operation.

    Attributes:
        total_videos: Total number of videos in batch
        successful: Number of successful uploads
        failed: Number of failed uploads
        quota_used: Total quota units used
        results: List of individual video results
        errors: List of error messages
    """

    total_videos: int
    successful: int
    failed: int
    quota_used: int
    results: List[VideoUploadResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def is_complete_success(self) -> bool:
        """
        Check if all videos uploaded successfully.

        Returns:
            True if all succeeded, False if any failed
        """
        return self.failed == 0 and self.successful == self.total_videos

    def has_failures(self) -> bool:
        """
        Check if any videos failed to upload.

        Returns:
            True if any failed, False if all succeeded
        """
        return self.failed > 0

    def success_rate(self) -> float:
        """
        Calculate success rate as percentage.

        Returns:
            Success rate (0.0-1.0)
        """
        if self.total_videos == 0:
            return 0.0
        return self.successful / self.total_videos


class BatchUploader:
    """
    Batch upload multiple videos to YouTube with quota management.

    Features:
    - Sequential upload with progress tracking
    - Quota validation before starting
    - Error handling (continue or stop on error)
    - Dry run mode
    - Per-video metadata and thumbnails
    - Comprehensive result reporting

    API Quota Usage:
    - videos.insert: 1600 units per video
    - thumbnails.set: 50 units per video (if thumbnail provided)
    - Total: ~1650 units per video

    Daily quota: 10,000 units (~6 videos per day safely)
    """

    # Quota costs
    UPLOAD_QUOTA_COST = 1600  # videos.insert
    THUMBNAIL_QUOTA_COST = 50  # thumbnails.set

    def __init__(self, credentials_path: str, project_id: str):
        """
        Initialize batch uploader.

        Args:
            credentials_path: Path to OAuth credentials JSON
            project_id: Google Cloud project ID

        Raises:
            ImportError: If Google API libraries not installed
            FileNotFoundError: If credentials file not found
        """
        self.credentials_path = credentials_path
        self.project_id = project_id

        # Initialize uploader (will be created when needed)
        self._uploader = None

        # Track quota usage in this session
        self.session_quota_used = 0

    def _get_uploader(self) -> YouTubeUploader:
        """
        Get or create YouTubeUploader instance.

        Returns:
            YouTubeUploader instance
        """
        if self._uploader is None:
            self._uploader = YouTubeUploader(self.credentials_path)
        return self._uploader

    def calculate_batch_cost(
        self, video_count: int, with_thumbnails: bool = False
    ) -> int:
        """
        Calculate total quota cost for batch upload.

        Args:
            video_count: Number of videos to upload
            with_thumbnails: Whether thumbnails will be uploaded (default: False)

        Returns:
            Total quota units required
        """
        cost_per_video = self.UPLOAD_QUOTA_COST
        if with_thumbnails:
            cost_per_video += self.THUMBNAIL_QUOTA_COST

        return video_count * cost_per_video

    def check_batch_quota(self, video_count: int, with_thumbnails: bool = True) -> bool:
        """
        Check if batch upload is within quota limits.

        Uses conservative estimate:
        - Daily quota: 10,000 units
        - Safe limit: 8,000 units (buffer for other operations)

        Args:
            video_count: Number of videos to upload
            with_thumbnails: Whether thumbnails will be uploaded

        Returns:
            True if quota available, False if would exceed
        """
        total_cost = self.calculate_batch_cost(video_count, with_thumbnails)

        # Conservative daily limit (leave buffer)
        SAFE_DAILY_LIMIT = 8000

        return total_cost <= SAFE_DAILY_LIMIT

    def validate_batch_quota(self, video_count: int, with_thumbnails: bool = True):
        """
        Validate batch quota and raise error if would exceed.

        Args:
            video_count: Number of videos to upload
            with_thumbnails: Whether thumbnails will be uploaded

        Raises:
            BatchQuotaError: If batch would exceed quota limits
        """
        if not self.check_batch_quota(video_count, with_thumbnails):
            total_cost = self.calculate_batch_cost(video_count, with_thumbnails)
            raise BatchQuotaError(
                f"Batch upload would exceed quota limits. "
                f"Required: {total_cost} units, Safe limit: 8000 units. "
                f"Try uploading fewer videos."
            )

    def validate_all_metadata(self, config: BatchUploadConfig) -> List[str]:
        """
        Validate metadata for all videos before uploading.

        Args:
            config: Batch upload configuration

        Returns:
            List of validation errors (empty if all valid)
        """
        errors = []

        for i, video in enumerate(config.videos):
            # Check video file exists
            if not Path(video.video_path).exists():
                errors.append(f"Video {i + 1}: File not found: {video.video_path}")

            # Check metadata
            if video.metadata is None:
                errors.append(f"Video {i + 1}: No metadata provided")
            else:
                # Validate metadata fields
                if not video.metadata.title:
                    errors.append(f"Video {i + 1}: Missing title")
                if not video.metadata.description:
                    errors.append(f"Video {i + 1}: Missing description")
                if not video.metadata.tags:
                    errors.append(f"Video {i + 1}: Missing tags")

            # Check thumbnail if provided
            if video.thumbnail_path and not Path(video.thumbnail_path).exists():
                errors.append(
                    f"Video {i + 1}: Thumbnail not found: {video.thumbnail_path}"
                )

            # Validate privacy status. "draft" is not a real YouTube
            # privacyStatus — it 400s on upload — so it is not whitelisted.
            valid_privacy = ["unlisted", "public", "private"]
            if video.privacy not in valid_privacy:
                errors.append(
                    f"Video {i + 1}: Invalid privacy '{video.privacy}'. "
                    f"Must be one of: {', '.join(valid_privacy)}"
                )

        return errors

    def upload_batch(
        self,
        config: BatchUploadConfig,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> BatchUploadResult:
        """
        Upload multiple videos in batch.

        Videos are uploaded sequentially in the order provided.

        Args:
            config: Batch upload configuration
            progress_callback: Optional callback(current, total, video_title)

        Returns:
            BatchUploadResult with summary and per-video results

        Raises:
            BatchQuotaError: If batch would exceed quota (before upload starts)
            ValueError: If validation fails and not in dry_run mode
        """
        total_videos = len(config.videos)

        # Initialize result
        result = BatchUploadResult(
            total_videos=total_videos, successful=0, failed=0, quota_used=0
        )

        # Validate all metadata before starting
        validation_errors = self.validate_all_metadata(config)
        if validation_errors:
            if config.dry_run:
                # In dry run, report errors but don't raise
                result.errors.extend(validation_errors)
                print("⚠️  Validation errors found:\n" + "\n".join(validation_errors))
                return result
            else:
                # In real mode, raise error
                raise ValueError("Validation failed:\n" + "\n".join(validation_errors))

        # Check quota before starting
        has_thumbnails = any(v.thumbnail_path is not None for v in config.videos)
        self.validate_batch_quota(total_videos, has_thumbnails)

        if config.dry_run:
            print(f"🔍 DRY RUN - Validating {total_videos} videos")

        # Upload each video sequentially
        for i, video in enumerate(config.videos, 1):
            video_title = (
                video.metadata.title if video.metadata else Path(video.video_path).name
            )

            # Progress callback
            if progress_callback:
                progress_callback(i, total_videos, video_title)

            print(f"\n📤 [{i}/{total_videos}] Uploading: {video_title}")

            try:
                # Get uploader instance
                uploader = self._get_uploader()

                # Upload video
                video_id = uploader.upload_video(
                    video_path=video.video_path,
                    metadata=video.metadata,
                    thumbnail_path=video.thumbnail_path,
                    privacy=video.privacy,
                    dry_run=config.dry_run,
                )

                # Calculate quota used
                quota_used = self.UPLOAD_QUOTA_COST
                if video.thumbnail_path:
                    quota_used += self.THUMBNAIL_QUOTA_COST

                # Record success
                video_result = VideoUploadResult(
                    video_path=video.video_path,
                    success=True,
                    video_id=video_id,
                    video_url=f"https://youtube.com/watch?v={video_id}"
                    if video_id
                    else None,
                    quota_used=quota_used,
                )
                result.results.append(video_result)
                result.successful += 1
                result.quota_used += quota_used
                self.session_quota_used += quota_used

                print(f"   ✅ Success: {video_id}")

            except Exception as e:
                # Record failure
                error_msg = str(e)
                video_result = VideoUploadResult(
                    video_path=video.video_path,
                    success=False,
                    error=error_msg,
                    quota_used=0,
                )
                result.results.append(video_result)
                result.failed += 1
                result.errors.append(f"Video {i} ({video_title}): {error_msg}")

                print(f"   ❌ Failed: {error_msg}")

                # Stop or continue based on config
                if not config.continue_on_error:
                    print("\n🛑 Stopping batch upload due to error")
                    break

        # Print summary
        print(f"\n{'=' * 60}")
        print("📊 BATCH UPLOAD SUMMARY")
        print(f"{'=' * 60}")
        print(f"Total videos:     {result.total_videos}")
        print(f"✅ Successful:     {result.successful}")
        print(f"❌ Failed:         {result.failed}")
        print(f"📈 Success rate:   {result.success_rate():.1%}")
        print(f"💰 Quota used:     {result.quota_used} units")
        print(f"{'=' * 60}")

        if result.errors:
            print("\n⚠️  ERRORS:")
            for error in result.errors:
                print(f"  - {error}")

        return result
