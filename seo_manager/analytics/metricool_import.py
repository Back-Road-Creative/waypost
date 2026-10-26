"""Metricool CSV analytics import.

Parses Metricool analytics export CSVs (per-post metrics) and creates
PERFORMANCE_UPDATED events in the JSONL event store. Feeds ThemeRotator's
update_performance() for voice style A/B analysis.

Supports FB, IG, TikTok analytics (YouTube handled separately via API).
"""

import csv
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from ..core.events import EventType, SEOEvent
from ..storage.event_store import SEOEventStore

logger = logging.getLogger(__name__)

# Expected Metricool analytics export columns (subset we care about)
REQUIRED_COLUMNS = {"Date", "Impressions", "Reach"}
OPTIONAL_METRIC_COLUMNS = {
    "Engagements",
    "Engagement Rate",
    "Clicks",
    "Likes",
    "Comments",
    "Shares",
    "Saves",
    "Video Views",
}


class MetricoolAnalyticsImporter:
    """Imports Metricool analytics CSV exports into the SEO event store."""

    def __init__(self, event_store: SEOEventStore | None = None):
        self.event_store = event_store

    def import_csv(self, csv_path: str, site_id: str) -> int:
        """Import a Metricool analytics CSV into the event store.

        Args:
            csv_path: Path to Metricool analytics export CSV
            site_id: Site identifier (e.g., "mybrand_instagram")

        Returns:
            Number of events created
        """
        csv_path = Path(csv_path)
        if not csv_path.exists():
            raise FileNotFoundError(f"CSV not found: {csv_path}")

        if self.event_store is None:
            raise ValueError("Event store not configured")

        count = 0

        with open(csv_path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)

            # Validate required columns exist
            if reader.fieldnames is None:
                raise ValueError(f"Empty CSV: {csv_path}")

            headers = set(reader.fieldnames)
            missing = REQUIRED_COLUMNS - headers
            if missing:
                raise ValueError(f"Missing required columns in {csv_path}: {missing}")

            for row_num, row in enumerate(reader, start=2):
                try:
                    event = self._row_to_event(row, site_id, row_num)
                    if event:
                        self.event_store.append_event(event)
                        count += 1
                except Exception as e:
                    logger.warning(f"Skipping row {row_num}: {e}")

        logger.info(
            f"Imported {count} analytics events from {csv_path.name} for {site_id}"
        )
        return count

    def _row_to_event(self, row: dict, site_id: str, row_num: int) -> SEOEvent | None:
        """Convert a CSV row to a PERFORMANCE_UPDATED event."""
        date_str = row.get("Date", "").strip()
        if not date_str:
            return None

        impressions = _safe_int(row.get("Impressions", ""))
        reach = _safe_int(row.get("Reach", ""))

        # Skip rows with no meaningful data (nothing reported, or reported 0)
        if not impressions and not reach:
            return None

        data = {
            "impressions": impressions,
            "reach": reach,
            "engagements": _safe_int(row.get("Engagements", "")),
            "engagement_rate": _safe_float(row.get("Engagement Rate", "")),
            "clicks": _safe_int(row.get("Clicks", "")),
            "likes": _safe_int(row.get("Likes", "")),
            "comments": _safe_int(row.get("Comments", "")),
            "shares": _safe_int(row.get("Shares", "")),
            "saves": _safe_int(row.get("Saves", "")),
            "video_views": _safe_int(row.get("Video Views", "")),
            "source": "metricool_csv",
            "source_date": date_str,
        }

        # Use date + row number as content_id if no post ID available
        content_id = (
            row.get("Post ID", "").strip() or f"metricool_{date_str}_r{row_num}"
        )

        return SEOEvent(
            event_id=str(uuid.uuid4()),
            event_type=EventType.PERFORMANCE_UPDATED,
            site_id=site_id,
            content_id=content_id,
            timestamp=datetime.now(UTC).isoformat(),
            data=data,
        )


def _safe_int(val: str) -> int | None:
    """Parse an integer from a potentially messy CSV value.

    A blank or unparseable cell is None (not reported), never 0: a reported
    "0" is a measurement, an empty cell is not."""
    if not val or not val.strip():
        return None
    try:
        # Handle commas in numbers (e.g., "1,234")
        return int(val.replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _safe_float(val: str) -> float | None:
    """Parse a float from a potentially messy CSV value (None when unreported)."""
    if not val or not val.strip():
        return None
    try:
        # Handle percentage strings (e.g., "2.5%")
        clean = val.replace(",", "").replace("%", "").strip()
        return float(clean)
    except (ValueError, TypeError):
        return None
