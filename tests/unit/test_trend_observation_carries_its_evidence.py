"""A reported movement carries the evidence it rests on, or is refused.

`_observe_ctr_trend` already refuses to call a period-over-period delta an
improvement (the step is named for what it does and carries no causal wording). What it
still did not do was say what either number was measured ON. A 25% "trend up"
computed from two rates, each resting on a handful of impressions, is
indistinguishable from noise, and the record it appended to
`optimization_history` carried no denominator at all -- so the next cycle read
the direction back as established fact with nothing to weigh it against.

These tests pin three things:

1. The record carries the sample size and the window behind each rate.
2. A movement whose evidence cannot support a direction is refused a direction
   -- explicitly, the way `ctr_unmeasured` already refuses the None case --
   rather than being labelled anyway.
3. `_update_templates` does not report writing a settings file as a learning
   outcome.
"""

import json
from types import SimpleNamespace

import pytest

from seo_manager.analytics import continuous_optimization_daemon as cod
from seo_manager.analytics.continuous_optimization_daemon import (
    ContinuousOptimizationDaemon,
)


def _summary(avg_ctr, impressions, clicks=0, views=40, start=None, end=None):
    """The shape `AnalyticsTracker.get_summary` actually returns."""
    metrics = {
        "total_views": views,
        "total_clicks": clicks,
        "total_impressions": impressions,
        "avg_ctr": avg_ctr,
        "avg_ctr_basis": "clicks_over_impressions" if avg_ctr is not None else None,
    }
    if impressions is None:
        # A hand-built summary that never reported a denominator at all.
        del metrics["total_impressions"]
    return {
        "period": {"start_date": start, "end_date": end},
        "performance_metrics": metrics,
    }


def _daemon(tmp_path, summary, history=None):
    daemon = ContinuousOptimizationDaemon(
        manager=SimpleNamespace(analytics=SimpleNamespace(get_summary=lambda: summary)),
        state_dir=tmp_path / "state",
    )
    if history is not None:
        daemon.state.optimization_history.append({"metrics": history})
    return daemon


#: Comfortably above any plausible floor, so these fixtures are never refused
#: for lack of evidence.
AMPLE = 50_000


class TestTheRecordCarriesItsEvidence:
    def test_the_sample_size_behind_the_current_rate_is_recorded(self, tmp_path):
        result = _daemon(
            tmp_path, _summary(0.05, AMPLE, clicks=2500)
        )._observe_ctr_trend()

        assert result.metrics["current_impressions"] == AMPLE
        assert result.metrics["current_clicks"] == 2500

    def test_the_sample_size_behind_the_previous_rate_is_recorded(self, tmp_path):
        result = _daemon(
            tmp_path,
            _summary(0.05, AMPLE),
            history={"current_avg_ctr": 0.04, "current_impressions": 30_000},
        )._observe_ctr_trend()

        assert result.metrics["previous_impressions"] == 30_000

    def test_the_window_each_rate_covers_is_recorded(self, tmp_path):
        """`get_summary()` is called with no date bounds, so both rates are
        cumulative-to-date pooled averages over every event ever stored -- not
        two disjoint periods. A reader comparing them has to know that."""
        result = _daemon(tmp_path, _summary(0.05, AMPLE))._observe_ctr_trend()

        window = result.metrics["window"]
        assert window["start_date"] is None
        assert window["end_date"] is None
        assert window["kind"] == cod.WINDOW_CUMULATIVE_TO_DATE

    def test_the_history_entry_carries_the_evidence_too(self, tmp_path):
        """The next cycle reads its baseline out of this entry."""
        daemon = _daemon(tmp_path, _summary(0.05, AMPLE, clicks=2500))
        daemon._observe_ctr_trend()

        recorded = daemon.state.optimization_history[-1]["metrics"]
        assert recorded["current_impressions"] == AMPLE
        assert recorded["window"]["kind"] == cod.WINDOW_CUMULATIVE_TO_DATE


