"""
YouTube Theme Sync - Syncs YouTube analytics with theme rotation tracking.

Pulls performance data from YouTube and updates the ThemeRotator with
actual views, CTR, and engagement metrics.

This enables data-driven theme selection based on real performance.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from .. import paths
from . import evidence_age
from .theme_rotator import ThemeRotator
from .thumbnail_ab_tracker import ThumbnailABTracker

logger = logging.getLogger(__name__)


class YouTubeThemeSync:
    """
    Syncs YouTube Analytics data with theme and thumbnail trackers.

    Example:
        sync = YouTubeThemeSync(
            youtube_service=get_youtube_service(),
            theme_rotator=ThemeRotator(state_dir=Path("./data/themes")),
            thumbnail_tracker=ThumbnailABTracker(state_dir=Path("./data/thumbnails"))
        )

        # Sync last 30 days of data
        sync.sync_all(days=30)

        # Generate reports
        print(sync.theme_rotator.generate_report())
        print(sync.thumbnail_tracker.generate_report())
    """

    def __init__(
        self,
        youtube_service=None,
        theme_rotator: Optional[ThemeRotator] = None,
        thumbnail_tracker: Optional[ThumbnailABTracker] = None,
        state_dir: Optional[Path] = None,
    ):
        """
        Initialize the sync.

        Args:
            youtube_service: YouTube Data API service object
            theme_rotator: ThemeRotator instance
            thumbnail_tracker: ThumbnailABTracker instance
            state_dir: Default state directory for trackers (defaults to
                ``theme_sync`` under the seo-manager data home, see
                ``seo_manager.paths``)
        """
        self.youtube = youtube_service

        state_dir = state_dir or paths.data_home() / "theme_sync"

        self.theme_rotator = theme_rotator or ThemeRotator(
            state_dir=state_dir / "theme_rotation"
        )
        self.thumbnail_tracker = thumbnail_tracker or ThumbnailABTracker(
            state_dir=state_dir / "thumbnail_ab"
        )

        # Mapping file: video_id -> content_id
        self.mapping_file = state_dir / "video_content_mapping.json"
        self.video_mapping: Dict[str, str] = {}
        self._load_mapping()

    def _load_mapping(self):
        """Load video -> content mapping."""
        if self.mapping_file.exists():
            try:
                with open(self.mapping_file) as f:
                    self.video_mapping = json.load(f)
            except json.JSONDecodeError:
                self.video_mapping = {}

    def _save_mapping(self):
        """Save video -> content mapping."""
        self.mapping_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.mapping_file, "w") as f:
            json.dump(self.video_mapping, f, indent=2)

    def register_video(self, content_id: str, video_id: str):
        """
        Register a video ID for a content ID.

        Call this after uploading to YouTube.
        """
        self.video_mapping[video_id] = content_id
        self._save_mapping()
        logger.info(f"Registered video mapping: {video_id} -> {content_id}")

    def sync_all(self, days: int = 30) -> Dict:
        """
        Sync all tracked videos with YouTube Analytics.

        Args:
            days: Number of days of analytics to fetch

        Returns:
            Summary of sync results
        """
        if not self.youtube:
            logger.warning("No YouTube service - cannot sync analytics")
            return {"error": "No YouTube service configured"}

        results = {"synced": 0, "failed": 0, "skipped": 0, "details": []}

        # Get all video IDs we're tracking
        tracked_videos = set()

        for _content_id, perf in self.theme_rotator.usage.items():
            if perf.video_id:
                tracked_videos.add(perf.video_id)

        for _content_id, perf in self.thumbnail_tracker.thumbnails.items():
            if perf.video_id:
                tracked_videos.add(perf.video_id)

        if not tracked_videos:
            logger.info("No tracked videos to sync")
            return results

        # Fetch analytics for each video
        for video_id in tracked_videos:
            try:
                stats = self._fetch_video_stats(video_id)

                if stats:
                    self._update_trackers(video_id, stats)
                    results["synced"] += 1
                    results["details"].append(
                        {
                            "video_id": video_id,
                            "status": "synced",
                            "views": stats.get("views", 0),
                        }
                    )
                else:
                    results["skipped"] += 1

            except Exception as e:
                logger.error(f"Failed to sync {video_id}: {e}")
                results["failed"] += 1
                results["details"].append(
                    {"video_id": video_id, "status": "failed", "error": str(e)}
                )

        logger.info(
            f"Sync complete: {results['synced']} synced, {results['failed']} failed"
        )
        return results

    def _fetch_video_stats(self, video_id: str) -> Optional[Dict]:
        """Fetch stats for a single video from YouTube API."""
        try:
            response = (
                self.youtube.videos()
                .list(part="statistics,contentDetails", id=video_id)
                .execute()
            )

            if not response.get("items"):
                return None

            item = response["items"][0]
            stats = item.get("statistics", {})

            return {
                "views": int(stats.get("viewCount", 0)),
                "likes": int(stats.get("likeCount", 0)),
                "comments": int(stats.get("commentCount", 0)),
                "shares": 0,  # Not available via basic API
            }

        except Exception as e:
            logger.error(f"YouTube API error for {video_id}: {e}")
            return None

    def _update_trackers(self, video_id: str, stats: Dict):
        """Update theme and thumbnail trackers with stats."""
        content_id = self.video_mapping.get(video_id)

        if not content_id:
            # Try to find by video_id in trackers
            for cid, perf in self.theme_rotator.usage.items():
                if perf.video_id == video_id:
                    content_id = cid
                    break

        if not content_id:
            for cid, perf in self.thumbnail_tracker.thumbnails.items():
                if perf.video_id == video_id:
                    content_id = cid
                    break

        if content_id:
            # Update theme tracker
            self.theme_rotator.update_performance(
                content_id=content_id,
                views=stats.get("views"),
                likes=stats.get("likes"),
                comments=stats.get("comments"),
                shares=stats.get("shares"),
            )

            # Update thumbnail tracker
            self.thumbnail_tracker.update_performance(
                content_id=content_id,
                views=stats.get("views"),
                # CTR requires impressions from YouTube Analytics API (different API)
            )

            logger.debug(f"Updated stats for {content_id}: {stats}")

    def generate_combined_report(self) -> str:
        """Generate a combined A/B testing report."""
        lines = [
            "=" * 70,
            "THEME AND THUMBNAIL A/B TESTING REPORT",
            "=" * 70,
            f"Generated: {datetime.now().isoformat()}",
            "",
        ]

        # Theme performance
        lines.append(self.theme_rotator.generate_report())
        lines.append("")

        # Thumbnail performance
        lines.append(self.thumbnail_tracker.generate_report())

        # Recommendations
        lines.extend(["", "RECOMMENDATIONS:", "-" * 40])

        # Best theme
        top_themes = self.theme_rotator.get_top_themes(3)
        if top_themes:
            best_theme, best_analysis = top_themes[0]
            lines.append(
                f"  📝 Best performing theme: {best_theme.value} "
                f"({best_analysis.evidence.value}, "
                f"{evidence_age.describe(best_analysis.newest_sample_at, best_analysis.evidence_age_days)})"
            )

        # Best thumbnail style — or the reason there isn't one. The Data API
        # stats fed in above carry no click-through rate at all, so this is
        # normally a refusal, and saying so beats naming a default style.
        # The age of the evidence arrives with the style, never separately.
        rec = self.thumbnail_tracker.get_recommended_style()
        lines.append(
            f"  🖼️  Best thumbnail style: {rec.style.value} "
            f"({rec.confidence:.0%} confidence, {rec.evidence.value}, "
            f"{rec.age_phrase()})"
            if rec.style
            else f"  🖼️  Best thumbnail style: unavailable — {rec.reason}"
        )

        # Underperforming themes to retire
        underperforming = self.theme_rotator.get_underperforming_themes()
        if underperforming:
            lines.append(
                f"  ⚠️  Consider retiring: {', '.join(t.value for t in underperforming)}"
            )

        lines.append("=" * 70)

        return "\n".join(lines)


def sync_youtube_analytics(
    youtube_service=None,
    state_dir: Optional[Path] = None,
    days: int = 30,
) -> Dict:
    """
    Convenience function to sync YouTube analytics with theme tracking.

    Args:
        youtube_service: YouTube Data API service
        state_dir: Directory for state files (default: see ``YouTubeThemeSync``)
        days: Days of analytics to sync

    Returns:
        Sync results
    """
    sync = YouTubeThemeSync(youtube_service=youtube_service, state_dir=state_dir)
    return sync.sync_all(days=days)
