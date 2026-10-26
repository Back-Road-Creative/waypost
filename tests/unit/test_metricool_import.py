"""Tests for Metricool CSV analytics import."""

import csv
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from seo_manager.analytics.metricool_import import (
    MetricoolAnalyticsImporter,
    _safe_float,
    _safe_int,
)
from seo_manager.core.events import EventType


@pytest.fixture
def sample_csv(tmp_path):
    """Create a sample Metricool analytics CSV."""
    csv_path = tmp_path / "metricool_analytics.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "Date",
                "Impressions",
                "Reach",
                "Engagements",
                "Engagement Rate",
                "Clicks",
                "Likes",
                "Comments",
                "Shares",
                "Saves",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "Date": "2026-02-10",
                "Impressions": "1,234",
                "Reach": "987",
                "Engagements": "56",
                "Engagement Rate": "4.5%",
                "Clicks": "23",
                "Likes": "45",
                "Comments": "8",
                "Shares": "3",
                "Saves": "12",
            }
        )
        writer.writerow(
            {
                "Date": "2026-02-11",
                "Impressions": "2,345",
                "Reach": "1,876",
                "Engagements": "89",
                "Engagement Rate": "3.8%",
                "Clicks": "34",
                "Likes": "67",
                "Comments": "15",
                "Shares": "7",
                "Saves": "20",
            }
        )
    return csv_path


@pytest.fixture
def mock_event_store():
    store = MagicMock()
    store.append_event = MagicMock(return_value="test-event-id")
    return store


class TestMetricoolImporter:
    def test_imports_csv(self, sample_csv, mock_event_store):
        importer = MetricoolAnalyticsImporter(event_store=mock_event_store)
        count = importer.import_csv(str(sample_csv), "examplebrand_instagram")
        assert count == 2
        assert mock_event_store.append_event.call_count == 2

    def test_creates_performance_events(self, sample_csv, mock_event_store):
        importer = MetricoolAnalyticsImporter(event_store=mock_event_store)
        importer.import_csv(str(sample_csv), "examplebrand_instagram")

        event = mock_event_store.append_event.call_args_list[0][0][0]
        assert event.event_type == EventType.PERFORMANCE_UPDATED
        assert event.site_id == "examplebrand_instagram"
        assert event.data["impressions"] == 1234
        assert event.data["reach"] == 987
        assert event.data["likes"] == 45

    def test_raises_on_missing_file(self, mock_event_store):
        importer = MetricoolAnalyticsImporter(event_store=mock_event_store)
        with pytest.raises(FileNotFoundError):
            importer.import_csv("/nonexistent.csv", "test")

    def test_raises_on_missing_columns(self, tmp_path, mock_event_store):
        csv_path = tmp_path / "bad.csv"
        with open(csv_path, "w") as f:
            f.write("Name,Value\nfoo,bar\n")

        importer = MetricoolAnalyticsImporter(event_store=mock_event_store)
        with pytest.raises(ValueError, match="Missing required columns"):
            importer.import_csv(str(csv_path), "test")

    def test_skips_empty_rows(self, tmp_path, mock_event_store):
        csv_path = tmp_path / "sparse.csv"
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["Date", "Impressions", "Reach"])
            writer.writeheader()
            writer.writerow({"Date": "2026-02-10", "Impressions": "0", "Reach": "0"})
            writer.writerow({"Date": "2026-02-11", "Impressions": "100", "Reach": "50"})

        importer = MetricoolAnalyticsImporter(event_store=mock_event_store)
        count = importer.import_csv(str(csv_path), "test")
        assert count == 1  # Only non-zero row imported

    def test_blank_metric_cells_import_as_unknown(self, tmp_path, mock_event_store):
        csv_path = tmp_path / "blank.csv"
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=["Date", "Impressions", "Reach", "Clicks", "Likes"]
            )
            writer.writeheader()
            writer.writerow(
                {
                    "Date": "2026-02-10",
                    "Impressions": "100",
                    "Reach": "50",
                    "Clicks": "",
                    "Likes": "0",
                }
            )

        importer = MetricoolAnalyticsImporter(event_store=mock_event_store)
        importer.import_csv(str(csv_path), "test")

        data = mock_event_store.append_event.call_args_list[0][0][0].data
        assert data["clicks"] is None
        assert data["likes"] == 0
        assert data["saves"] is None

    def test_raises_without_event_store(self, sample_csv):
        importer = MetricoolAnalyticsImporter()
        with pytest.raises(ValueError, match="Event store not configured"):
            importer.import_csv(str(sample_csv), "test")


class TestSafeInt:
    def test_normal_int(self):
        assert _safe_int("42") == 42

    def test_commas(self):
        assert _safe_int("1,234") == 1234

    def test_empty_is_unreported_not_zero(self):
        assert _safe_int("") is None
        assert _safe_int("  ") is None

    def test_invalid_is_unreported_not_zero(self):
        assert _safe_int("abc") is None

    def test_reported_zero_is_zero(self):
        assert _safe_int("0") == 0


class TestSafeFloat:
    def test_normal_float(self):
        assert _safe_float("3.14") == pytest.approx(3.14)

    def test_percentage(self):
        assert _safe_float("4.5%") == pytest.approx(4.5)

    def test_empty_is_unreported_not_zero(self):
        assert _safe_float("") is None

    def test_invalid_is_unreported_not_zero(self):
        assert _safe_float("abc") is None

    def test_reported_zero_is_zero(self):
        assert _safe_float("0%") == 0.0
