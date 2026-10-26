"""The installed command and the module expose the same public CLI."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from seo_manager.cli import main


def test_installed_command_matches_module_help_outside_checkout(tmp_path):
    found = shutil.which("seo-manager", path=str(Path(sys.executable).parent))
    assert found, "the package must install seo-manager"
    command = Path(found)
    for args in (["--help"], ["schedule", "claim", "--help"]):
        installed = subprocess.run(
            [str(command), *args],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        module = subprocess.run(
            [sys.executable, "-I", "-m", "seo_manager.cli", *args],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert installed.returncode == module.returncode == 0
        assert installed.stdout == module.stdout
        assert not installed.stderr and not module.stderr


def test_placeholder_sync_is_not_a_successful_command(capsys):
    with pytest.raises(SystemExit) as error:
        main(["sync", "--dry-run"])
    assert error.value.code == 2
    output = capsys.readouterr()
    assert "invalid choice" in output.err and not output.out
