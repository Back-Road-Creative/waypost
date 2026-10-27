"""State files are replaced whole or not at all.

A write that is interrupted half way used to leave a truncated JSON file where
the previous good state had been, and the next start then read garbage (or, for
the quota counter, raised and refused to run at all). Every state writer now
goes through ``seo_manager.fsio.atomic_write_json``: the new content is written
to a sibling temporary file and moved over the old one in a single step.
"""

import json
import logging
import os
from datetime import UTC, datetime, timezone
from types import SimpleNamespace

import pytest

from seo_manager import fsio
from seo_manager.analytics import continuous_optimization_daemon as cod
from seo_manager.analytics.theme_rotator import ThemeRotator
from seo_manager.analytics.thumbnail_ab_tracker import ThumbnailABTracker
from seo_manager.quota.manager import QuotaManager

OLD = {"keep": "me"}


def _interrupt_mid_dump(monkeypatch):
    """Make ``json.dump`` write part of the document and then die."""
    real_dump = json.dump

    def half_dump(obj, fp, *args, **kwargs):
        fp.write('{"half": ')
        fp.flush()
        raise RuntimeError("interrupted mid-write")

    monkeypatch.setattr(json, "dump", half_dump)
    return real_dump


def _no_tmp_left(directory):
    return [p.name for p in directory.iterdir() if p.name.endswith(".tmp")] == []


class TestAtomicWriteJson:
    def test_writes_the_document(self, tmp_path):
        path = tmp_path / "state.json"
        fsio.atomic_write_json(path, {"a": 1})
        assert json.loads(path.read_text()) == {"a": 1}
        assert _no_tmp_left(tmp_path)

    def test_creates_missing_parent_directory(self, tmp_path):
        path = tmp_path / "deep" / "er" / "state.json"
        fsio.atomic_write_json(path, {"a": 1})
        assert json.loads(path.read_text()) == {"a": 1}

    def test_interrupted_write_keeps_the_old_content(self, tmp_path, monkeypatch):
        path = tmp_path / "state.json"
        path.write_text(json.dumps(OLD))
        _interrupt_mid_dump(monkeypatch)
        with pytest.raises(RuntimeError):
            fsio.atomic_write_json(path, {"new": True})
        assert json.loads(path.read_text()) == OLD
        assert _no_tmp_left(tmp_path)

    def test_failed_replace_keeps_the_old_content(self, tmp_path, monkeypatch):
        path = tmp_path / "state.json"
        path.write_text(json.dumps(OLD))

        def boom(src, dst):
            raise OSError("disk went away")

        monkeypatch.setattr(os, "replace", boom)
        with pytest.raises(OSError):
            fsio.atomic_write_json(path, {"new": True})
        assert json.loads(path.read_text()) == OLD
        assert _no_tmp_left(tmp_path)

    @pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
    def test_keeps_the_mode_of_the_file_it_replaces(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text(json.dumps(OLD))
        path.chmod(0o664)
        fsio.atomic_write_json(path, {"new": True})
        assert path.stat().st_mode & 0o777 == 0o664


class TestCallersAreAtomic:
    def test_quota_manager_save(self, tmp_path, monkeypatch):
        manager = QuotaManager(project_id="p", storage_path=tmp_path)
        manager.track_operation("search", cost=100)
        before = manager.quota_file.read_text()
        _interrupt_mid_dump(monkeypatch)
        with pytest.raises(RuntimeError):
            manager.track_operation("search", cost=100)
        assert manager.quota_file.read_text() == before

    def test_theme_rotator_save(self, tmp_path, monkeypatch):
        rotator = ThemeRotator(state_dir=tmp_path, strategy="explore")
        rotator._save_state()
        state = tmp_path / "theme_rotation_state.json"
        before = state.read_text()
        _interrupt_mid_dump(monkeypatch)
        with pytest.raises(RuntimeError):
            rotator._save_state()
        assert state.read_text() == before

    def test_thumbnail_tracker_save(self, tmp_path, monkeypatch):
        tracker = ThumbnailABTracker(state_dir=tmp_path)
        tracker._save_state()
        state = tmp_path / "thumbnail_ab_state.json"
        before = state.read_text()
        _interrupt_mid_dump(monkeypatch)
        with pytest.raises(RuntimeError):
            tracker._save_state()
        assert state.read_text() == before

    def _daemon(self, tmp_path):
        manager = SimpleNamespace(
            optimizer=SimpleNamespace(
                analyze_patterns=lambda min_views=0: {"tags": []},
                get_title_length_guidance=lambda: SimpleNamespace(
                    range=(50, 70), basis="style"
                ),
            ),
            analytics=SimpleNamespace(get_summary=lambda: {}),
        )
        return cod.ContinuousOptimizationDaemon(
            manager=manager, state_dir=tmp_path / "state"
        )

    def test_daemon_state_save(self, tmp_path, monkeypatch):
        daemon = self._daemon(tmp_path)
        daemon._save_state()
        state = daemon.state_dir / "daemon_state.json"
        before = state.read_text()
        _interrupt_mid_dump(monkeypatch)
        with pytest.raises(RuntimeError):
            daemon._save_state()
        assert state.read_text() == before

    def test_daemon_run_record_write(self, tmp_path, monkeypatch):
        daemon = self._daemon(tmp_path)
        record = SimpleNamespace()
        path = daemon.state_dir / cod.RUN_RECORD_FILENAME
        daemon.state_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(OLD))
        monkeypatch.setattr(cod, "asdict", lambda r: {"new": True})
        _interrupt_mid_dump(monkeypatch)
        with pytest.raises(RuntimeError):
            daemon._write_run_record(record)
        assert json.loads(path.read_text()) == OLD

    def test_daemon_learned_config_write(self, tmp_path, monkeypatch):
        daemon = self._daemon(tmp_path)
        daemon.state_dir.mkdir(parents=True, exist_ok=True)
        config = daemon.state_dir / "learned_config.json"
        config.write_text(json.dumps(OLD))
        _interrupt_mid_dump(monkeypatch)
        result = daemon._update_templates()
        assert result.success is False
        assert json.loads(config.read_text()) == OLD


class TestCorruptQuotaFile:
    def test_starts_fresh_with_a_warning(self, tmp_path, caplog):
        project = tmp_path / "p"
        project.mkdir()
        (project / "quota_usage.json").write_text('{"total_used": 12, "oper')
        with caplog.at_level(logging.WARNING):
            manager = QuotaManager(project_id="p", storage_path=tmp_path)
        assert manager.get_usage()["total_used"] == 0
        assert any("quota" in r.message.lower() for r in caplog.records)
        # The fresh state is written back, so the next start reads a good file.
        assert json.loads(manager.quota_file.read_text())["total_used"] == 0

    def test_wrong_shape_also_starts_fresh(self, tmp_path):
        project = tmp_path / "p"
        project.mkdir()
        bad = {
            "last_reset": datetime.now(UTC).isoformat(),
            "operations": [{"x": 1}],
        }
        (project / "quota_usage.json").write_text(json.dumps(bad))
        manager = QuotaManager(project_id="p", storage_path=tmp_path)
        assert manager.get_usage()["total_used"] == 0
