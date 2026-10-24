"""The unused blog and Twitter adapters stay removed.

Nothing in the shipped code selected them: the CLI and the daemon only drive
YouTube. This test keeps the adapters and their ``Platform`` members from
coming back unnoticed.
"""

import importlib
from pathlib import Path

import pytest

from seo_manager.core.metadata import Platform

REPO = Path(__file__).resolve().parents[2]
PACKAGE_DIR = REPO / "seo_manager"

REMOVED_MODULES = (
    "seo_manager.platforms.blog",
    "seo_manager.platforms.twitter",
)

REMOVED_FILES = (
    "platforms/blog.py",
    "platforms/twitter.py",
)

REMOVED_NAMES = ("BlogAdapter", "TwitterAdapter")


@pytest.mark.parametrize("name", REMOVED_MODULES)
def test_removed_module_cannot_be_imported(name):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(name)


@pytest.mark.parametrize("relative", REMOVED_FILES)
def test_removed_file_is_gone(relative):
    assert not (PACKAGE_DIR / relative).exists()


@pytest.mark.parametrize("member", ("BLOG", "TWITTER"))
def test_platform_enum_has_no_removed_member(member):
    assert member not in Platform.__members__


@pytest.mark.parametrize("name", REMOVED_NAMES)
def test_no_source_names_a_removed_class(name):
    offenders = [
        str(path.relative_to(REPO))
        for path in PACKAGE_DIR.rglob("*.py")
        if name in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_no_source_selects_a_removed_platform():
    offenders = []
    for path in PACKAGE_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "Platform.BLOG" in text or "Platform.TWITTER" in text:
            offenders.append(str(path.relative_to(REPO)))
    assert offenders == []