class TestADeltaTooThinToCharacterise:
    def test_a_direction_needs_enough_impressions_on_both_sides(self, tmp_path):
        """40 impressions cannot tell a 25% move from a coin flip."""
        result = _daemon(
            tmp_path,
            _summary(0.05, 40),
            history={"current_avg_ctr": 0.04, "current_impressions": 40},
        )._observe_ctr_trend()

        assert cod.CTR_TREND_INDETERMINATE in result.changes_made
        assert not any(c.startswith("ctr_trend_up_") for c in result.changes_made)
        assert not any(c.startswith("ctr_trend_down_") for c in result.changes_made)

    def test_the_refusal_says_what_it_would_have_taken(self, tmp_path):
        result = _daemon(
            tmp_path,
            _summary(0.05, 40),
            history={"current_avg_ctr": 0.04, "current_impressions": 40},
        )._observe_ctr_trend()

        assert result.metrics["evidence_sufficiency"] == cod.EVIDENCE_BELOW_FLOOR
        assert (
            result.metrics["min_impressions_for_direction"]
            == cod.MIN_IMPRESSIONS_FOR_DIRECTION
        )

    def test_the_arithmetic_is_still_reported_even_when_unlabelled(self, tmp_path):
        """The percentage is a fact about two numbers; the direction label is a
        claim about the world. Refusing the second does not hide the first."""
        result = _daemon(
            tmp_path,
            _summary(0.05, 40),
            history={"current_avg_ctr": 0.04, "current_impressions": 40},
        )._observe_ctr_trend()

        assert result.metrics["ctr_change_percent"] == pytest.approx(25.0)

    def test_one_thin_side_is_enough_to_refuse(self, tmp_path):
        result = _daemon(
            tmp_path,
            _summary(0.05, AMPLE),
            history={"current_avg_ctr": 0.04, "current_impressions": 12},
        )._observe_ctr_trend()

        assert cod.CTR_TREND_INDETERMINATE in result.changes_made

    def test_a_thin_delta_is_not_logged_as_a_decline(self, tmp_path, caplog):
        with caplog.at_level("WARNING"):
            _daemon(
                tmp_path,
                _summary(0.01, 40),
                history={"current_avg_ctr": 0.05, "current_impressions": 40},
            )._observe_ctr_trend()

        assert "trend down" not in caplog.text

    def test_ample_evidence_still_gets_its_direction(self, tmp_path):
        result = _daemon(
            tmp_path,
            _summary(0.05, AMPLE),
            history={"current_avg_ctr": 0.04, "current_impressions": AMPLE},
        )._observe_ctr_trend()

        assert any(c.startswith("ctr_trend_up_") for c in result.changes_made)
        assert result.metrics["evidence_sufficiency"] == cod.EVIDENCE_SUFFICIENT


class TestWhatCouldNotBeTold:
    def test_a_missing_denominator_is_said_out_loud(self, tmp_path):
        """A rate whose sample size nobody reported is not a well-evidenced
        rate. It is not a thin one either -- it is unknown, and unknown gets
        its own token rather than being folded into either."""
        result = _daemon(
            tmp_path,
            _summary(0.05, None),
            history={"current_avg_ctr": 0.04, "current_impressions": AMPLE},
        )._observe_ctr_trend()

        assert cod.CTR_SAMPLE_SIZE_UNKNOWN in result.changes_made
        assert result.metrics["evidence_sufficiency"] == cod.EVIDENCE_SAMPLE_UNKNOWN

    def test_a_history_entry_from_before_this_change_is_unknown_not_ample(
        self, tmp_path
    ):
        """Entries written by earlier versions carry no denominator. Assuming
        they were adequate is the fabrication; saying so is not."""
        result = _daemon(
            tmp_path,
            _summary(0.05, AMPLE),
            history={"current_avg_ctr": 0.04},
        )._observe_ctr_trend()

        assert cod.CTR_SAMPLE_SIZE_UNKNOWN in result.changes_made
        assert result.metrics["previous_impressions"] is None

    def test_a_zero_baseline_rate_is_reported_not_silently_dropped(self, tmp_path):
        """A previous rate of a measured 0.0 makes the relative change
        undefined. The old code fell off the end of an `elif last_ctr > 0`
        chain and appended nothing at all -- a cycle that looked exactly like a
        healthy one in the history it wrote."""
        result = _daemon(
            tmp_path,
            _summary(0.04, AMPLE),
            history={"current_avg_ctr": 0.0, "current_impressions": AMPLE},
        )._observe_ctr_trend()

        assert result.changes_made, "a cycle that could not tell must still say so"
        assert cod.CTR_BASELINE_ZERO in result.changes_made
        assert "ctr_change_percent" not in result.metrics


class TestAFileWriteIsNotALearningOutcome:
    def _daemon_for_templates(self, tmp_path):
        return _daemon(tmp_path, _summary(0.05, AMPLE))

    def test_writing_the_config_is_not_itself_a_change(self, tmp_path):
        daemon = self._daemon_for_templates(tmp_path)
        result = daemon._update_templates()

        assert "updated_learned_config" not in result.changes_made, (
            "a settings-file write is an I/O event, not a measured improvement"
        )

    def test_an_unchanged_config_reports_that_it_is_unchanged(self, tmp_path):
        daemon = self._daemon_for_templates(tmp_path)
        daemon._update_templates()
        second = daemon._update_templates()

        assert cod.LEARNED_VALUES_UNCHANGED in second.changes_made
        assert second.metrics["changed_sections"] == []

    def test_a_real_change_in_a_learned_value_is_reported(self, tmp_path):
        daemon = self._daemon_for_templates(tmp_path)
        daemon._update_templates()

        daemon.state.optimal_title_length_max = 65
        result = daemon._update_templates()

        assert cod.LEARNED_VALUES_CHANGED in result.changes_made
        assert "title" in result.metrics["changed_sections"]

    def test_the_regenerated_timestamp_alone_is_not_a_change(self, tmp_path):
        """`generated_at` moves every cycle by construction; comparing it would
        make every cycle look like it learned something."""
        daemon = self._daemon_for_templates(tmp_path)
        daemon._update_templates()
        first = json.loads((tmp_path / "state" / "learned_config.json").read_text())

        second = daemon._update_templates()
        rewritten = json.loads((tmp_path / "state" / "learned_config.json").read_text())

        assert second.metrics["changed_sections"] == []
        assert rewritten["generated_at"] >= first["generated_at"]
