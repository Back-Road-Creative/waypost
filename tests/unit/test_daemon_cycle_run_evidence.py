"""A cycle that ran leaves evidence, and a cycle that never ran says so.

The remaining gap: no scheduler is proven active. ``build_daemon`` supplied
no ``youtube_sync``/``video_mapping_file`` by default, and no cron/marker
evidence existed.

Two failures, tested here as two halves.

**Half A.** Nothing on disk recorded that a cycle happened, so a cron that
never fired and a cron that fired nightly and found nothing to do were
indistinguishable from outside the process -- silence reported as a
measurement, which is the defect this whole campaign keeps finding. The run
record fixes that, and the rules it must obey are the rules the rest of this
service already obeys: "never ran" is its own state and is never a zero or a
fresh timestamp, a failed cycle is recorded rather than skipped, a dry run is
told apart from an applying one, an unknown field reads back unknown, and the
age of the last run is *reported* while how old is too old stays a product
decision nobody here is entitled to make.

**Half B.** ``build_daemon`` could not wire a collector because two values --
an OAuth token path and a ``video_id -> content_id`` mapping file -- had
nowhere to enter. They enter as explicit parameters now (and as CLI flags with
no default, because this service's convention for a credential path is an
explicit argument -- ``YouTubeAnalyticsSync.__init__``,
``YouTubeUploader.__init__``, ``BatchUploader.__init__`` all take one -- and
inventing an env var or a default path here would be inventing the operator's answer).
With either absent the cycle still reports ``unconfigured``, but it now names
WHICH input was missing instead of reporting a bare one.
"""

import importlib
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

from seo_manager.analytics import continuous_optimization_daemon as cod
from seo_manager.analytics.continuous_optimization_daemon import (
    ContinuousOptimizationDaemon,
    SyncOutcome,
)


def _manager(avg_ctr=0.04):
    """A manager that lets a whole cycle run without touching a real store."""
    return SimpleNamespace(
        optimizer=SimpleNamespace(
            analyze_patterns=lambda min_views=0: {"tags": []},
            get_optimal_title_length=lambda: (50, 70),
        ),
        analytics=SimpleNamespace(
            get_summary=lambda: {
                "performance_metrics": {
                    "avg_ctr": avg_ctr,
                    "total_impressions": 5000,
                    "total_clicks": 200,
                    "total_views": 100,
                }
            }
        ),
    )


def _daemon(tmp_path, **kwargs):
    kwargs.setdefault("manager", _manager())
    return ContinuousOptimizationDaemon(state_dir=tmp_path / "state", **kwargs)


def _record_path(tmp_path):
    return tmp_path / "state" / cod.RUN_RECORD_FILENAME


# ── Half A: a cycle leaves evidence it ran ──────────────────────────────────


class TestNeverRanIsItsOwnState:
    def test_no_record_reads_back_as_never_recorded(self, tmp_path):
        """Not a zero, not an empty result, not a fresh timestamp."""
        assert cod.read_last_run(tmp_path / "state") is None

    def test_status_says_never_recorded_rather_than_showing_a_blank(self, tmp_path):
        status = _daemon(tmp_path).get_status()

        assert status["last_cycle_run_state"] == cod.RUN_NEVER_RECORDED
        assert status["last_cycle_run"] is None

    def test_an_unreadable_record_is_not_a_missing_one(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True)
        (state_dir / cod.RUN_RECORD_FILENAME).write_text("{not json", encoding="utf-8")

        record = cod.read_last_run(state_dir)

        assert record is not None, "a corrupt record is not 'no cycle ever ran'"
        assert record.completion == cod.RUN_UNREADABLE
        assert record.started_at is None and record.dry_run is None
        assert _daemon(tmp_path).get_status()["last_cycle_run_state"] == (
            cod.RUN_UNREADABLE
        )


