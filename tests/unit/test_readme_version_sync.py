"""The version names the docs quote are the ones the code ships."""

import re
from pathlib import Path

from seo_manager.analytics.drive_templates import DRIVE_TEMPLATES_VERSION
from seo_manager.analytics.final_text_review import REVIEW_VERSION, FinalTextPolicy
from seo_manager.analytics.title_selection import TitlePolicy

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
REVIEW_DOC = ROOT / "docs" / "final-text-review.md"

CURRENT = {
    "final-text-review": REVIEW_VERSION,
    "final-text-policy": FinalTextPolicy().version,
    "drive-templates": DRIVE_TEMPLATES_VERSION,
    "title-policy": TitlePolicy().version,
}
VERSION_NAME = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in CURRENT) + r")-\d+\b"
)


def _stale(path):
    text = path.read_text(encoding="utf-8")
    return sorted(
        {m.group(0) for m in VERSION_NAME.finditer(text)} - set(CURRENT.values())
    )


def test_readme_names_only_current_versions():
    assert _stale(README) == []


def test_review_doc_names_only_current_versions():
    assert REVIEW_DOC.is_file()
    assert _stale(REVIEW_DOC) == []


def test_review_doc_states_every_current_version():
    text = REVIEW_DOC.read_text(encoding="utf-8")
    for version in CURRENT.values():
        assert version in text, f"{version} missing from docs/final-text-review.md"


def test_readme_title_is_waypost_and_indexes_the_review_doc():
    text = README.read_text(encoding="utf-8")
    assert text.splitlines()[0] == "# Waypost"
    assert "(docs/final-text-review.md)" in text


def test_readme_final_text_bullet_is_short():
    bullet = next(
        line for line in README.read_text(encoding="utf-8").splitlines()
        if line.startswith("- ") and "Final-text review" in line
    )
    assert len(bullet) < 600
