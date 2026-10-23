"""Title casing keeps acronyms, apostrophes and numerals intact.

`str.title()` turns "GPS" into "Gps", "Don't" into "Don'T" and "II" into
"Ii". `_optimize_title` must capitalise per word instead.
"""

import pytest

from seo_manager.generation.base import BaseSEOGenerator


@pytest.fixture
def generator():
    return BaseSEOGenerator()


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("gps tracks for the trail", "Gps Tracks For The Trail"),
        ("GPS tracks for the trail", "GPS Tracks For The Trail"),
        ("don't miss this loop", "Don't Miss This Loop"),
        ("DON'T miss this loop", "DON'T Miss This Loop"),
        ("rock island II sunrise", "Rock Island II Sunrise"),
        ("the 3rd bridge at 5am", "The 3rd Bridge At 5am"),
        ("state-of-the-art route", "State-Of-The-Art Route"),
        ("rock'n'roll loop", "Rock'n'roll Loop"),
        ("iPhone mapping tips", "iPhone Mapping Tips"),
        ("  extra   spaces  here ", "  Extra   Spaces  Here "),
        ("", ""),
    ],
)
def test_title_case_per_word(generator, raw, expected):
    assert generator._optimize_title(raw, limit=100) == expected


def test_acronym_survives_truncation(generator):
    raw = "GPS " + "word " * 30
    out = generator._optimize_title(raw, limit=40)
    assert out.startswith("GPS Word")
    assert len(out) <= 40
    assert out.endswith("...")