class TestACycleIsRecorded:
    def test_a_completed_cycle_writes_when_it_ran_and_how_it_finished(self, tmp_path):
        before = datetime.now()
        _daemon(tmp_path).run_optimization_cycle()

        record = cod.read_last_run(tmp_path / "state")

        assert record.completion == cod.RUN_COMPLETED
        assert record.failed_steps == ()
        assert datetime.fromisoformat(record.started_at) >= before
        assert datetime.fromisoformat(record.finished_at) >= datetime.fromisoformat(
            record.started_at
        )

    def test_the_record_carries_the_sync_outcome(self, tmp_path):
        _daemon(tmp_path).run_optimization_cycle()

        assert cod.read_last_run(tmp_path / "state").sync_outcome == (
            SyncOutcome.UNCONFIGURED.value
        )

    def test_a_dry_run_is_distinguishable_from_an_applying_run(self, tmp_path):
        _daemon(tmp_path, dry_run=True).run_optimization_cycle()
        assert cod.read_last_run(tmp_path / "state").dry_run is True

        _daemon(tmp_path, dry_run=False).run_optimization_cycle()
        assert cod.read_last_run(tmp_path / "state").dry_run is False

    def test_a_dry_run_still_leaves_evidence_at_all(self, tmp_path):
        """The shipped CLI is dry unless --apply. If a dry cycle left nothing,
        the default nightly schedule would still be unprovable."""
        _daemon(tmp_path, dry_run=True).run_optimization_cycle()

        assert _record_path(tmp_path).exists()

    def test_a_failed_step_is_recorded_as_a_failure_not_a_completion(self, tmp_path):
        daemon = _daemon(tmp_path)
        daemon.manager.analytics = SimpleNamespace(
            get_summary=lambda: (_ for _ in ()).throw(RuntimeError("quota"))
        )

        daemon.run_optimization_cycle()
        record = cod.read_last_run(tmp_path / "state")

        assert record.completion == cod.RUN_STEPS_FAILED
        assert "trend_observation" in record.failed_steps

    def test_a_cycle_that_raised_still_leaves_evidence_it_was_attempted(self, tmp_path):
        daemon = _daemon(tmp_path)

        def boom():
            raise RuntimeError("disk full")

        daemon._save_state = boom

        with pytest.raises(RuntimeError):
            daemon.run_optimization_cycle()

        record = cod.read_last_run(tmp_path / "state")
        assert record.completion == cod.RUN_RAISED
        assert "disk full" in record.error
        assert record.started_at is not None

    def test_a_raise_before_the_sync_leaves_the_outcome_unknown_not_invented(
        self, tmp_path
    ):
        daemon = _daemon(tmp_path)

        def boom():
            raise RuntimeError("no store")

        daemon._sync_youtube_analytics = boom

        with pytest.raises(RuntimeError):
            daemon.run_optimization_cycle()

        record = cod.read_last_run(tmp_path / "state")
        assert record.sync_outcome is None, "an outcome nobody observed is unknown"
        assert record.completion == cod.RUN_RAISED
        assert record.finished_at is None, "a cycle that raised did not finish"


class TestStalenessIsReportedNeverJudged:
    def test_the_age_of_the_last_run_is_available(self, tmp_path):
        _daemon(tmp_path).run_optimization_cycle()

        record = cod.read_last_run(tmp_path / "state")
        assert record.age_days is not None and record.age_days < 1

    def test_an_undated_record_has_an_unknown_age_not_a_zero(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True)
        (state_dir / cod.RUN_RECORD_FILENAME).write_text(
            json.dumps({"completion": cod.RUN_COMPLETED}), encoding="utf-8"
        )

        assert cod.read_last_run(state_dir).age_days is None

    def test_the_stored_record_carries_no_age(self, tmp_path):
        """An age written to disk is wrong the moment after it is written."""
        _daemon(tmp_path).run_optimization_cycle()

        assert "age_days" not in json.loads(_record_path(tmp_path).read_text())

    def test_no_freshness_threshold_or_verdict_is_invented(self, tmp_path):
        judged = [
            name
            for name in dir(cod)
            if any(
                word in name.upper()
                for word in ("STALE", "FRESH", "MAX_AGE", "UNHEALTHY", "OVERDUE")
            )
        ]
        assert judged == [], (
            f"the daemon invented a freshness policy: {judged}. How old is too "
            "old is a product decision, not a constant an agent gets to pick."
        )

        _daemon(tmp_path).run_optimization_cycle()
        status = _daemon(tmp_path).get_status()["last_cycle_run"]
        assert "age_days" in status
        assert not [k for k in status if "stale" in k or "healthy" in k]


