"""The scheduled (cron) entry point of the optimization daemon.

The learning loop had no caller anywhere before that script, so these are the
first tests that a cycle can run once without widening a gate left closed.
"""

import json
import logging
from types import SimpleNamespace

import pytest

from seo_manager.analytics import continuous_optimization_daemon as cod
from seo_manager.analytics.recommendation_auto_applier import (
    AutoApplierConfig,
    RecommendationAutoApplier,
)

LEARNED = {
    "title": {"optimal_length_min": 55, "optimal_length_max": 65},
    "tags": {"recommended": ["a"]},
    "thumbnails": {"style_performance": {"bold": 1.0}},
    "posting": {"best_times": ["09:00"]},
}


def _applier(tmp_path, **flags):
    (tmp_path / "config").mkdir(exist_ok=True)
    return RecommendationAutoApplier(
        config_dir=tmp_path / "config",
        state_dir=tmp_path / "state",
        config=AutoApplierConfig(**flags),
    )


def _patch_cycle(monkeypatch, success=True, seen=None):
    step = SimpleNamespace(
        success=success,
        error="quota",
        cycle_type=cod.OptimizationCycleType.TREND_OBSERVATION,
    )

    def build(**kw):
        (seen if seen is not None else {}).update(kw)
        return SimpleNamespace(run_optimization_cycle=lambda: [step])

    monkeypatch.setattr(cod, "build_daemon", build)


def test_posting_is_dropped_because_its_flag_is_off_by_default(tmp_path):
    applier = _applier(tmp_path)
    assert applier.config.allow_posting_time is False
    applier.apply_learned_config(LEARNED)
    assert applier._get_config_value("posting") is None


def test_a_flag_turned_off_drops_its_section(tmp_path):
    off = _applier(tmp_path, allow_tag_changes=False)
    applied = {rec.target_config for rec in off.apply_learned_config(LEARNED)}
    assert "seo.tags.recommended" not in applied
    assert "seo.title.optimal_length" in applied


def test_dry_run_is_the_default_and_applies_nothing(monkeypatch, tmp_path):
    seen = {}
    _patch_cycle(monkeypatch, seen=seen)
    monkeypatch.setattr(
        cod, "_apply_learned", lambda *a: pytest.fail("dry run applied")
    )
    assert cod.main(["--state-dir", str(tmp_path)]) == 0
    assert seen["dry_run"] is True


def test_a_failed_step_exits_non_zero(monkeypatch, tmp_path):
    _patch_cycle(monkeypatch, success=False)
    assert cod.main(["--state-dir", str(tmp_path)]) == 1


def test_apply_without_a_learned_config_is_an_error(tmp_path):
    assert cod._apply_learned(tmp_path, tmp_path / "config") == 1


def test_apply_writes_only_the_gated_sections(tmp_path):
    (tmp_path / "learned_config.json").write_text(json.dumps(LEARNED))
    assert cod._apply_learned(tmp_path, tmp_path / "config") == 0
    written = json.loads((tmp_path / "config" / "learned_config.json").read_text())
    assert written["seo"]["title"]["optimal_length"] == {"min": 55, "max": 65}
    assert "posting" not in written


def test_the_nightly_path_still_refuses_a_gated_section(tmp_path, monkeypatch):
    """End-to-end guard: the CLI hands the whole config over untouched now, so
    only the applier's own gate stands between a closed flag and a write."""
    (tmp_path / "learned_config.json").write_text(json.dumps(LEARNED))
    real = cod.RecommendationAutoApplier
    monkeypatch.setattr(
        cod,
        "RecommendationAutoApplier",
        lambda **kw: real(config=AutoApplierConfig(allow_tag_changes=False), **kw),
    )

    assert cod._apply_learned(tmp_path, tmp_path / "config") == 0

    written = json.loads((tmp_path / "config" / "learned_config.json").read_text())
    assert "tags" not in written["seo"]
    assert written["seo"]["title"]["optimal_length"] == {"min": 55, "max": 65}


# ── DaemonState schema drift ────────────────────────────────────────────────
# _load_state does DaemonState(**data). An unknown key in daemon_state.json --
# a field this release removed, or one a newer release wrote -- raises
# TypeError, which the except swallows into a fresh DaemonState(). Every
# learned value the daemon had is then silently gone, with only a warning in
# the log to say so. One stale key must cost that key, not the whole state.


def _state_file(tmp_path, **extra):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "total_videos_synced": 41,
        "optimal_title_length_min": 55,
        "top_performing_tags": ["ireland", "roadtrip"],
        **extra,
    }
    (state_dir / "daemon_state.json").write_text(json.dumps(payload), encoding="utf-8")
    return state_dir


def _daemon(state_dir):
    return cod.ContinuousOptimizationDaemon(
        manager=SimpleNamespace(), state_dir=state_dir
    )


def test_an_unknown_state_key_does_not_erase_everything_else(tmp_path):
    state_dir = _state_file(tmp_path, a_field_this_release_removed=["09:00"])
    state = _daemon(state_dir).state
    assert state.total_videos_synced == 41, "a stale key wiped the whole state"
    assert state.optimal_title_length_min == 55
    assert state.top_performing_tags == ["ireland", "roadtrip"]


def test_an_unknown_state_key_is_named_in_the_log(tmp_path, caplog):
    state_dir = _state_file(tmp_path, a_field_this_release_removed=["09:00"])
    with caplog.at_level(logging.WARNING):
        _daemon(state_dir)
    assert "a_field_this_release_removed" in caplog.text, (
        "dropping a key silently is how schema drift goes unnoticed"
    )


def test_a_clean_state_file_still_round_trips(tmp_path):
    state = _daemon(_state_file(tmp_path)).state
    assert state.total_videos_synced == 41 and state.top_performing_tags == [
        "ireland",
        "roadtrip",
    ]


def test_site_id_defaults_to_a_neutral_value(monkeypatch, tmp_path):
    seen = {}
    _patch_cycle(monkeypatch, seen=seen)
    assert cod.main(["--state-dir", str(tmp_path)]) == 0
    assert seen["site_id"] == "default"


def test_site_id_option_overrides_the_default(monkeypatch, tmp_path):
    seen = {}
    _patch_cycle(monkeypatch, seen=seen)
    assert cod.main(["--state-dir", str(tmp_path), "--site-id", "mysite"]) == 0
    assert seen["site_id"] == "mysite"
