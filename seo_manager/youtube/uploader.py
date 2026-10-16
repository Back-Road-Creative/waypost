"""
YouTube Video Uploader with SEO Metadata Integration.

Uploads videos to YouTube with optimized metadata, thumbnails,
and scheduling capabilities.

API QUOTA USAGE:
- videos.insert: 1600 units per upload
- thumbnails.set: 50 units
- playlistItems.insert: 50 units
Total per video: ~1700 units (can upload ~5-6 videos per day safely)

Daily quota: 10,000 units
"""

import os
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

# Imported unconditionally: google-api-python-client and google-auth are both
# declared dependencies, and youtube/__init__.py imports this module eagerly,
# so a genuine absence already breaks `import seo_manager.youtube`. The
# try/except that used to sit here only delayed that failure until someone
# constructed a YouTubeUploader, and reported it as a missing-library message
# rather than the real import error.
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from ..analytics.final_text_review import FinalTextReviewRefused, verify_review
from ..analytics.title_selection import DEFAULT_POLICY
from ..platforms.youtube import YouTubeAdapter
from ..validation.validators import PlatformValidator


class PrivacyStatus(Enum):
    """YouTube video privacy statuses"""

    PRIVATE = "private"
    UNLISTED = "unlisted"
    PUBLIC = "public"
    DRAFT = "draft"  # Legacy sentinel — NOT a real YouTube privacyStatus


# Privacy values the YouTube Data API v3 actually accepts. "draft" is a
# legacy sentinel that 400s on upload, so it is deliberately excluded.
VALID_PRIVACY_STATUSES = ("private", "public", "unlisted")


class YouTubeUploadLimits(YouTubeAdapter):
    """YouTube Data API hard limits, used to gate the upload payload.

    YouTubeAdapter's title limit (70) is a mobile-display recommendation; the
    API accepts 100, so refusing 71-100 would reject legal uploads. The API
    caps tags at 500 characters in total, not by count; since every tag is at
    least one character, 500 is a sound (loose) count bound for the validator.
    """

    def get_title_limit(self) -> int:
        return 100

    def get_tag_limit(self) -> int:
        return 500


@dataclass
class UploadConfig:
    """Configuration for video upload"""

    video_path: str
    privacy: str = "draft"
    notify_subscribers: bool = False
    made_for_kids: bool = False
    category_id: Optional[int] = None
    playlist_id: Optional[str] = None
    publish_at: Optional[datetime] = None


@dataclass
class UploadResult:
    """Result of video upload"""

    success: bool
    video_id: Optional[str] = None
    video_url: Optional[str] = None
    quota_used: int = 0
    error: Optional[str] = None