# ── Half B: the missing sync is a configuration gap, not a code gap ─────────


@pytest.fixture
def _stub_manager_class(monkeypatch):
    """`build_daemon` constructs a real SEOManager; stand one in."""
    from seo_manager.core import manager as manager_module

    monkeypatch.setattr(
        manager_module, "SEOManager", lambda **kwargs: _manager(), raising=True
    )


class _StubSync:
    instances: ClassVar[list] = []

    def __init__(self, credentials_path):
        self.credentials_path = credentials_path
        _StubSync.instances.append(self)

    def batch_sync(self, manager, video_mapping):  # pragma: no cover - unused
        return []


def _patch_collector(monkeypatch, replacement):
    """Replace YouTubeAnalyticsSync on the module ``build_daemon`` will load.

    Deliberately ``importlib.import_module`` and not
    ``from seo_manager.analytics import youtube_sync``.
    ``tests/test_youtube_sync_scopes.py`` deletes
    ``sys.modules["seo_manager.analytics.youtube_sync"]`` to re-import the
    module against mocked google libraries, and leaves the *package attribute*
    pointing at that throwaway module with no ``sys.modules`` entry behind it.
    A ``from ... import`` finds the attribute, skips the import, and hands back
    the orphan -- so the patch lands on a module nothing will ever load again,
    and the real collector runs. ``import_module`` goes through ``sys.modules``,
    re-importing and re-binding the attribute when it is missing, which is the
    same path ``build_daemon`` takes.
    """
    module = importlib.import_module("seo_manager.analytics.youtube_sync")
    monkeypatch.setattr(module, "YouTubeAnalyticsSync", replacement, raising=True)


@pytest.fixture
def _stub_collector(monkeypatch):
    _StubSync.instances = []
    _patch_collector(monkeypatch, _StubSync)
    return _StubSync


