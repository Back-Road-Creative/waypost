"""Modules removed as dead code stay removed and unimported."""

import importlib
import importlib.util
from pathlib import Path

import pytest

import seo_manager

REMOVED = [
    "seo_manager.youtube.thumbnail_design_system",
]


@pytest.mark.parametrize("name", REMOVED)
def test_removed_module_cannot_be_imported(name):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(name)


@pytest.mark.parametrize("name", REMOVED)
def test_no_source_file_imports_a_removed_module(name):
    root = Path(seo_manager.__file__).parent
    leaf = name.rsplit(".", 1)[-1]
    needles = (f"import {name}", f"from {name}", f"from .{leaf}", f"from ..{leaf}")
    offenders = [
        str(p.relative_to(root))
        for p in root.rglob("*.py")
        if any(n in p.read_text(encoding="utf-8") for n in needles)
    ]
    assert offenders == []
