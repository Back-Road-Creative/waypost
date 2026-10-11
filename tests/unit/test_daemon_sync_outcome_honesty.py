"""A skipped sync is not a sync, and a trend is not a measured improvement.

First defect: ``_sync_youtube_analytics`` had three separate ``success=True``
returns a caller could not tell apart -- no collector configured, no videos to
sync, and a batch in which every single video failed. ``build_daemon`` supplied
neither ``youtube_sync`` nor ``video_mapping_file``, so the shipped CLI always
took the first of those and reported the advertised fresh YouTube sync as a
success while collecting nothing.

``build_daemon`` now ACCEPTS both -- an OAuth token path and a mapping file,
neither with a default -- so the remaining gap is the two values themselves
rather than the plumbing, and an unconfigured cycle names which one is missing.
The wiring and the per-cycle run record are covered in
``test_daemon_cycle_run_evidence.py``.

Second defect: ``_validate_improvements`` compared this cycle's pooled rate with the
previous cycle's and called the difference a validated improvement. It is a
trend across time with no control group, no holdout and no matching on content
age, audience, topic, exposure or sample size -- any of which moves the rate on
its own. It also carried an unasked-for product decision: a drop of up to five
percent was reported as ``ctr_stable``.
"""

import inspect
import json
from types import SimpleNamespace

import pytest

from seo_manager.analytics import continuous_optimization_daemon as cod
from seo_manager.analytics.continuous_optimization_daemon import (
    ContinuousOptimizationDaemon,
    OptimizationCycleType,
    SyncOutcome,
)


class _Sync:
    """Stand-in collector: returns the batch it was constructed with."""

    def __init__(self, results):
        self._results = results
        self.calls = 0

    def batch_sync(self, manager, video_mapping):
        self.calls += 1
        return list(self._results)


def _result(video_id, success):
    return {"video_id": video_id, "content_id": video_id, "success": success}


def _daemon(tmp_path, **kwargs):
    return ContinuousOptimizationDaemon(
        manager=SimpleNamespace(), state_dir=tmp_path / "state", **kwargs
    )


def _mapping(tmp_path, payload):
    path = tmp_path / "mapping.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ── the sync says which of five things happened ────────────────────────


class TestSyncOutcome:
    def test_no_collector_is_unconfigured_not_a_fresh_sync(self, tmp_path):
        result = _daemon(tmp_path)._sync_youtube_analytics()

        assert result.sync_outcome is SyncOutcome.UNCONFIGURED
        # The existing marker stays: callers/tests may key on it.
        assert "skipped_no_youtube_sync" in result.changes_made

    def test_an_empty_mapping_file_is_told_apart_from_a_missing_one(self, tmp_path):
        empty = _daemon(
            tmp_path,
            youtube_sync=_Sync([]),
            video_mapping_file=_mapping(tmp_path, {}),
        )._sync_youtube_analytics()

        missing = _daemon(
            tmp_path,
            youtube_sync=_Sync([]),
            video_mapping_file=tmp_path / "gone.json",
        )._sync_youtube_analytics()

        unconfigured_file = _daemon(
            tmp_path, youtube_sync=_Sync([])
        )._sync_youtube_analytics()

        assert empty.sync_outcome is SyncOutcome.EMPTY
        assert missing.sync_outcome is SyncOutcome.EMPTY
        assert unconfigured_file.sync_outcome is SyncOutcome.EMPTY
        # A brand-new account with nothing published yet and a collector whose
        # mapping went away are both "empty", but they are not the same event.
        assert empty.metrics["video_mapping_state"] == cod.MAPPING_FILE_EMPTY
        assert missing.metrics["video_mapping_state"] == cod.MAPPING_FILE_MISSING
        assert (
            unconfigured_file.metrics["video_mapping_state"]
            == cod.MAPPING_NOT_CONFIGURED
        )
        assert "no_videos_to_sync" in empty.changes_made

    def test_every_video_synced_is_fresh(self, tmp_path):
        daemon = _daemon(
            tmp_path,
            youtube_sync=_Sync([_result("a", True), _result("b", True)]),
            video_mapping_file=_mapping(tmp_path, {"a": "c1", "b": "c2"}),
        )

        result = daemon._sync_youtube_analytics()

        assert result.sync_outcome is SyncOutcome.FRESH
        assert result.success is True
        assert "synced_2_videos" in result.changes_made
        assert result.metrics["videos_synced"] == 2

    def test_some_failed_is_partial_not_unqualified_success(self, tmp_path):
        daemon = _daemon(
            tmp_path,
            youtube_sync=_Sync([_result("a", True), _result("b", False)]),
            video_mapping_file=_mapping(tmp_path, {"a": "c1", "b": "c2"}),
        )

        result = daemon._sync_youtube_analytics()

        assert result.sync_outcome is SyncOutcome.PARTIAL
        assert "synced_1_videos" in result.changes_made
        assert "failed_1_videos" in result.changes_made
        assert result.metrics["videos_failed"] == 1
        assert result.metrics["videos_requested"] == 2

    def test_a_batch_where_everything_failed_is_not_a_success(self, tmp_path):
        daemon = _daemon(
            tmp_path,
            youtube_sync=_Sync([_result("a", False), _result("b", False)]),
            video_mapping_file=_mapping(tmp_path, {"a": "c1", "b": "c2"}),
        )

        result = daemon._sync_youtube_analytics()

        assert result.sync_outcome is SyncOutcome.FAILED
        assert result.success is False, "zero videos collected is not a success"

    def test_a_collector_returning_nothing_for_requested_videos_failed(self, tmp_path):
        daemon = _daemon(
            tmp_path,
            youtube_sync=_Sync([]),
            video_mapping_file=_mapping(tmp_path, {"a": "c1"}),
        )

        result = daemon._sync_youtube_analytics()

        assert result.sync_outcome is SyncOutcome.FAILED
        assert result.metrics["videos_missing"] == 1

    def test_a_total_failure_does_not_stamp_a_last_sync_time(self, tmp_path):
        daemon = _daemon(
            tmp_path,
            youtube_sync=_Sync([_result("a", False)]),
            video_mapping_file=_mapping(tmp_path, {"a": "c1"}),
        )

        daemon._sync_youtube_analytics()

        assert daemon.state.last_analytics_sync is None, (
            "a sync that collected nothing must not claim the account is synced"
        )
        assert daemon.state.total_videos_synced == 0

    def test_a_partial_sync_does_stamp_one(self, tmp_path):
        daemon = _daemon(
            tmp_path,
            youtube_sync=_Sync([_result("a", True), _result("b", False)]),
            video_mapping_file=_mapping(tmp_path, {"a": "c1", "b": "c2"}),
        )

        daemon._sync_youtube_analytics()

        assert daemon.state.last_analytics_sync is not None
        assert daemon.state.total_videos_synced == 1

    def test_the_cycle_stops_when_the_sync_collected_nothing(self, tmp_path):
        daemon = _daemon(
            tmp_path,
            youtube_sync=_Sync([_result("a", False)]),
            video_mapping_file=_mapping(tmp_path, {"a": "c1"}),
        )

        results = daemon.run_optimization_cycle()

        assert [r.cycle_type for r in results] == [OptimizationCycleType.ANALYTICS_SYNC]

    def test_build_daemon_still_configures_no_collector_by_default(self):
        """The gap is now a CONFIGURATION gap, and this pins the default.

        This test used to assert that `youtube_sync=` and `video_mapping_file=`
        appeared nowhere in `build_daemon`'s source -- true while there was no
        way to pass either. Both appear now: the builder ACCEPTS an OAuth token
        path and a `video_id -> content_id` mapping file and wires a real
        collector when it has both, so that source assertion would only be
        pinning the absence of the plumbing rather than the absence of the
        values.

        What has not changed is the default. Called the way the shipped CLI
        calls it with neither input supplied -- neither has a default path, on
        purpose -- it still builds no collector and the cycle still reports
        `unconfigured`, now naming which input is missing. The wired-collector
        behaviour is in tests/unit/test_daemon_cycle_run_evidence.py.
        """
        params = inspect.signature(cod.build_daemon).parameters
        assert params["youtube_credentials"].default is None
        assert params["video_mapping_file"].default is None, (
            "a default path here would be this code guessing a value that is "
            "the operator's to supply"
        )

        collector, reason = cod._build_collector(None, None)
        assert collector is None
        assert reason == cod.COLLECTOR_NO_CREDENTIALS

        assert "unconfigured" in inspect.getsource(cod.build_daemon), (
            "the limitation must be stated where the daemon is built"
        )


