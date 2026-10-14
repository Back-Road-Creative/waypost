"""The scheduled (cron) entry point of the optimization daemon.

The learning loop had no caller anywhere before that script, so these are the
first tests that a cycle can run once without widening a gate left closed.
"""

import json
import logging
from types import SimpleNamespace

import pytest

from seo_manager.analytics import continuous_optimization_daemon as cod


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


def test_dry_run_is_the_default(monkeypatch, tmp_path):
    seen = {}
    _patch_cycle(monkeypatch, seen=seen)
    assert cod.main(["--state-dir", str(tmp_path)]) == 0
    assert seen["dry_run"] is True


def test_apply_only_turns_off_the_dry_run(monkeypatch, tmp_path):
    """`--apply` persists the cycle's own state and stops: there is no applier
    step after it, which used to exit 1 here for want of a learned config."""
    seen = {}
    _patch_cycle(monkeypatch, seen=seen)
    assert cod.main(["--state-dir", str(tmp_path / "s"), "--apply"]) == 0
    assert seen["dry_run"] is False


def test_config_dir_is_no_longer_accepted(monkeypatch, tmp_path):
    _patch_cycle(monkeypatch)
    with pytest.raises(SystemExit):
        cod.main(["--state-dir", str(tmp_path), "--config-dir", str(tmp_path)])


def test_a_failed_step_exits_non_zero(monkeypatch, tmp_path):
    _patch_cycle(monkeypatch, success=False)
    assert cod.main(["--state-dir", str(tmp_path)]) == 1


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
