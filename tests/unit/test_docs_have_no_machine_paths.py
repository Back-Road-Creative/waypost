"""Shipped documents describe locations by variable or option, never by a machine path."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# An absolute home-directory, drive-mount or Windows-drive path.
MACHINE_PATH = re.compile(
    r"(?<![A-Za-z0-9_.~$])(?:/home/[A-Za-z0-9_.-]+|/Users/[A-Za-z0-9_.-]+"
    r"|/mnt/[a-z](?:/|\b)|[A-Za-z]:\\[A-Za-z0-9_.-]+)"
)


REQUIRED = [
    ROOT / "README.md",
    ROOT / "ARCHITECTURE.md",
    ROOT / "docs" / "YOUTUBE_ANALYTICS_SETUP.md",
]

# The package was once published from a differently named checkout. The retired
# name is assembled from parts so this file does not itself carry it.
_RETIRED_CHECKOUT = "-".join(("social", "media", "seo", "manager"))
RETIRED_LAYOUT = re.compile(re.escape(_RETIRED_CHECKOUT))


def _documents():
    found = list(REQUIRED)
    found += sorted((ROOT / "docs").rglob("*.md"))
    found += sorted((ROOT / "seo_manager").rglob("*.md"))
    return list(dict.fromkeys(found))


@pytest.mark.parametrize("doc", REQUIRED, ids=lambda p: str(p.relative_to(ROOT)))
def test_required_documents_exist(doc):
    assert doc.is_file(), f"{doc.relative_to(ROOT)} is missing"


def test_the_scan_sees_the_documents():
    assert len(_documents()) >= 3


def test_the_retired_layout_pattern_matches_what_it_is_meant_to_catch():
    assert RETIRED_LAYOUT.search(f"cd {_RETIRED_CHECKOUT}")
    assert not RETIRED_LAYOUT.search("install the seo_manager package")


@pytest.mark.parametrize("doc", REQUIRED, ids=lambda p: str(p.relative_to(ROOT)))
def test_setup_docs_use_the_current_checkout_name(doc):
    text = doc.read_text(encoding="utf-8")
    hit = RETIRED_LAYOUT.search(text)
    assert not hit, f"{doc.relative_to(ROOT)} still names the retired checkout"


def test_the_pattern_matches_what_it_is_meant_to_catch():
    assert MACHINE_PATH.search("cd /home/someone/project")
    assert MACHINE_PATH.search("copy to /mnt/d/clips")
    assert MACHINE_PATH.search(r"open C:\Users\someone")
    assert not MACHINE_PATH.search("under ~/.local/share/seo-manager")
    assert not MACHINE_PATH.search("set $HOME/data")


@pytest.mark.parametrize("doc", _documents(), ids=lambda p: str(p.relative_to(ROOT)))
def test_document_names_no_machine_path(doc):
    hits = [
        n
        for n, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1)
        if MACHINE_PATH.search(line)
    ]
    assert not hits, f"machine path on line(s) {hits}"
