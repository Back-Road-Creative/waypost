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

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.metadata import SEOMetadata
from ..paths import data_home
from ..quota.manager import (
    DEFAULT_BUCKET,
    YOUTUBE_DATA,
    QuotaExceededError,
    QuotaManager,
)
from ..transport import AuthError, QuotaExhaustedError
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
        final_text_review: This video's final-text review; a video without
            one is refused at upload
        final_text_context: ``visible``, ``source_fingerprint`` and
            ``plan_fingerprint`` for the review check
    """

    video_path: str
    metadata: SEOMetadata | None
    privacy: str = "private"
    thumbnail_path: str | None = None
    final_text_review: Any = None
    final_text_context: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class BatchUploadConfig:
    """
    Configuration for batch upload operation.

    Attributes:
        videos: List of VideoConfig objects to upload
        continue_on_error: Continue uploading if one video fails
        dry_run: Validate without actually uploading
    """

    videos: list[VideoConfig]
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
        quota_used: Default-pool units used for this video (thumbnail and
            playlist calls). The ``videos.insert`` call is counted in its own
            bucket, not in these units.
        error: Error message (if failed)
        error_class: ``quota_exhausted``, ``quota_budget``, ``auth`` or the
            failure's own ``error_class`` (e.g. ``outcome_unknown``), if any
        deferred: Not tried (or refused) for quota; retry at ``next_eligible``
        next_eligible: ISO time the quota resets, when known
    """

    video_path: str
    success: bool
    video_id: str | None = None
    video_url: str | None = None
    quota_used: int = 0
    error: str | None = None
    error_class: str | None = None
    deferred: bool = False
    next_eligible: str | None = None


