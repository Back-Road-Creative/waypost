"""The unused blog metadata generator stays removed.

``SEOManager`` only ever selected it for ``Platform.BLOG``, which nothing in the
shipped code uses: the CLI and the daemon only drive YouTube. This test keeps the
generator from coming back unnoticed.
"""

import importlib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PACKAGE_DIR = REPO / "seo_manager"


def test_blog_generator_module_cannot_be_imported():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("seo_manager.generation.blog_generator")


def test_blog_generator_file_is_gone():
    assert not (PACKAGE_DIR / "generation" / "blog_generator.py").exists()


def test_no_source_names_the_blog_generator():
    offenders = [
        str(path.relative_to(REPO))
        for path in PACKAGE_DIR.rglob("*.py")
        if "BlogMetadataGenerator" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
