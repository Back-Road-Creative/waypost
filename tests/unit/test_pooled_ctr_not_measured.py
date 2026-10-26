"""A site-wide rate nobody measured is unknown, not zero.

``get_content_performance`` already refuses to report an unmeasured per-video
click rate as 0. The POOLED rate in ``get_summary`` did not: with no
impressions in the period it published ``avg_ctr: 0``, and the optimization
daemon read that back as a real measurement and logged a "CTR declined by
100.0%" warning about a period nothing was measured in.

The same fold covers the generation averages: their numerator skipped an
unscored event as ``0`` while their denominator counted it, so an item nobody
scored pulled the mean down exactly as a genuine 0.00 would.
"""

import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from seo_manager.analytics import continuous_optimization_daemon as cod
from seo_manager.analytics.tracker import CTR_BASIS_RECOMPUTED, AnalyticsTracker
from seo_manager.core.events import EventType, SEOEvent
from seo_manager.storage.event_store import SEOEventStore


@pytest.fixture
def tracker():
    temp_dir = tempfile.mkdtemp()
    yield AnalyticsTracker(
        store=SEOEventStore(storage_path=Path(temp_dir)), site_id="test_site"
    )
    shutil.rmtree(temp_dir)


def _generation_event(tracker, content_id, **data):
    tracker.store.append_event(
        SEOEvent(
            event_id=content_id,
            event_type=EventType.METADATA_GENERATED,
            site_id=tracker.site_id,
            content_id=content_id,
            timestamp="2026-01-15T10:00:00+00:00",
            data=data,
        )
    )


class TestPooledCTR:
    def test_no_impressions_is_unknown_not_zero(self, tracker):
        tracker.track_performance("content_0", views=100, clicks=None)

        performance = tracker.get_summary()["performance_metrics"]

        assert performance["avg_ctr"] is None
        assert performance["avg_ctr_basis"] is None
        # Totals sum what was reported; a total nobody reported is unknown,
        # not 0. Only the rate is refused.
        assert performance["total_views"] == 100
        assert performance["total_impressions"] is None

    def test_measured_rate_is_still_reported_with_its_basis(self, tracker):
        tracker.track_performance("c0", views=1000, clicks=50, impressions=2500)

        performance = tracker.get_summary()["performance_metrics"]

        assert performance["avg_ctr"] == pytest.approx(0.02)
        assert performance["avg_ctr_basis"] == CTR_BASIS_RECOMPUTED

    def test_a_measured_zero_is_kept(self, tracker):
        """Nobody clicked IS a measurement, and stays a 0."""
        tracker.track_performance("c0", views=10, clicks=0, impressions=500)

        assert tracker.get_summary()["performance_metrics"]["avg_ctr"] == 0


class TestGenerationAverages:
    def test_unscored_event_does_not_drag_the_mean(self, tracker):
        _generation_event(tracker, "scored", quality_score=0.8, tag_count=10)
        _generation_event(tracker, "unscored")

        content = tracker.get_summary()["content_metrics"]

        assert content["total_content_generated"] == 2
        assert content["avg_quality_score"] == pytest.approx(0.8)
        assert content["avg_tag_count"] == pytest.approx(10)

    def test_nothing_reported_is_unknown_not_zero(self, tracker):
        _generation_event(tracker, "unscored")

        content = tracker.get_summary()["content_metrics"]

        assert content["avg_quality_score"] is None
        assert content["avg_title_length"] is None
        assert content["avg_tag_count"] is None


class TestDaemonTrendObservation:
    def _daemon(self, tmp_path, summary, history_ctr):
        daemon = cod.ContinuousOptimizationDaemon(
            manager=SimpleNamespace(
                analytics=SimpleNamespace(get_summary=lambda: summary)
            ),
            state_dir=tmp_path,
        )
        daemon.state.optimization_history.append(
            {"metrics": {"current_avg_ctr": history_ctr}}
        )
        return daemon

    def test_unmeasured_period_is_not_a_decline(self, tmp_path, caplog):
        daemon = self._daemon(
            tmp_path,
            {"performance_metrics": {"avg_ctr": None, "total_views": 40}},
            history_ctr=0.05,
        )

        with caplog.at_level("WARNING"):
            result = daemon._observe_ctr_trend()

        assert "trend down" not in caplog.text
        assert not any("trend_down" in c for c in result.changes_made)
        assert "ctr_change_percent" not in result.metrics
        assert result.metrics["current_avg_ctr"] is None

    def test_an_unmeasured_previous_period_is_not_a_baseline(self, tmp_path):
        daemon = self._daemon(
            tmp_path,
            {"performance_metrics": {"avg_ctr": 0.04, "total_views": 40}},
            history_ctr=None,
        )

        result = daemon._observe_ctr_trend()

        assert "ctr_change_percent" not in result.metrics

    def test_a_real_drop_is_still_warned_about(self, tmp_path, caplog):
        """The step no longer calls this a validated decline, but a
        measured drop is still surfaced at WARNING with its magnitude."""
        daemon = self._daemon(
            tmp_path,
            {"performance_metrics": {"avg_ctr": 0.01, "total_views": 40}},
            history_ctr=0.05,
        )

        with caplog.at_level("WARNING"):
            result = daemon._observe_ctr_trend()

        assert "CTR trend down 80.0%" in caplog.text
        assert result.metrics["ctr_change_percent"] == pytest.approx(-80.0)
