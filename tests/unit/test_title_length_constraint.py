"""Learned title-length range threaded from the optimizer into generation.

`SEOOptimizer.optimize_metadata` used to compute `get_optimal_title_length()`
and throw the result away, because no generator accepted a length constraint.
These tests pin the contract that replaces that dead call: no constraint means
byte-identical output; a constraint is honoured but only ever tightens the
platform cap; an unsatisfiable range leaves a whole title rather than a
word-chopped one; and `optimize_metadata` really hands the range to
`manager.generate`.
"""

import shutil
import tempfile
from pathlib import Path

import pytest

from seo_manager.analytics.seo_optimizer import SEOOptimizer
from seo_manager.core.manager import SEOManager
from seo_manager.core.metadata import ContentType
from seo_manager.generation.base import BaseSEOGenerator
from seo_manager.platforms.blog import BlogAdapter
from seo_manager.platforms.twitter import TwitterAdapter
from seo_manager.platforms.youtube import YouTubeAdapter

YT_KWARGS = dict(
    content_type=ContentType.VIDEO,
    topic="Driving the North Cascades Highway in Early Autumn Light",
    transcript=[{"text": "We start at Diablo Lake and head east."}],
    show_name="Open Road Show",
    episode_number=12,
)

# Titles captured from the pre-change code path (the show-name acronym keeps
# its capitals now that title casing is per word). If one of these moves, the
# "optional, defaults to today's behaviour" promise has been broken.
GOLDEN_CASES = [
    (
        YouTubeAdapter,
        YT_KWARGS,
        "Driving The North Cascades Highway In Early Autumn Light | ORS #12",
    ),
    (
        YouTubeAdapter,
        dict(content_type=ContentType.VIDEO, topic="Sunset Run"),
        "Sunset Run: Overview",
    ),
    (
        BlogAdapter,
        dict(content_type=ContentType.BLOG_POST, topic="Mount Si Trail Review"),
        "Mount Si Trail Review: Overview",
    ),
    (
        TwitterAdapter,
        dict(
            content_type=ContentType.BLOG_POST,
            topic="Why local inference beats the cloud for small models",
        ),
        "Why Local Inference Beats The Cloud For Small...",
    ),
]


@pytest.fixture
def storage():
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir)


@pytest.fixture
def generator():
    return BaseSEOGenerator()


def _manager(storage, adapter_cls):
    return SEOManager(site_id="t", platform=adapter_cls(), storage_path=storage)


class TestDefaultBehaviourUnchanged:
    @pytest.mark.parametrize("adapter_cls,kwargs,expected", GOLDEN_CASES)
    def test_generate_without_constraint_is_byte_identical(
        self, storage, adapter_cls, kwargs, expected
    ):
        assert _manager(storage, adapter_cls).generate(**kwargs).title == expected

    def test_optimize_title_without_range_is_unchanged(self, generator):
        long_title = (
            "This is a very long title that exceeds the maximum limit and needs cutting"
        )
        assert generator._optimize_title(
            long_title, limit=70, target_range=None
        ) == generator._optimize_title(long_title, limit=70)


class TestConstraintIsHonoured:
    def test_generated_title_falls_in_range(self, storage):
        metadata = _manager(storage, YouTubeAdapter).generate(
            title_length_range=(40, 50), **YT_KWARGS
        )
        assert 40 <= len(metadata.title) <= 50
        # Word boundary respected: nothing before the ellipsis is a fragment.
        assert metadata.title.endswith("...")
        assert not metadata.title[:-3].endswith(" ")


class TestPlatformCapWins:
    def test_range_wider_than_cap_is_clamped(self, storage):
        metadata = _manager(storage, BlogAdapter).generate(
            content_type=ContentType.BLOG_POST,
            topic="A Deliberately Overlong Blog Headline About Trail Conditions In The Cascades",
            title_length_range=(100, 140),
        )
        assert len(metadata.title) <= BlogAdapter().get_title_limit()

    def test_optimize_title_clamps_range_to_limit(self, generator):
        title = "Alpha Beta Gamma Delta Epsilon Zeta Eta Theta Iota Kappa Lambda"
        result = generator._optimize_title(title, limit=40, target_range=(80, 120))
        assert len(result) <= 40


class TestUnsatisfiableConstraint:
    """Policy: the range may only SHORTEN, only on a word boundary, and only
    when the result still lands inside the range. Otherwise the whole,
    slightly-off title is kept — an over-range title reads, a chopped one
    does not, and a cut below the minimum satisfies nothing."""

    def test_no_word_boundary_keeps_whole_title(self, generator):
        title = "Supercalifragilisticexpialidocious Adventure"
        result = generator._optimize_title(title, limit=70, target_range=(5, 20))
        assert result == title
        assert len(result) > 20
        assert "Supercalifragilisticexpialidocious" in result

    def test_cut_that_overshoots_below_min_is_rejected(self, generator):
        title = "Alpha Beta Gammagammagammagamma Delta"
        result = generator._optimize_title(title, limit=70, target_range=(25, 28))
        assert result == title
        assert not result.endswith("...")

    def test_short_title_is_never_padded(self, generator):
        result = generator._optimize_title(
            "Short Title", limit=70, target_range=(50, 60)
        )
        assert result == "Short Title"


class _FakeAnalytics:
    def get_summary(self):
        return {"top_performers": []}


class _RecordingManager:
    """Minimal manager double recording what optimize_metadata passes on."""

    def __init__(self):
        self.analytics = _FakeAnalytics()
        self.generate_kwargs = None

    def get_metadata(self, content_id):
        return None

    def generate(self, **kwargs):
        self.generate_kwargs = kwargs
        return "metadata-sentinel"


class TestOptimizerPassesLearnedRange:
    def test_learned_range_reaches_generate(self):
        manager = _RecordingManager()
        optimizer = SEOOptimizer(manager)
        optimizer.optimize_metadata(content_type=ContentType.VIDEO, topic="A Topic")
        assert (
            manager.generate_kwargs["title_length_range"]
            == optimizer.get_optimal_title_length()
        )

    def test_caller_supplied_range_is_not_overwritten(self):
        manager = _RecordingManager()
        optimizer = SEOOptimizer(manager)
        optimizer.optimize_metadata(
            content_type=ContentType.VIDEO,
            topic="A Topic",
            title_length_range=(33, 44),
        )
        assert manager.generate_kwargs["title_length_range"] == (33, 44)
