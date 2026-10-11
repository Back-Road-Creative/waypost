"""The top-performers list is a slice, and ``get_summary`` says of what.

``_get_top_performers`` sorts every content item with performance data and
hard-slices ``[:10]``. A reader of ``get_summary`` sees only the ten rows --
nothing said whether that was ten of eleven or ten of four hundred, so an
absent video looked the same either way. This pins the two numbers that make
the slice legible: how many distinct content items had performance data at
all, and what cap was actually applied to reach the ten shown.
"""

import shutil
import tempfile
from pathlib import Path

import pytest

from seo_manager.analytics.tracker import TOP_PERFORMERS_CAP, AnalyticsTracker
from seo_manager.storage.event_store import SEOEventStore


@pytest.fixture
def tracker():
    temp_dir = tempfile.mkdtemp()
    yield AnalyticsTracker(
        store=SEOEventStore(storage_path=Path(temp_dir)), site_id="test_site"
    )
    shutil.rmtree(temp_dir)


class TestTopPerformersPopulation:
    def test_population_counts_distinct_content_not_events(self, tracker):
        tracker.track_performance("c0", views=10)
        tracker.track_performance("c0", views=5)
        tracker.track_performance("c1", views=1)

        performance = tracker.get_summary()["performance_metrics"]

        assert performance["total_content_with_performance"] == 2

    def test_population_exceeds_the_cap(self, tracker):
        for i in range(12):
            tracker.track_performance(f"c{i}", views=i)

        summary = tracker.get_summary()
        performance = summary["performance_metrics"]

        assert performance["total_content_with_performance"] == 12
        assert performance["top_performers_cap"] == TOP_PERFORMERS_CAP
        assert len(summary["top_performers"]) == TOP_PERFORMERS_CAP

    def test_no_performance_data_is_zero_not_absent(self, tracker):
        performance = tracker.get_summary()["performance_metrics"]

        assert performance["total_content_with_performance"] == 0
        assert performance["top_performers_cap"] == TOP_PERFORMERS_CAP
