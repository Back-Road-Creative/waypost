"""W10d: the generator emits every GMS description shape, blank lines included.

Every place, road, URL and number is invented (synthetic); no production text,
route or location appears here.
"""

import dataclasses

import pytest
from drive_text_synthetic import (
    ASHMERE,
    BRIN,
    HARROW,
    OCEAN,
    QUILL,
    ROAD,
    _review,
)
from test_drive_text_generator import (
    GOLDEN_DESCRIPTION,
    RULE,
    golden_drive_data_synthetic,
)

from seo_manager.analytics.final_text_review import AGREE, DEFAULT_TEXT_POLICY
from seo_manager.analytics.final_text_surfaces import (
    build_visible,
    compose_description,
)
from seo_manager.generation.drive_text import PlaceRef, build_drive_text

LIST_ID = "PLsynth000000001"
URL = "https://www.youtube.com/playlist?list=" + LIST_ID
POLICY = dataclasses.replace(DEFAULT_TEXT_POLICY, playlist_allowlist=(LIST_ID,))


def full_drive_synthetic():
    """The golden drive plus every GMS body line and the footer lines."""
    return dataclasses.replace(
        golden_drive_data_synthetic(),
        journey_start=BRIN, journey_end=QUILL, passing=[OCEAN], roads=[ROAD],
        states=[HARROW, ASHMERE], features=[OCEAN],
        playlist=("Ireland", URL), cadence=("Ireland", "Sunday"),
    )  # fmt: skip


def _gms_shaped(lines_after_body, footer):
    """GOLDEN_DESCRIPTION with GMS's body-line group and footer lines added.

    GMS inserts its body lines as ``"\\n" + lines + "\\n"`` before
    ``"\\n📍 Location:"`` (a blank line each side) and joins the footer lines
    contiguously, a blank line before the closing rule.
    """
    head, tail = GOLDEN_DESCRIPTION.split("\n\U0001f4cd Location:", 1)
    out = head + "\n" + "\n".join(lines_after_body) + "\n\n\U0001f4cd Location:" + tail
    site = "\U0001f30d https://gomoveshift.com\n"
    return out.replace(site, "\n".join(footer) + "\n" + site, 1)


FULL_DESCRIPTION = _gms_shaped(
    [
        "Started in Brindlemoor, ended in Quillhaven, passing through Oceanmarsh.",
        "Roads traveled: Tidewater Road.",
        "Spans 2 states: Harrow County, Ashmere Vale.",
        "Highlights along the route: Oceanmarsh.",
    ],
    [f"▶ Full Ireland playlist: {URL}", "New Ireland drive every Sunday"],
)


def _entries(data):
    return build_visible(build_drive_text(data))


def _ids(data):
    return [getattr(p, "template_id", None) for p in build_drive_text(data)]


def test_w10d_2_full_synthetic_drive_is_byte_identical_to_the_gms_shape():
    assert compose_description(_entries(full_drive_synthetic())) == FULL_DESCRIPTION
    # The blank lines are where GMS puts them, around the body-line group.
    assert "Quillhaven.\n\nStarted in" in FULL_DESCRIPTION
    assert "Oceanmarsh.\n\n\U0001f4cd Location:" in FULL_DESCRIPTION
    assert "Sunday\n\U0001f30d" in FULL_DESCRIPTION and RULE in FULL_DESCRIPTION


def test_w10d_2_the_full_synthetic_drive_review_agrees():
    review = _review(_entries(full_drive_synthetic()), policy=POLICY)
    assert review.verdict == AGREE, review.reasons


EXTRA = [
    {"evidence_id": f"z-{k}", "kind": "region", "value": f"Zone {k}", "source": "gps"}
    for k in range(1, 14)
]
ZONES = [PlaceRef(f"z-{k}", f"Zone {k}") for k in range(1, 14)]

SHAPES = {
    "arc": (dict(journey_start=BRIN, journey_end=QUILL), "description.journey.arc.0"),
    "arc_passing": (
        dict(journey_start=BRIN, journey_end=QUILL, passing=[OCEAN]),
        "description.journey.arc.1",
    ),
    "towns": (dict(towns=[BRIN, OCEAN]), "description.journey.towns.2"),
    "towns_roads": (
        dict(towns=[BRIN, QUILL], roads=[ROAD]),
        "description.journey.towns_roads.2.1",
    ),
    "roads": (dict(roads=[ROAD]), "description.roads.1"),
    "states": (dict(states=[HARROW, ASHMERE]), "description.states.2"),
    "features": (dict(features=[OCEAN]), "description.features.1"),
    "through_more": (dict(through=ZONES), "description.through.12.more"),
    "playlist": (dict(playlist=("USA", URL)), "description.playlist.full"),
    "cadence": (dict(cadence=("USA", "Friday")), "description.cadence.weekly"),
    "filmed": (dict(filmed=(2024, 2)), "description.filmed.month"),
}


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_w10d_1_every_registered_shape_is_emitted_and_reviewed_agree(shape):
    change, template_id = SHAPES[shape]
    data = dataclasses.replace(golden_drive_data_synthetic(), **change)
    assert template_id in _ids(data)
    review = _review(_entries(data), policy=POLICY, extra=EXTRA)
    assert review.verdict == AGREE, review.reasons


