"""Docs do not describe modules that were removed as dead code."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS = [
    ROOT / "README.md",
    ROOT / "ARCHITECTURE.md",
    *sorted((ROOT / "docs").rglob("*.md")),
]
REMOVED_NAMES = [
    "seo_manager/autofix",
    "autofix/",
    "AutoFixEngine",
    "Auto-Fix Engine",
    "youtube_theme_sync",
    "YouTubeThemeSync",
    "theme sync",
    "ThumbnailDesignSystem",
    "ThumbnailValidator",
    "ThumbnailMode",
]


@pytest.mark.parametrize("name", REMOVED_NAMES)
def test_no_doc_names_a_removed_module(name):
    offenders = [
        str(p.relative_to(ROOT))
        for p in DOCS
        if name.lower() in p.read_text(encoding="utf-8").lower()
    ]
    assert offenders == []