class TestBuildDaemonConfiguration:
    def test_both_inputs_supplied_wires_a_real_collector_and_mapping(
        self, tmp_path, _stub_manager_class, _stub_collector
    ):
        token = tmp_path / "token.json"
        token.write_text("{}", encoding="utf-8")
        mapping = tmp_path / "mapping.json"
        mapping.write_text(json.dumps({"vid": "content"}), encoding="utf-8")

        daemon = cod.build_daemon(
            state_dir=tmp_path / "state",
            dry_run=True,
            youtube_credentials=token,
            video_mapping_file=mapping,
        )

        assert isinstance(daemon.youtube_sync, _StubSync)
        assert daemon.video_mapping_file == mapping
        assert daemon._load_video_mapping() == {"vid": "content"}

    def test_no_credentials_names_the_credentials_not_a_bare_unconfigured(
        self, tmp_path, _stub_manager_class
    ):
        mapping = tmp_path / "mapping.json"
        mapping.write_text(json.dumps({"vid": "content"}), encoding="utf-8")

        daemon = cod.build_daemon(
            state_dir=tmp_path / "state", dry_run=True, video_mapping_file=mapping
        )
        result = daemon._sync_youtube_analytics()

        assert daemon.youtube_sync is None
        assert result.sync_outcome is SyncOutcome.UNCONFIGURED
        assert cod.COLLECTOR_NO_CREDENTIALS in result.changes_made
        assert cod.COLLECTOR_NO_CREDENTIALS in result.metrics["unconfigured_reasons"]

    def test_no_mapping_names_the_mapping(
        self, tmp_path, _stub_manager_class, _stub_collector
    ):
        token = tmp_path / "token.json"
        token.write_text("{}", encoding="utf-8")

        daemon = cod.build_daemon(
            state_dir=tmp_path / "state", dry_run=True, youtube_credentials=token
        )
        result = daemon._sync_youtube_analytics()

        assert daemon.youtube_sync is None, "half a collector is not a collector"
        assert result.sync_outcome is SyncOutcome.UNCONFIGURED
        assert cod.MAPPING_NOT_CONFIGURED in result.metrics["unconfigured_reasons"]

    def test_neither_input_names_both(self, tmp_path, _stub_manager_class):
        daemon = cod.build_daemon(state_dir=tmp_path / "state", dry_run=True)
        reasons = daemon._sync_youtube_analytics().metrics["unconfigured_reasons"]

        assert cod.COLLECTOR_NO_CREDENTIALS in reasons
        assert cod.MAPPING_NOT_CONFIGURED in reasons

    def test_a_collector_that_cannot_be_built_says_so_rather_than_looking_absent(
        self, tmp_path, _stub_manager_class, monkeypatch
    ):
        def refuse(credentials_path):
            raise FileNotFoundError(credentials_path)

        _patch_collector(monkeypatch, refuse)
        token = tmp_path / "token.json"
        token.write_text("{}", encoding="utf-8")
        mapping = tmp_path / "mapping.json"
        mapping.write_text(json.dumps({"vid": "content"}), encoding="utf-8")

        daemon = cod.build_daemon(
            state_dir=tmp_path / "state",
            dry_run=True,
            youtube_credentials=token,
            video_mapping_file=mapping,
        )
        reasons = daemon._sync_youtube_analytics().metrics["unconfigured_reasons"]

        assert daemon.youtube_sync is None
        assert cod.COLLECTOR_UNAVAILABLE in reasons

    def test_a_daemon_built_by_hand_with_no_reason_does_not_invent_one(self, tmp_path):
        """Everything the daemon can see is configured, yet it has no
        collector. It says the reason was not recorded rather than picking
        the likeliest-looking one."""
        mapping = tmp_path / "mapping.json"
        mapping.write_text(json.dumps({"vid": "content"}), encoding="utf-8")

        reasons = (
            _daemon(tmp_path, video_mapping_file=mapping)
            ._sync_youtube_analytics()
            .metrics["unconfigured_reasons"]
        )

        assert reasons == [cod.COLLECTOR_REASON_UNRECORDED]


class TestTheCliPassesBothInputsThrough:
    def test_the_flags_reach_build_daemon(self, tmp_path, monkeypatch):
        seen = {}

        def build(**kwargs):
            seen.update(kwargs)
            return SimpleNamespace(run_optimization_cycle=lambda: [])

        monkeypatch.setattr(cod, "build_daemon", build)

        assert (
            cod.main(
                [
                    "--state-dir",
                    str(tmp_path),
                    "--youtube-credentials",
                    "/tmp/token.json",
                    "--video-mapping",
                    "/tmp/mapping.json",
                ]
            )
            == 0
        )

        assert seen["youtube_credentials"] == Path("/tmp/token.json")
        assert seen["video_mapping_file"] == Path("/tmp/mapping.json")

    def test_the_flags_have_no_default_path(self, tmp_path, monkeypatch):
        """Neither value is the agent's to guess: absent means absent."""
        seen = {}

        def build(**kwargs):
            seen.update(kwargs)
            return SimpleNamespace(run_optimization_cycle=lambda: [])

        monkeypatch.setattr(cod, "build_daemon", build)
        cod.main(["--state-dir", str(tmp_path)])

        assert seen["youtube_credentials"] is None
        assert seen["video_mapping_file"] is None
