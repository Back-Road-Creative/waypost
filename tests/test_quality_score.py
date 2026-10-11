"""Tests for quality score calculation — strict YouTube scoring.

No participation points. Each component either meets the bar or scores 0.
Score 1.0 = all four components pass. Anything less means something is wrong.
"""

import pytest

from seo_manager.generation.base import BaseSEOGenerator


@pytest.fixture
def gen():
    return BaseSEOGenerator()


def _optimal_score(gen, title="x" * 50, desc="x" * 500, tags=None):
    """Helper: score with defaults that hit all optimal ranges."""
    if tags is None:
        tags = ["x"] * 12
    return gen._calculate_quality_score(title, desc, tags)


class TestStrictScoring:
    """No participation points — miss a criterion, get 0 for it."""

    def test_perfect_score(self, gen):
        """All four components optimal = 1.0."""
        score = _optimal_score(gen)
        assert score == 1.0

    def test_short_title_scores_zero_title_component(self, gen):
        """Title under 40 chars = 0 for title (0.7 total)."""
        score = gen._calculate_quality_score("Short x", "x" * 500, ["x"] * 12)
        assert score == 0.7

    def test_short_description_scores_zero_desc_component(self, gen):
        """Description under 300 chars = 0 for description (0.7 total)."""
        score = gen._calculate_quality_score("x" * 50, "short", ["x"] * 12)
        assert score == 0.7

    def test_too_few_tags_scores_zero_tag_component(self, gen):
        """Under 10 tags = 0 for tags (0.8 total)."""
        score = gen._calculate_quality_score("x" * 50, "x" * 500, ["x"] * 3)
        assert score == 0.8

    def test_no_keyword_in_title_scores_zero_keyword_component(self, gen):
        """Tags not in title = 0 for keyword bonus (0.8 total)."""
        score = gen._calculate_quality_score(
            "A" * 50, "x" * 500, ["zzz_not_in_title"] * 12
        )
        assert score == 0.8

    def test_all_bad_scores_zero(self, gen):
        """Everything wrong = 0.0."""
        score = gen._calculate_quality_score("Hi", "Hi", ["zzz"])
        assert score == 0.0


class TestTitleLengthBoundaries:
    """Title must be 40-70 chars for the 0.3 component."""

    def test_39_chars_fails(self, gen):
        assert _optimal_score(gen, title="x" * 39) == 0.7

    def test_40_chars_passes(self, gen):
        assert _optimal_score(gen, title="x" * 40) == 1.0

    def test_70_chars_passes(self, gen):
        assert _optimal_score(gen, title="x" * 70) == 1.0

    def test_71_chars_fails(self, gen):
        assert _optimal_score(gen, title="x" * 71) == 0.7


class TestDescriptionLengthBoundaries:
    """Description must be 300+ chars for the 0.3 component."""

    def test_299_chars_fails(self, gen):
        assert _optimal_score(gen, desc="x" * 299) == 0.7

    def test_300_chars_passes(self, gen):
        assert _optimal_score(gen, desc="x" * 300) == 1.0

    def test_2000_chars_passes(self, gen):
        """Long descriptions are fine for YouTube."""
        assert _optimal_score(gen, desc="x" * 2000) == 1.0


class TestTagCountBoundaries:
    """Tag count must be 10-15 for the 0.2 component."""

    def test_9_tags_fails(self, gen):
        assert _optimal_score(gen, tags=["x"] * 9) == 0.8

    def test_10_tags_passes(self, gen):
        assert _optimal_score(gen, tags=["x"] * 10) == 1.0

    def test_15_tags_passes(self, gen):
        assert _optimal_score(gen, tags=["x"] * 15) == 1.0

    def test_16_tags_fails(self, gen):
        assert _optimal_score(gen, tags=["x"] * 16) == 0.8


class TestRealisticMetadata:
    """Realistic long-form metadata with a proper title must score 1.0."""

    def test_padded_title_metadata_scores_100(self, gen):
        """With a padded title, all criteria should be met."""
        # Title padded by route_context to 40+ chars
        title = "POV Driving Alpha Bay to Beta Cove | Gamma Head, Examplia"  # 52 chars
        description = (
            "Join me on a relaxing POV drive through the Gamma Peninsula.\n\n"
            "This full-length scenic driving video takes you through the beautiful "
            "roads of Gamma Peninsula to Delta Point, County Test, Examplia. "
            "Perfect for relaxation, study background, or virtual travel.\n\n"
            "Location: Gamma Peninsula to Delta Point, County Test, Examplia\n"
            "Duration: 62m 27s\n"
            "Recorded: January 10, 2025\n"
            "Published: Saturday, February 7, 2026\n\n"
            "Subscribe for more POV driving videos from around the world!\n"
            "Like this video if you enjoyed the drive\n"
            "Comment your favorite moment or guess the location!"
        )
        tags = [
            "POV driving",
            "scenic drive",
            "road trip",
            "driving video",
            "4K driving",
            "relaxing drive",
            "virtual travel",
            "dashcam",
            "drive with me",
            "scenic route",
            "POV",
            "long drive",
            "Gamma Peninsula to Delta Point County Test Examplia",
            "Gamma",
            "Peninsula",
        ]
        score = gen._calculate_quality_score(title, description, tags)
        assert score == 1.0, f"Realistic metadata scored {score}, expected 1.0"