def test_w10d_1_through_overflow_names_twelve_and_counts_the_rest():
    data = dataclasses.replace(golden_drive_data_synthetic(), through=ZONES)
    text = compose_description(_entries(data))
    assert "Zone 12 … +1 more\n" in text and "Zone 13" not in text


def test_w10d_1_a_single_state_prints_no_states_line_as_gms_does():
    data = dataclasses.replace(golden_drive_data_synthetic(), states=[HARROW])
    assert not any(str(t).startswith("description.states") for t in _ids(data))
    assert "description.gap" not in _ids(data)


@pytest.mark.parametrize(
    "change",
    [
        dict(towns=[BRIN] * 13),
        dict(roads=[ROAD] * 6),
        dict(states=[HARROW] * 13),
        dict(features=[OCEAN] * 6),
        dict(journey_start=BRIN, journey_end=QUILL, passing=[OCEAN] * 6),
        dict(route=[BRIN] * 9),
    ],
    ids=["towns", "roads", "states", "features", "passing", "route"],
)
def test_w10d_4_over_a_cap_raises_never_truncates(change):
    with pytest.raises(ValueError, match="at most"):
        build_drive_text(dataclasses.replace(golden_drive_data_synthetic(), **change))


@pytest.mark.parametrize(
    "change",
    [
        dict(journey_start=BRIN, journey_end=BRIN),
        dict(journey_start=BRIN),
        dict(journey_start=BRIN, journey_end=QUILL, towns=[OCEAN]),
        dict(passing=[OCEAN]),
        dict(playlist=("Ireland", URL), site=False),
        dict(cadence=("Ireland", "Sunday"), site=False),
        dict(playlist=("Atlantis", URL)),
        dict(cadence=("Ireland", "Someday")),
    ],
    ids=[
        "arc_same_end", "arc_half", "arc_and_towns", "passing_no_arc",
        "playlist_no_site", "cadence_no_site", "playlist_region", "weekday",
    ],
)  # fmt: skip
def test_w10d_3_data_that_cannot_be_rendered_raises(change):
    with pytest.raises(ValueError):
        build_drive_text(dataclasses.replace(golden_drive_data_synthetic(), **change))


def test_w10d_3_a_playlist_id_off_the_allowlist_is_refused():
    data = dataclasses.replace(golden_drive_data_synthetic(), playlist=("USA", URL))
    reasons = _review(_entries(data)).reasons  # default policy: empty allowlist
    assert "param_slot:description.playlist:url" in reasons


# --- mutations the golden must kill -------------------------------------------


def _full_entries():
    return _entries(full_drive_synthetic())


def _edit(entries, surface, text):
    entry = next(e for e in entries if e["surface"] == surface)
    entry["text"] = text
    return entries


def test_w10d_mutation_the_states_count_comes_from_the_skeleton_not_data():
    entries = _edit(
        _full_entries(), "description.states",
        "Spans 3 states: Harrow County, Ashmere Vale.",
    )  # fmt: skip
    assert "param_text:description.states" in _review(entries, policy=POLICY).reasons


def test_w10d_mutation_a_free_word_joined_into_a_body_line_is_refused():
    entries = _edit(
        _full_entries(), "description.journey",
        "Started in Brindlemoor, ended in scenic Quillhaven, passing through Oceanmarsh.",
    )  # fmt: skip
    assert "param_text:description.journey" in _review(entries, policy=POLICY).reasons


def test_w10d_mutation_a_skipped_blank_line_is_refused():
    entries = [e for e in _full_entries() if e["surface"] != "description.gap"]
    assert "layout_gap:gap" in _review(entries, policy=POLICY).reasons
    assert compose_description(entries) != FULL_DESCRIPTION


def test_w10d_a_blank_line_with_no_body_lines_is_refused():
    golden = build_visible(build_drive_text(golden_drive_data_synthetic()))
    gap = next(e for e in _full_entries() if e["surface"] == "description.gap")
    at = next(i for i, e in enumerate(golden) if e["surface"] == "description.location")
    entries = [*golden[:at], gap, *golden[at:]]
    assert "layout_gap:gap" in _review(entries).reasons
