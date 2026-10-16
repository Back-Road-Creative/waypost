"""Drive text: the generator turns typed (synthetic) drive data into the parts.

Every place and number is invented (synthetic); no production text appears.
"""

import dataclasses

import pytest
from drive_text_synthetic import (
    BRIN,
    GOLDEN_PARTS,
    HARROW,
    OCEAN,
    PLAN,
    QUILL,
    ROAD,
    SRC,
    TITLE,
    _review,
)

from seo_manager.analytics.drive_templates import LAYOUT
from seo_manager.analytics.final_text_review import (
    AGREE,
    FinalTextReviewRefused,
    verify_review,
)
from seo_manager.analytics.final_text_surfaces import (
    build_visible,
    compose_description,
    compose_tags,
)
from seo_manager.generation.drive_text import (
    ChapterData,
    DriveTextData,
    SeoArc,
    build_drive_text,
)  # fmt: skip


def golden_drive_data_synthetic() -> DriveTextData:
    """The (synthetic) golden input: invented places, invented numbers."""
    return DriveTextData(
        place=QUILL, final_seconds=2723, source_seconds=7500, highlights=True,
        edited=True, seo=SeoArc(HARROW, BRIN, QUILL, ROAD), body=True,
        terrain="coastal", road=ROAD, distance_km=47.2, heading="W",
        through=[HARROW], route=[BRIN, OCEAN, QUILL], insert_count=3,
        filmed=(2025, 9), geo_hashtags=[BRIN, QUILL],
        chapters=[
            ChapterData(0, BRIN, ("p-1",)), ChapterData(1500, QUILL, ("p-2",))
        ],
        place_tags=[BRIN, QUILL], month=9,
    )  # fmt: skip


RULE = "━" * 24
GOLDEN_DESCRIPTION = "\n".join(
    [
        "Highlights from 2h 5m of driving through Quillhaven.",
        "Harrow County 4K drive — Brindlemoor to Quillhaven via Tidewater Road.",
        "",
        "A 45m 23s coastal drive through Quillhaven.",
        "",
        "\U0001f4cd Location: Quillhaven via Tidewater Road",
        "\U0001f3ac Final cut: 45m 23s (edited from 2h 5m of footage)",
        "\U0001f6e3️ Distance: 47 km (29 mi)",
        "\U0001f9ed Heading: Westbound",
        "\U0001f5fa️ Through: Harrow County",
        "\U0001f6e4️ Route: Brindlemoor → Oceanmarsh → Quillhaven",
        "\U0001f4f8 3 photo/clip inserts",
        "\U0001f4c5 Filmed: Sep 2025",
        "",
        RULE,
        "",
        "\U0001f514 Subscribe for more POV driving videos!",
        "\U0001f44d Like this video if you enjoyed the drive",
        "\U0001f4ac Comment your favorite moment!",
        "",
        "\U0001f30d https://gomoveshift.com",
        "",
        RULE,
        "",
        "\U0001f4cd CHAPTERS",
        "0:00 Brindlemoor",
        "25:00 Quillhaven",
        "",
        "#Brindlemoor #Quillhaven #PovDriving #Drive #RoadTrip #DrivingVideo "
        "#4kDriving #RelaxingDrive #VirtualTravel #Dashcam #DriveWithMe #DrivingRoute",
    ]
)
GOLDEN_TAGS = [
    "Brindlemoor", "Quillhaven", "September", "autumn", "autumn drive",
    "POV driving", "drive", "road trip", "driving video", "4K driving",
    "relaxing drive", "virtual travel", "dashcam", "drive with me",
    "driving route", "POV", "long drive",
]  # fmt: skip


def golden_entries():
    return build_visible(build_drive_text(golden_drive_data_synthetic()))


def test_w10_5_the_generator_builds_exactly_the_typed_golden_parts():
    assert build_drive_text(golden_drive_data_synthetic()) == GOLDEN_PARTS


@pytest.mark.parametrize("name", ["golden (synthetic)"])
def test_w10_golden_synthetic_description_and_tags_are_byte_equal(name):
    entries = golden_entries()
    assert compose_description(entries) == GOLDEN_DESCRIPTION
    assert compose_tags(entries) == GOLDEN_TAGS


def test_w10_golden_synthetic_review_agrees_and_binds_the_sent_bytes():
    review = _review(golden_entries())
    assert review.verdict == AGREE, review.reasons
    assert review.description_layout == LAYOUT

    def verify(description, tags):
        verify_review(
            review, final_title=TITLE, visible={}, source_fingerprint=SRC,
            plan_fingerprint=PLAN, policy_version=review.decision_policy_version,
            description=description, tags=tags,
        )  # fmt: skip

    verify(GOLDEN_DESCRIPTION, GOLDEN_TAGS)
    with pytest.raises(FinalTextReviewRefused) as caught:
        verify(GOLDEN_DESCRIPTION.replace("Westbound", "Westbounds"), GOLDEN_TAGS)
    assert caught.value.reasons == ("stale:description",)
    with pytest.raises(FinalTextReviewRefused) as caught:
        verify(GOLDEN_DESCRIPTION, GOLDEN_TAGS[:-1])
    assert caught.value.reasons == ("stale:tags",)


# --- W10-5: the generator cannot emit an invalid surface ---------------------------


def test_w10_5_a_raw_string_for_a_numeric_field_raises_at_build_time():
    with pytest.raises(TypeError):
        build_drive_text(
            dataclasses.replace(golden_drive_data_synthetic(), distance_km="47")
        )


@pytest.mark.parametrize("change", [{"heading": "Westbound"}, {"terrain": "jungle"}])
def test_w10_5_an_unregistered_enum_value_raises_at_build_time(change):
    with pytest.raises(ValueError):
        build_drive_text(dataclasses.replace(golden_drive_data_synthetic(), **change))