@dataclass
class BatchUploadResult:
    """
    Result of batch upload operation.

    Attributes:
        total_videos: Total number of videos in batch
        successful: Number of successful uploads
        failed: Number of failed uploads
        quota_used: Total default-pool units used. ``videos.insert`` calls are
            counted in their own bucket, not in these units.
        results: List of individual video results
        errors: List of error messages
    """

    total_videos: int
    successful: int
    failed: int
    quota_used: int
    results: list[VideoUploadResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

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

    API Quota Usage (see ``QuotaManager`` for the cost table):
    - videos.insert: 1 call in its own ``youtube_data/videos.insert`` bucket
      (100 per day by default), not default-pool units
    - thumbnails.set: 50 units of the default pool (if a thumbnail is given)
    - playlistItems.insert: 50 units of the default pool (if a playlist is set)

    Default pool: 10,000 units per day. Every cost is read from the
    ``QuotaManager`` operation table; nothing is hard-coded here.
    """

    def __init__(
        self,
        credentials_path: str,
        project_id: str,
        *,
        quota: QuotaManager | None = None,
        brand_id: str | None = None,
    ):
        """
        Initialize batch uploader.

        Args:
            credentials_path: Path to OAuth credentials JSON
            project_id: Google Cloud project ID
            quota: Quota budget shared with the uploader. Default: a
                ``QuotaManager`` stored under ``data_home() / "quota"``.
            brand_id: Brand the quota is charged to

        Raises:
            ImportError: If Google API libraries not installed
            FileNotFoundError: If credentials file not found
        """
        self.credentials_path = credentials_path
        self.project_id = project_id
        self.brand_id = brand_id
        self.quota = quota or QuotaManager(
            project_id=project_id,
            storage_path=data_home() / "quota",
            brand_id=brand_id,
        )

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
            self._uploader = YouTubeUploader(
                self.credentials_path, quota=self.quota, brand_id=self.brand_id
            )
        return self._uploader

    def calculate_batch_cost(
        self,
        video_count: int,
        with_thumbnails: bool = False,
        playlist_count: int = 0,
    ) -> int:
        """
        Calculate the default-pool units a batch needs.

        ``videos.insert`` is one call in its own bucket and adds no pool units;
        see ``check_batch_quota`` for that bucket. Costs come from the
        ``QuotaManager`` operation table.

        Args:
            video_count: Number of videos to upload
            with_thumbnails: Whether every video has a thumbnail
            playlist_count: Number of videos that are added to a playlist

        Returns:
            Default-pool units required
        """
        total = playlist_count * self.quota.estimate_cost("playlistItems.insert")
        if with_thumbnails:
            total += video_count * self.quota.estimate_cost("thumbnails.set")
        return total

    def _shortfalls(
        self, video_count: int, with_thumbnails: bool, playlist_count: int
    ) -> list[str]:
        """Describe each bucket that cannot cover the batch (advisory, no reservation)."""
        problems = []
        insert = self.quota.resolve("videos.insert")
        inserts_left = self.quota.get_bucket_usage(insert.api, insert.bucket)[
            "remaining"
        ]
        if inserts_left is not None and inserts_left < video_count * insert.cost:
            problems.append(
                f"videos.insert bucket has {inserts_left} calls left, "
                f"batch needs {video_count * insert.cost}"
            )
        needed = self.calculate_batch_cost(video_count, with_thumbnails, playlist_count)
        pool_left = self.quota.get_bucket_usage(YOUTUBE_DATA, DEFAULT_BUCKET)[
            "remaining"
        ]
        if pool_left is not None and pool_left < needed:
            problems.append(
                f"default pool has {pool_left} units left, batch needs {needed}"
            )
        return problems

    def check_batch_quota(
        self,
        video_count: int,
        with_thumbnails: bool = True,
        playlist_count: int = 0,
    ) -> bool:
        """
        Check if the batch fits today's remaining quota.

        Advisory: nothing is reserved here. Real reservations happen per
        request inside ``YouTubeUploader``. Checks that the ``videos.insert``
        bucket has at least ``video_count`` calls left and that the default
        pool covers the thumbnails and playlist inserts. An unbudgeted bucket
        always passes.

        Args:
            video_count: Number of videos to upload
            with_thumbnails: Whether thumbnails will be uploaded
            playlist_count: Number of videos that are added to a playlist

        Returns:
            True if quota available, False if would exceed
        """
        return not self._shortfalls(video_count, with_thumbnails, playlist_count)

    def validate_batch_quota(
        self,
        video_count: int,
        with_thumbnails: bool = True,
        playlist_count: int = 0,
    ):
        """
        Validate batch quota and raise error if would exceed.

        Args:
            video_count: Number of videos to upload
            with_thumbnails: Whether thumbnails will be uploaded
            playlist_count: Number of videos that are added to a playlist

        Raises:
            BatchQuotaError: If batch would exceed quota limits
        """
        problems = self._shortfalls(video_count, with_thumbnails, playlist_count)
        if problems:
            raise BatchQuotaError(
                "Batch upload would exceed quota limits: "
                + "; ".join(problems)
                + ". Try uploading fewer videos."
            )

    def validate_all_metadata(self, config: BatchUploadConfig) -> list[str]:
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

    @staticmethod
    def _playlist_id(video: "VideoConfig") -> str | None:
        """The playlist the uploader adds this video to, if any."""
        if video.metadata is None:
            return None
        return (video.metadata.youtube_metadata or {}).get("playlist_id")

    def upload_batch(
        self,
        config: BatchUploadConfig,
        progress_callback: Callable[[int, int, str], None] | None = None,
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
        playlist_count = sum(1 for v in config.videos if self._playlist_id(v))
        self.validate_batch_quota(total_videos, has_thumbnails, playlist_count)

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
                    final_text_review=video.final_text_review,
                    final_text_context=video.final_text_context,
                )

                if not isinstance(video_id, str) or not video_id.strip():
                    raise RuntimeError("Upload returned no video ID")

                # Calculate quota used
                # (default-pool units only; the insert has its own bucket)
                quota_used = 0
                if video.thumbnail_path:
                    quota_used += self.quota.estimate_cost("thumbnails.set")
                if self._playlist_id(video):
                    quota_used += self.quota.estimate_cost("playlistItems.insert")

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

            except (QuotaExhaustedError, QuotaExceededError, AuthError) as e:
                # Nothing more can be sent today (or with these credentials):
                # this video and every untried one are recorded, not attempted.
                if isinstance(e, AuthError):
                    error_class, deferred, next_eligible = "auth", False, None
                elif isinstance(e, QuotaExhaustedError):
                    error_class, deferred = "quota_exhausted", True
                    next_eligible = e.next_eligible.isoformat()
                else:
                    error_class, deferred, next_eligible = "quota_budget", True, None
                for j, rest in enumerate(config.videos[i - 1 :], i):
                    result.results.append(
                        VideoUploadResult(
                            video_path=rest.video_path,
                            success=False,
                            error=str(e) if j == i else f"not tried: {error_class}",
                            error_class=error_class,
                            deferred=deferred,
                            next_eligible=next_eligible,
                        )
                    )
                    result.failed += 1
                result.errors.append(f"Video {i} ({video_title}): {e}")
                print(f"   🛑 {error_class}: stopping the batch ({e})")
                break

            except Exception as e:
                # Record failure
                error_msg = str(e)
                video_result = VideoUploadResult(
                    video_path=video.video_path,
                    success=False,
                    error=error_msg,
                    quota_used=0,
                    error_class=getattr(e, "error_class", None),
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
