"""Version introspection works without configuration or command execution."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from seo_manager import __version__
from seo_manager.cli import main


def test_version_reports_package_version_without_configuration(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    for name in ("SEO_BRAND_YAML", "SEO_SCHEDULE_DB", "SEO_DECISIONS_DB"):
        monkeypatch.setenv(name, str(tmp_path / "missing"))
    with pytest.raises(SystemExit) as result:
        main(["--version"])
    assert result.value.code == 0
    captured = capsys.readouterr()
    assert captured.out == f"seo-manager {__version__}\n"
    assert not captured.err
    assert list(tmp_path.iterdir()) == []


def test_installed_and_module_versions_match_outside_checkout(tmp_path):
    found = shutil.which("seo-manager", path=str(Path(sys.executable).parent))
    assert found, "the package must install seo-manager"
    command = Path(found)
    for invocation in ([str(command)], [sys.executable, "-I", "-m", "seo_manager.cli"]):
        result = subprocess.run(
            [*invocation, "--version"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == f"seo-manager {__version__}\n"
        assert not result.stderr


def test_missing_command_still_returns_usage_error(capsys):
    with pytest.raises(SystemExit) as result:
        main([])
    assert result.value.code == 2
    assert "required" in capsys.readouterr().err
