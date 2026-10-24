"""
YouTube Analytics API Integration.

Automatically syncs video performance metrics from YouTube Analytics API
to SEO Manager for data-driven optimization.

Requires google-api-python-client, google-auth, google-auth-oauthlib and
google-auth-httplib2. All four are declared in pyproject.toml, so a normal
install of this package brings them; there is no reduced mode without them.
"""

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

# Imported unconditionally on purpose. These used to sit behind a
# `try/except ImportError` that set GOOGLE_APIS_AVAILABLE = False and printed a
# warning, which made a missing dependency look identical to a healthy import:
# pyproject.toml did not declare google-auth-oauthlib, so this block raised on
# every CI run and analytics sync was quietly unavailable for as long as that
# lasted. Every one of these is now a declared dependency, so an ImportError
# here is a real broken environment and must say so at import time.
from google.auth.transport.requests import Request  # noqa: F401
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from . import metric_source

# Default scopes used ONLY for the interactive client-secret flow
# (create_credentials_from_flow) and as a last-resort fallback when a token
# file carries no "scopes" key. In normal operation the loaded token's OWN
# scope list drives access — these are NOT authoritative and must not be
# advertised on the class as if they were (a stale superset/subset there
# silently lies about what the credential can actually do, which is why the
# old class-level SCOPES constant was removed). A token minted with broader scopes
# is a superset of these.
_DEFAULT_FLOW_SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]

# ANALYTICS_CTR_LIMITATION — the true thumbnail-impression CTR
# ("impressions" / "impressionClickThroughRate") shown in YouTube Studio is
# NOT exposed by the YouTube Analytics API v2: Google deliberately keeps it
# Studio-UI-only (verified against developers.google.com/youtube/analytics/
# metrics; corroborated by issuetracker.google.com/issues/254665034).
# The closest real click-through signal the API DOES expose is `cardClickRate`
# (with `cardImpressions`/`cardClicks`). We surface that as the `ctr` proxy and
# document it; we never synthesize a fake impression CTR, and a missing row
# degrades to None (not a 0 that masquerades as real data). A durable true-CTR
# backfill would require a Studio CSV-export ingest path.
_ANALYTICS_METRICS = [
    "views",
    "estimatedMinutesWatched",
    "averageViewPercentage",
    "cardImpressions",
    "cardClicks",
    "cardClickRate",
]


# YouTube Analytics reports by Pacific calendar day. A far-future end date is
# refused with HTTP 400 ("Invalid value (2100-01-01) given in field
# parameters.end-date."), so the lifetime window ends on today's date there.
_ANALYTICS_START_DATE = "2005-01-01"
_ANALYTICS_ZONE = ZoneInfo("America/Los_Angeles")


def analytics_report_window(now: datetime | None = None) -> tuple[str, str]:
    """Lifetime ``(startDate, endDate)`` for a per-video Analytics report."""
    now = now or datetime.now(_ANALYTICS_ZONE)
    return _ANALYTICS_START_DATE, now.astimezone(_ANALYTICS_ZONE).date().isoformat()


