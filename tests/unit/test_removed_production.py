"""The unused ``seo_manager.production`` package stays removed.

Nothing in the shipped code imported it; its error-recovery, health-check,
rate-limiting, structured-logging and tracker helpers were never wired into the
CLI or the upload path. This test keeps it from coming back unnoticed.
"""

import importlib
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PACKAGE_DIR = REPO / "seo_manager"

REMOVED_MODULES = (
    "seo_manager.production",
    "seo_manager.production.error_recovery",
    "seo_manager.production.structured_logger",
    "seo_manager.production.rate_limiter",
    "seo_manager.production.health_check",
    "seo_manager.production.issue_tracker",
    "seo_manager.production.podcast_quality_tracker",
)


@pytest.mark.parametrize("name", REMOVED_MODULES)
def test_removed_module_cannot_be_imported(name):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(name)


def test_production_package_directory_is_gone():
    assert not (PACKAGE_DIR / "production").exists()


def test_production_test_directory_is_gone():
    assert not (REPO / "tests" / "production").exists()


def test_no_source_imports_the_removed_package():
    needle = "seo_manager" + ".production"
    relative = "from " + ".production"
    offenders = []
    for path in PACKAGE_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if needle in text or relative in text:
            offenders.append(str(path.relative_to(REPO)))
    assert offenders == []
