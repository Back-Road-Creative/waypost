"""
Unit tests for _get_intel_db and _log_seo_to_intel_db (manager.py).

Tests verify the importlib.util-based loading approach:
- Loads intel_db.py from _INTEL_SCRIPTS_DIR by file path, nothing else
- Ignores an intel_db already in sys.modules or elsewhere on sys.path
- Does NOT pollute sys.path or sys.modules
- Returns None gracefully on missing/broken modules
- Calls IntelDB(db_path) on success
- _log_seo_to_intel_db is non-fatal and always closes the DB
"""

import os
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from seo_manager.core import manager
from seo_manager.core.manager import _get_intel_db, _log_seo_to_intel_db

_ROOT = Path(__file__).resolve().parents[2]

_FAKE_INTEL_DB_SOURCE = (
    "class IntelDB:\n"
    "    SOURCE = {marker!r}\n"
    "\n"
    "    def __init__(self, db_path):\n"
    "        self.db_path = db_path\n"
)


def _write_intel_module(directory: Path, source: str = "") -> Path:
    """Write an intel_db.py into ``directory`` (created) and return the directory."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "intel_db.py").write_text(
        source or _FAKE_INTEL_DB_SOURCE.format(marker="configured")
    )
    return directory


def _decoy_module() -> types.ModuleType:
    decoy = types.ModuleType("intel_db")

    class _DecoyIntelDB:
        SOURCE = "decoy"

        def __init__(self, db_path):
            self.db_path = db_path

    decoy.IntelDB = _DecoyIntelDB
    return decoy


class TestGetIntelDb:
    """Tests for _get_intel_db using importlib.util.spec_from_file_location."""

    def test_returns_none_when_module_file_missing(self, tmp_path):
        """_get_intel_db returns None when intel_db.py does not exist at the expected path."""
        with patch.object(manager, "_INTEL_SCRIPTS_DIR", str(tmp_path / "empty")):
            result = _get_intel_db("/data/seo/test.db")
        assert result is None

    def test_returns_none_when_spec_is_none(self, tmp_path):
        """_get_intel_db returns None when spec_from_file_location returns None."""
        scripts = _write_intel_module(tmp_path / "scripts")
        with patch.object(manager, "_INTEL_SCRIPTS_DIR", str(scripts)), patch(
            "importlib.util.spec_from_file_location", return_value=None
        ):
            result = _get_intel_db("/data/seo/test.db")
        assert result is None

    def test_returns_none_when_spec_loader_is_none(self, tmp_path):
        """_get_intel_db returns None when spec.loader is None."""
        scripts = _write_intel_module(tmp_path / "scripts")
        mock_spec = MagicMock()
        mock_spec.loader = None
        with patch.object(manager, "_INTEL_SCRIPTS_DIR", str(scripts)), patch(
            "importlib.util.spec_from_file_location", return_value=mock_spec
        ):
            result = _get_intel_db("/data/seo/test.db")
        assert result is None

    def test_does_not_pollute_sys_path(self, tmp_path):
        """_get_intel_db must NOT add directories to sys.path, even on a miss."""
        original_path = sys.path.copy()
        with patch.object(manager, "_INTEL_SCRIPTS_DIR", str(tmp_path / "missing")):
            _get_intel_db("/data/seo/test.db")
        assert sys.path == original_path

    def test_leaves_sys_path_and_sys_modules_untouched_on_success(self, tmp_path):
        """A successful load adds nothing to sys.path and caches nothing in sys.modules."""
        scripts = _write_intel_module(tmp_path / "scripts")
        original_path = sys.path.copy()
        cached_before = sys.modules.get("intel_db")
        with patch.object(manager, "_INTEL_SCRIPTS_DIR", str(scripts)):
            result = _get_intel_db(str(tmp_path / "test.db"))
        assert result is not None
        assert sys.path == original_path
        assert sys.modules.get("intel_db") is cached_before

    def test_returns_intel_db_instance_on_success(self, tmp_path):
        """_get_intel_db returns module.IntelDB(db_path) on successful load."""
        scripts = _write_intel_module(tmp_path / "scripts")
        with patch.object(manager, "_INTEL_SCRIPTS_DIR", str(scripts)):
            result = _get_intel_db(str(tmp_path / "test.db"))

        assert result is not None
        assert result.SOURCE == "configured"
        assert result.db_path == str(tmp_path / "test.db")

    def test_returns_none_when_intel_db_constructor_raises(self, tmp_path):
        """A configured intel_db.py whose IntelDB raises on construction degrades to None."""
        scripts = _write_intel_module(
            tmp_path / "scripts",
            "class IntelDB:\n"
            "    def __init__(self, db_path):\n"
            "        raise RuntimeError('broken module')\n",
        )
        with patch.object(manager, "_INTEL_SCRIPTS_DIR", str(scripts)):
            result = _get_intel_db("/data/seo/test.db")
        assert result is None

    def test_returns_none_when_module_raises_on_import(self, tmp_path):
        """A configured intel_db.py that raises while executing degrades to None."""
        scripts = _write_intel_module(
            tmp_path / "scripts", "raise RuntimeError('import-time failure')\n"
        )
        with patch.object(manager, "_INTEL_SCRIPTS_DIR", str(scripts)):
            result = _get_intel_db("/data/seo/test.db")
        assert result is None

    def test_ignores_cached_intel_db_module(self, tmp_path):
        """An intel_db cached in sys.modules must not stand in for a missing file."""
        with patch.dict(sys.modules, {"intel_db": _decoy_module()}), patch.object(
            manager, "_INTEL_SCRIPTS_DIR", str(tmp_path / "missing")
        ):
            result = _get_intel_db("/data/seo/test.db")
        assert result is None

    def test_ignores_intel_db_elsewhere_on_sys_path(self, tmp_path, monkeypatch):
        """An intel_db.py importable from another sys.path entry must not be used."""
        decoy_dir = _write_intel_module(
            tmp_path / "decoy", _FAKE_INTEL_DB_SOURCE.format(marker="decoy")
        )
        monkeypatch.syspath_prepend(str(decoy_dir))
        with patch.dict(sys.modules):
            sys.modules.pop("intel_db", None)
            with patch.object(manager, "_INTEL_SCRIPTS_DIR", str(tmp_path / "missing")):
                result = _get_intel_db("/data/seo/test.db")
        assert result is None

    def test_loads_configured_file_over_cached_module(self, tmp_path):
        """The configured intel_db.py wins over an intel_db cached in sys.modules."""
        scripts = _write_intel_module(tmp_path / "scripts")
        with patch.dict(sys.modules, {"intel_db": _decoy_module()}), patch.object(
            manager, "_INTEL_SCRIPTS_DIR", str(scripts)
        ):
            result = _get_intel_db(str(tmp_path / "test.db"))
        assert result is not None
        assert result.SOURCE == "configured"

    def test_unset_scripts_dir_means_not_configured(self, capsys):
        """With INTEL_SCRIPTS_DIR unset there is no default location: the loader
        returns None and the warning names the variable."""
        with patch.object(manager, "_INTEL_SCRIPTS_DIR", ""):
            result = _get_intel_db("/data/seo/test.db")
        assert result is None
        assert "INTEL_SCRIPTS_DIR is not set" in capsys.readouterr().err

    def test_scripts_dir_is_empty_when_env_var_unset(self):
        """The module-level value is empty unless the environment sets it."""
        env = {k: v for k, v in os.environ.items() if k != "INTEL_SCRIPTS_DIR"}
        env["PYTHONPATH"] = str(_ROOT)
        out = subprocess.run(
            [
                sys.executable,
                "-c",
                "from seo_manager.core import manager; "
                "print(repr(manager._INTEL_SCRIPTS_DIR))",
            ],
            env=env,
            cwd=_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        assert out.stdout.strip() == "''"

    def test_env_var_overrides_and_loads_the_module(self, tmp_path):
        """INTEL_SCRIPTS_DIR names the directory; an intel_db.py there is loaded."""
        scripts = _write_intel_module(tmp_path / "scripts")
        env = {
            **os.environ,
            "INTEL_SCRIPTS_DIR": str(scripts),
            "PYTHONPATH": str(_ROOT),
        }
        code = (
            "from seo_manager.core import manager; "
            "db = manager._get_intel_db('x.db'); "
            "print(manager._INTEL_SCRIPTS_DIR, db.SOURCE)"
        )
        out = subprocess.run(
            [sys.executable, "-c", code],
            env=env,
            cwd=_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        assert out.stdout.split() == [str(scripts), "configured"]


class TestLogSeoToIntelDb:
    """Tests for _log_seo_to_intel_db — non-fatal IntelDB logging."""

    @pytest.fixture(autouse=True)
    def clear_intel_db_env(self, monkeypatch):
        """Ensure INTEL_DB_PATH is unset by default."""
        monkeypatch.delenv("INTEL_DB_PATH", raising=False)

    def test_noop_when_intel_db_path_empty(self):
        """No action when INTEL_DB_PATH is not set."""
        with patch("seo_manager.core.manager._get_intel_db") as mock:
            _log_seo_to_intel_db(
                content_id="x",
                platform="youtube",
                title="T",
                tag_count=1,
                generated_at="2026-01-01T00:00:00Z",
            )
            mock.assert_not_called()

    def test_noop_when_get_intel_db_returns_none(self, monkeypatch):
        """No crash when _get_intel_db returns None."""
        monkeypatch.setenv("INTEL_DB_PATH", "/data/seo/test.db")
        with patch("seo_manager.core.manager._get_intel_db", return_value=None):
            # Should not raise
            _log_seo_to_intel_db(
                content_id="x",
                platform="blog",
                title="T",
                tag_count=1,
                generated_at="2026-01-01T00:00:00Z",
            )

    def test_calls_start_upsert_finish(self, monkeypatch):
        """Successful path: calls start_scrape_run, upsert_news_item, finish_scrape_run."""
        monkeypatch.setenv("INTEL_DB_PATH", "/data/seo/test.db")
        mock_db = MagicMock()
        mock_db.start_scrape_run.return_value = "run-001"

        with patch("seo_manager.core.manager._get_intel_db", return_value=mock_db):
            _log_seo_to_intel_db(
                content_id="vid-123",
                platform="youtube",
                title="AI Safety Talk",
                tag_count=5,
                generated_at="2026-03-13T10:00:00Z",
            )

        mock_db.start_scrape_run.assert_called_once_with(
            "seo-youtube", source_name="vid-123"
        )
        mock_db.upsert_news_item.assert_called_once_with(
            date="2026-03-13",
            summary="SEO metadata: AI Safety Talk [youtube]",
            article_links=[],
            source_count=5,
        )
        mock_db.finish_scrape_run.assert_called_once_with(
            "run-001", items_found=1, items_imported=1
        )
        mock_db.close.assert_called_once()

    def test_closes_db_even_on_exception(self, monkeypatch):
        """db.close() must be called even if upsert_news_item raises."""
        monkeypatch.setenv("INTEL_DB_PATH", "/data/seo/test.db")
        mock_db = MagicMock()
        mock_db.start_scrape_run.return_value = "run-001"
        mock_db.upsert_news_item.side_effect = RuntimeError("db error")

        with patch("seo_manager.core.manager._get_intel_db", return_value=mock_db):
            # Should not raise — errors are caught
            _log_seo_to_intel_db(
                content_id="x",
                platform="blog",
                title="T",
                tag_count=1,
                generated_at="2026-01-01T00:00:00Z",
            )

        mock_db.close.assert_called_once()

    def test_date_extraction_from_generated_at(self, monkeypatch):
        """Date is extracted as first 10 chars of generated_at (YYYY-MM-DD)."""
        monkeypatch.setenv("INTEL_DB_PATH", "/data/seo/test.db")
        mock_db = MagicMock()
        mock_db.start_scrape_run.return_value = "run-001"

        with patch("seo_manager.core.manager._get_intel_db", return_value=mock_db):
            _log_seo_to_intel_db(
                content_id="x",
                platform="twitter",
                title="T",
                tag_count=1,
                generated_at="2026-07-04T12:00:00-05:00",
            )

        call_kwargs = mock_db.upsert_news_item.call_args.kwargs
        assert call_kwargs["date"] == "2026-07-04"