class YouTubeAnalyticsSync:
    """
    Syncs YouTube Analytics data to SEO Manager.

    Automatically fetches video performance metrics (views, likes, comments)
    and tracks them in the SEO Manager analytics system.

    Requires YouTube Data API v3 access and OAuth 2.0 credentials.
    """

    def __init__(self, credentials_path: str):
        """
        Initialize YouTube Analytics sync.

        Args:
            credentials_path: Path to OAuth 2.0 credentials JSON file

        Raises:
            FileNotFoundError: If credentials file not found
        """
        if not os.path.exists(credentials_path):
            raise FileNotFoundError(f"Credentials file not found: {credentials_path}")

        # Load credentials — use scopes from the token file itself to avoid
        # mismatch when the token was authorized with a superset of scopes
        with open(credentials_path) as f:
            token_data = json.load(f)
        token_scopes = token_data.get("scopes", _DEFAULT_FLOW_SCOPES)

        self.credentials = Credentials.from_authorized_user_file(
            credentials_path, token_scopes
        )

        # Build API clients
        self.youtube_data = build("youtube", "v3", credentials=self.credentials)
        self.youtube_analytics = build(
            "youtubeAnalytics", "v2", credentials=self.credentials
        )

    def fetch_video_statistics(self, video_id: str) -> dict | None:
        """
        Fetch statistics for a single video.

        Args:
            video_id: YouTube video ID

        Returns:
            Dict with statistics or None if error

        Example response:
            {
                'viewCount': '1250',
                'likeCount': '87',
                'commentCount': '23',
                'favoriteCount': '0'
            }
        """
        try:
            response = (
                self.youtube_data.videos()
                .list(part="statistics", id=video_id)
                .execute()
            )

            if "items" in response and len(response["items"]) > 0:
                return response["items"][0]["statistics"]
            else:
                return None

        except HttpError as e:
            print(f"Error fetching stats for video {video_id}: {e}")
            return None

    def fetch_video_analytics(self, video_id: str) -> dict | None:
        """
        Fetch click/engagement metrics from the YouTube *Analytics* API v2.

        This is the call the old code never made — it requested only the Data
        API ``part="statistics"`` (views/likes/comments) and ignored the
        already-built ``youtubeAnalytics`` v2 client, so the optimizer's
        ``performer.get("ctr", 0)`` was structurally always 0.

        Requests ``_ANALYTICS_METRICS`` for the single video. See
        ANALYTICS_CTR_LIMITATION: the API exposes no true impression-CTR, so
        ``cardClickRate`` is the click-through proxy.

        Args:
            video_id: YouTube video ID

        Returns:
            ``{metric_name: value}`` for the requested metrics, or ``None``
            when the report has no row (no-data is degraded explicitly — the
            caller threads ``ctr=None``, never a fake 0 that looks real).
        """
        start_date, end_date = analytics_report_window()
        try:
            response = (
                self.youtube_analytics.reports()
                .query(
                    ids="channel==MINE",
                    startDate=start_date,
                    endDate=end_date,
                    metrics=",".join(_ANALYTICS_METRICS),
                    filters=f"video=={video_id}",
                )
                .execute()
            )
        except HttpError as e:
            print(f"Error fetching analytics for video {video_id}: {e}")
            return None

        rows = response.get("rows") or []
        if not rows:
            # Degrade explicitly — no analytics data for this video.
            return None

        headers = [h["name"] for h in response.get("columnHeaders", [])]
        return dict(zip(headers, rows[0], strict=False))

    def sync_video_analytics(
        self, manager, video_id: str, content_id: str
    ) -> str | None:
        """
        Fetch analytics for one video and sync to SEO Manager.

        Args:
            manager: SEOManager instance
            video_id: YouTube video ID
            content_id: Content ID in SEO Manager

        Returns:
            Event ID if successful, None if error
        """
        # Fetch statistics from YouTube
        stats = self.fetch_video_statistics(video_id)

        if not stats:
            return None

        # Parse Data-API metrics
        views = int(stats.get("viewCount", 0))
        likes = int(stats.get("likeCount", 0))
        comments = int(stats.get("commentCount", 0))

        # Calculate engagement rate
        engagement_rate = self._calculate_engagement_rate(stats)

        # Pull click signals from the Analytics API. None == no data
        # available — we thread ctr=None so the tracker stores an honest
        # "unmeasured" rather than a fabricated 0 (ANALYTICS_CTR_LIMITATION).
        #
        # Every number below is CARD-derived: the denominator is viewers
        # already watching, not people shown the thumbnail. ctr_source carries
        # that with the value, so no downstream consumer has to read this
        # comment to find out.
        analytics = self.fetch_video_analytics(video_id)
        clicks = None
        impressions = None
        ctr = None
        ctr_source = metric_source.UNKNOWN
        if analytics is not None:
            impressions = analytics.get("cardImpressions")
            clicks = analytics.get("cardClicks")
            # cardClickRate is a 0..1 rate; the API reports it directly. Fall
            # back to clicks/impressions only if the rate field is absent.
            ctr = analytics.get("cardClickRate")
            if ctr is None and impressions:
                ctr = clicks / impressions
            if ctr is not None:
                ctr_source = metric_source.YOUTUBE_CARD_CLICK_RATE

        # Track in SEO Manager
        event_id = manager.analytics.track_performance(
            content_id=content_id,
            views=views,
            clicks=clicks,
            impressions=impressions,
            engagement_rate=engagement_rate,
            ctr=ctr,
            ctr_source=ctr_source,
            # Additional YouTube-specific metrics
            likes=likes,
            comments=comments,
        )

        return event_id

    def batch_sync(self, manager, video_mapping: dict[str, str]) -> list[dict]:
        """
        Sync analytics for multiple videos in batch.

        Args:
            manager: SEOManager instance
            video_mapping: Dict of {video_id: content_id}

        Returns:
            List of results with success status

        Example:
            results = sync.batch_sync(manager, {
                "abc123": "episode_001",
                "def456": "episode_002"
            })
        """
        results = []

        for video_id, content_id in video_mapping.items():
            try:
                event_id = self.sync_video_analytics(manager, video_id, content_id)

                results.append(
                    {
                        "video_id": video_id,
                        "content_id": content_id,
                        "success": event_id is not None,
                        "event_id": event_id,
                    }
                )

            except Exception as e:
                results.append(
                    {
                        "video_id": video_id,
                        "content_id": content_id,
                        "success": False,
                        "error": str(e),
                    }
                )

        return results

    def _calculate_engagement_rate(self, stats: dict) -> float:
        """
        Calculate engagement rate from video statistics.

        Engagement = (likes + comments) / views

        Args:
            stats: Video statistics dict

        Returns:
            Engagement rate (0.0-1.0)
        """
        views = int(stats.get("viewCount", 0))

        if views == 0:
            return 0.0

        likes = int(stats.get("likeCount", 0))
        comments = int(stats.get("commentCount", 0))

        engagement = (likes + comments) / views

        return round(engagement, 4)

    @staticmethod
    def create_credentials_from_flow(client_secrets_path: str, output_path: str) -> str:
        """
        Create OAuth credentials from client secrets (interactive).

        This is a one-time setup process that opens a browser
        for user authentication.

        Args:
            client_secrets_path: Path to client_secret.json from Google Cloud
            output_path: Where to save the credentials

        Returns:
            Path to saved credentials file
        """
        flow = InstalledAppFlow.from_client_secrets_file(
            client_secrets_path, _DEFAULT_FLOW_SCOPES
        )

        credentials = flow.run_local_server(port=0)

        # Save credentials
        with open(output_path, "w") as f:
            f.write(credentials.to_json())

        return output_path