class YouTubeUploader:
    """
    Upload videos to YouTube with SEO-optimized metadata.

    Features:
    - Upload video files
    - Set optimized metadata (title, description, tags)
    - Upload custom thumbnails
    - Set privacy (private, public, unlisted)
    - Schedule publishing
    - Track quota usage
    - Progress callbacks
    """

    # YouTube category mappings
    CATEGORIES = {  # noqa: RUF012
        "Film & Animation": 1,
        "Autos & Vehicles": 2,
        "Music": 10,
        "Pets & Animals": 15,
        "Sports": 17,
        "Short Movies": 18,
        "Travel & Events": 19,
        "Gaming": 20,
        "Videoblogging": 21,
        "People & Blogs": 22,
        "Comedy": 23,
        "Entertainment": 24,
        "News & Politics": 25,
        "Howto & Style": 26,
        "Education": 27,
        "Science & Technology": 28,
        "Nonprofits & Activism": 29,
    }

    # OAuth scopes required for upload
    SCOPES = [  # noqa: RUF012
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube",
        "https://www.googleapis.com/auth/youtube.readonly",
    ]

    def __init__(self, credentials_path: str):
        """
        Initialize YouTube uploader.

        Args:
            credentials_path: Path to OAuth credentials JSON

        Raises:
            FileNotFoundError: If credentials file not found
        """
        if not os.path.exists(credentials_path):
            raise FileNotFoundError(f"Credentials file not found: {credentials_path}")

        # Load credentials
        self.credentials = Credentials.from_authorized_user_file(
            credentials_path, self.SCOPES
        )

        # Build YouTube API client
        self.youtube = build("youtube", "v3", credentials=self.credentials)

        # Track quota usage
        self.quota_used = 0

    def upload_video(
        self,
        video_path: str,
        metadata: "SEOMetadata",  # noqa: F821
        thumbnail_path: Optional[str] = None,
        privacy: str = "private",
        publish_at: Optional[datetime] = None,
        progress_callback: Optional[Callable] = None,
        dry_run: bool = False,
        *,
        final_text_review: Any,
        final_text_context: Mapping[str, Any],
    ) -> Optional[str]:
        """
        Upload video to YouTube with SEO metadata.

        Args:
            video_path: Path to video file
            metadata: SEOMetadata with optimized title/description/tags
            thumbnail_path: Optional custom thumbnail
            privacy: Privacy status ('private', 'public', 'unlisted')
            publish_at: Schedule publish time (for private videos)
            progress_callback: Function(current_bytes, total_bytes)
            dry_run: Validate but don't upload
            final_text_review: The ``FinalTextReview`` (or its persisted dict)
                of this upload's title and on-screen text. Required; there is
                no opt-out, and passing it is not an approval: the caller's
                human gate still applies.
            final_text_context: ``visible`` (surface -> text the upload
                shows), ``source_fingerprint`` and ``plan_fingerprint``,
                recomputed from current state

        Returns:
            Video ID if successful, None if failed

        Raises:
            FileNotFoundError: If video file doesn't exist
            ValueError: If metadata is invalid
            FinalTextReviewRefused: If the review does not cover the title in
                the request body, the visible text, the current source and
                plan, or the title policy in force

        API Quota:
            - Upload: 1600 units
            - Thumbnail: 50 units (if provided)
            - Playlist: 50 units (if specified)
            Total: ~1700 units per video
        """
        # Validate inputs
        if not self.validate_video_file(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")

        if thumbnail_path and not self.validate_thumbnail(thumbnail_path):
            raise FileNotFoundError(f"Thumbnail file not found: {thumbnail_path}")

        # Reject privacy values the YouTube API doesn't accept (notably the
        # legacy "draft" sentinel, which is not a real privacyStatus and 400s
        # on upload). Validate before any API call, including dry runs.
        if privacy not in VALID_PRIVACY_STATUSES:
            raise ValueError(
                f"Invalid privacy {privacy!r}. YouTube privacyStatus must be "
                f"one of {VALID_PRIVACY_STATUSES}."
            )

        # Prepare video metadata
        body = {
            "snippet": {
                "title": metadata.title,
                "description": metadata.description,
                "tags": metadata.tags,
                "categoryId": self._get_category_id_from_metadata(metadata),
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": metadata.youtube_metadata.get(
                    "made_for_kids", False
                ),
            },
        }

        # Add scheduled publish time. Scheduled videos MUST be private until
        # publishAt, and the API requires an RFC3339 timestamp with an explicit
        # UTC offset — a naive datetime.isoformat() emits none and 400s. Force
        # privacyStatus=private (instead of silently dropping the schedule when
        # privacy != "private") and normalize naive datetimes to UTC "Z".
        if publish_at is not None:
            if publish_at.tzinfo is None:
                publish_at = publish_at.replace(tzinfo=timezone.utc)
            body["status"]["privacyStatus"] = "private"
            body["status"]["publishAt"] = (
                publish_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            )

        # Gate the final payload: validate the snippet exactly as it will be
        # sent, so nothing between the caller's metadata and the request body
        # can slip past. Runs for dry runs too, before any API call.
        self.validate_upload_body(metadata, body)
        self.verify_final_text(body, final_text_review, final_text_context)

        if dry_run:
            print("🔍 DRY RUN - Validation only")
            print(f"   Video: {video_path}")
            print(f"   Title: {metadata.title}")
            print(f"   Tags: {len(metadata.tags)} tags")
            print(f"   Privacy: {privacy}")
            return "DRY_RUN_SUCCESS"

        # Create media file upload
        media = MediaFileUpload(
            video_path,
            chunksize=-1,  # Upload in single request
            resumable=True,
        )

        try:
            # Execute upload
            print(f"📤 Uploading video: {Path(video_path).name}")
            print(f"   Title: {metadata.title}")
            print(f"   Privacy: {privacy}")

            request = self.youtube.videos().insert(
                part="snippet,status", body=body, media_body=media
            )

            response = None
            while response is None:
                status, response = request.next_chunk()
                if status and progress_callback:
                    progress_callback(status.resumable_progress, status.total_size)

            video_id = response["id"]
            self.quota_used += 1600  # Upload quota cost

            print(f"   ✅ Uploaded: {video_id}")

            # Upload thumbnail if provided
            if thumbnail_path:
                self._upload_thumbnail(video_id, thumbnail_path)

            # Add to playlist if specified
            playlist_id = metadata.youtube_metadata.get("playlist_id")
            if playlist_id:
                self._add_to_playlist(video_id, playlist_id)

            return video_id

        except HttpError as e:
            error_msg = f"YouTube API error: {e}"
            print(f"   ❌ Upload failed: {error_msg}")
            return None

    def validate_upload_body(self, metadata: "SEOMetadata", body: dict) -> None:  # noqa: F821
        """
        Validate the request body's snippet against YouTube's API limits.

        Args:
            metadata: The caller's SEOMetadata (identity fields only are used)
            body: The videos.insert body that is about to be sent

        Raises:
            ValueError: If the snippet breaks a limit
        """
        snippet = body["snippet"]
        sent = replace(
            metadata,
            title=snippet["title"],
            description=snippet["description"],
            tags=list(snippet["tags"]),
        )
        result = PlatformValidator(YouTubeUploadLimits()).validate(sent)
        if not result.is_valid:
            raise ValueError(
                "Upload payload failed validation: " + "; ".join(result.errors)
            )

    def verify_final_text(
        self, body: dict, review: Any, context: Mapping[str, Any]
    ) -> None:
        """Refuse a body whose title, description, tags or on-screen text was not reviewed.

        The description and tags in the body are always compared with the
        reviewed typed surfaces (``compose_description`` / ``compose_tags``);
        there is no opt-out, so build them with those functions.
        """
        if not isinstance(context, Mapping):
            raise FinalTextReviewRefused(["review_malformed"], "no review context")
        description = body["snippet"].get("description")
        if not isinstance(description, str):
            # None would skip the description bind in ``verify_review``.
            raise FinalTextReviewRefused(
                ["stale:description"], "the body carries no description"
            )
        verify_review(
            review,
            final_title=body["snippet"]["title"],
            visible=context.get("visible") or {},
            source_fingerprint=context.get("source_fingerprint") or "",
            plan_fingerprint=context.get("plan_fingerprint") or "",
            policy_version=DEFAULT_POLICY.version,
            description=description,
            tags=list(body["snippet"].get("tags") or []),
        )

    def _upload_thumbnail(self, video_id: str, thumbnail_path: str):
        """
        Upload custom thumbnail for video.

        Args:
            video_id: YouTube video ID
            thumbnail_path: Path to thumbnail image

        API Quota: 50 units
        """
        try:
            print("   📸 Uploading thumbnail...")

            self.youtube.thumbnails().set(
                videoId=video_id, media_body=MediaFileUpload(thumbnail_path)
            ).execute()

            self.quota_used += 50
            print("   ✅ Thumbnail uploaded")

        except HttpError as e:
            print(f"   ⚠️  Thumbnail upload failed: {e}")

    def _add_to_playlist(self, video_id: str, playlist_id: str):
        """
        Add video to playlist.

        Args:
            video_id: YouTube video ID
            playlist_id: YouTube playlist ID

        API Quota: 50 units
        """
        try:
            print("   📋 Adding to playlist...")

            self.youtube.playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {"kind": "youtube#video", "videoId": video_id},
                    }
                },
            ).execute()

            self.quota_used += 50
            print("   ✅ Added to playlist")

        except HttpError as e:
            print(f"   ⚠️  Playlist add failed: {e}")

    def _get_category_id_from_metadata(self, metadata: "SEOMetadata") -> int:  # noqa: F821
        """
        Extract category ID from metadata.

        Args:
            metadata: SEOMetadata

        Returns:
            Category ID (default: 28 = Science & Technology)
        """
        category_name = metadata.youtube_metadata.get(
            "category", "Science & Technology"
        )
        return self.get_category_id(category_name)

    def get_category_id(self, category_name: str) -> int:
        """
        Get YouTube category ID from name.

        Args:
            category_name: Category name

        Returns:
            Category ID
        """
        return self.CATEGORIES.get(category_name, 28)  # Default: Science & Technology

    def validate_video_file(self, video_path: str) -> bool:
        """
        Validate video file exists and is readable.

        Args:
            video_path: Path to video file

        Returns:
            True if valid, False otherwise
        """
        path = Path(video_path)
        return path.exists() and path.is_file()

    def validate_thumbnail(self, thumbnail_path: str) -> bool:
        """
        Validate thumbnail file exists and is an image.

        Args:
            thumbnail_path: Path to thumbnail file

        Returns:
            True if valid, False otherwise
        """
        path = Path(thumbnail_path)
        if not path.exists() or not path.is_file():
            return False

        # Check if it's an image format
        valid_extensions = {".jpg", ".jpeg", ".png"}
        return path.suffix.lower() in valid_extensions

    def get_quota_usage(self) -> int:
        """
        Get total quota used in this session.

        Returns:
            Quota units used
        """
        return self.quota_used