# ── the fourth step observes a trend, it does not validate anything ────


class TestTrendObservationHonesty:
    def _daemon(self, tmp_path, avg_ctr, history_ctr=None):
        daemon = ContinuousOptimizationDaemon(
            manager=SimpleNamespace(
                analytics=SimpleNamespace(
                    get_summary=lambda: {
                        "performance_metrics": {
                            "avg_ctr": avg_ctr,
                            "total_views": 40,
                        }
                    }
                )
            ),
            state_dir=tmp_path / "state",
        )
        if history_ctr is not None:
            daemon.state.optimization_history.append(
                {"metrics": {"current_avg_ctr": history_ctr}}
            )
        return daemon

    def test_the_step_is_named_an_observation_not_a_validation(self):
        assert OptimizationCycleType.TREND_OBSERVATION.value == "trend_observation"
        assert not hasattr(OptimizationCycleType, "VALIDATION"), (
            "the step validates nothing; the name was the claim"
        )

    def test_the_result_is_labelled_an_unvalidated_trend(self, tmp_path):
        result = self._daemon(tmp_path, 0.05, history_ctr=0.04)._observe_ctr_trend()

        assert result.cycle_type is OptimizationCycleType.TREND_OBSERVATION
        assert result.metrics["observation_kind"] == cod.OBSERVATION_UNVALIDATED_TREND
        assert result.metrics["causal_attribution"] is None

    def test_an_upward_move_is_not_called_an_improvement(self, tmp_path):
        result = self._daemon(tmp_path, 0.05, history_ctr=0.04)._observe_ctr_trend()

        assert not any("improved" in c for c in result.changes_made)
        assert any(c.startswith("ctr_trend_up_") for c in result.changes_made)

    def test_no_acceptable_decline_margin_is_invented(self, tmp_path):
        """A 4% drop used to be reported as `ctr_stable` -- a product decision
        about tolerable degradation that nobody made."""
        result = self._daemon(tmp_path, 0.048, history_ctr=0.05)._observe_ctr_trend()

        assert result.metrics["ctr_change_percent"] == pytest.approx(-4.0)
        assert not any(c == "ctr_stable" for c in result.changes_made)
        assert any(c.startswith("ctr_trend_down_") for c in result.changes_made)

    def test_an_unchanged_rate_is_flat_not_an_improvement(self, tmp_path):
        result = self._daemon(tmp_path, 0.05, history_ctr=0.05)._observe_ctr_trend()

        assert "ctr_trend_flat" in result.changes_made

    def test_the_first_cycle_records_a_baseline_not_a_validation(self, tmp_path):
        result = self._daemon(tmp_path, 0.05)._observe_ctr_trend()

        assert "first_observation_baseline" in result.changes_made
