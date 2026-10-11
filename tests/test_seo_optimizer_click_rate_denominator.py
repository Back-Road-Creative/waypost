"""A click rate never pools across denominators.

`metric_source` exists because YouTube has two click rates that answer
different questions. `cardClickRate` asks: of the viewers ALREADY watching,
how many clicked the in-player card. `impressionClickThroughRate` asks: of the
people shown the thumbnail, how many clicked. They are not the same quantity
and 0.02 does not mean the same thing in each.

`youtube_sync`, `tracker` and `thumbnail_ab_tracker` all carry `ctr_source`
with the rate. `seo_optimizer` was the one consumer that did not read it. Its
own docstring promises the click rate is "NOT evidence that a title or
thumbnail won anyone's click" -- but nothing enforced that promise: every rate
went into one `max_click_rate` normalization base and one blended score
regardless of what it was a rate OF. A Studio-CSV thumbnail CTR landing beside
card rates would be normalized against them and averaged into them, and the
number that came out would measure nothing at all.

These make the pooling structurally impossible rather than promised in a
comment: rates normalize within their own denominator, and a pattern whose
samples disagree reports no rate rather than a blended fiction.
"""

from types import SimpleNamespace

import pytest

from seo_manager.analytics import metric_source
from seo_manager.analytics.seo_optimizer import MIXED_DENOMINATORS, SEOOptimizer

CARD = metric_source.YOUTUBE_CARD_CLICK_RATE
THUMB = metric_source.YOUTUBE_THUMBNAIL_IMPRESSION_CTR


def _performer(content_id, views, ctr, source, tags=("travel",), title="A title here"):
    return (
        {
            "content_id": content_id,
            "views": views,
            "ctr": ctr,
            "ctr_source": source,
            "last_updated": "2026-09-01T00:00:00",
        },
        SimpleNamespace(title=title, tags=list(tags), keywords=[], quality_score=None),
    )


class _Manager:
    def __init__(self, performers):
        self._meta = {p["content_id"]: m for p, m in performers}
        self.analytics = SimpleNamespace(
            get_summary=lambda: {"top_performers": [p for p, _ in performers]}
        )

    def get_metadata(self, content_id):
        return self._meta.get(content_id)


def _optimizer(*performers):
    return SEOOptimizer(_Manager(list(performers)))


def _by_type(patterns, kind):
    return [p for p in patterns[kind]]


def test_a_pattern_records_the_denominator_its_rate_came_from(_reset=None):
    opt = _optimizer(_performer("a", 1000, 0.05, CARD))

    patterns = opt.analyze_patterns(min_views=1)

    assert _by_type(patterns, "title_lengths")[0].ctr_source == CARD


def test_a_pattern_with_no_rate_claims_no_denominator():
    opt = _optimizer(_performer("a", 1000, None, metric_source.UNKNOWN))

    patterns = opt.analyze_patterns(min_views=1)

    pattern = _by_type(patterns, "title_lengths")[0]
    assert pattern.avg_ctr is None
    assert pattern.ctr_source == metric_source.UNKNOWN


def test_a_tag_whose_samples_share_one_denominator_still_averages():
    """The accept case. Nothing here may stop the optimizer working on the
    only kind of data it has ever actually had."""
    opt = _optimizer(
        _performer("a", 1000, 0.04, CARD),
        _performer("b", 1000, 0.06, CARD),
    )

    patterns = opt.analyze_patterns(min_views=1)

    tag = _by_type(patterns, "tags")[0]
    assert tag.avg_ctr == pytest.approx(0.05)
    assert tag.ctr_sample_size == 2
    assert tag.ctr_source == CARD


def test_a_tag_whose_samples_disagree_reports_no_rate_at_all():
    """0.04 of one thing and 0.06 of another do not average to 0.05 of
    anything. Refusing is the only honest answer -- and it is the same rule
    the optimizer already applies to an absent rate: never substitute a number."""
    opt = _optimizer(
        _performer("a", 1000, 0.04, CARD),
        _performer("b", 1000, 0.06, THUMB),
    )

    patterns = opt.analyze_patterns(min_views=1)

    tag = _by_type(patterns, "tags")[0]
    assert tag.avg_ctr is None
    assert tag.ctr_sample_size == 0
    assert tag.ctr_source == MIXED_DENOMINATORS


def test_a_mixed_tag_still_reports_its_views_and_sample_size():
    """Refusing the rate must not throw away the rest of the pattern."""
    opt = _optimizer(
        _performer("a", 1000, 0.04, CARD),
        _performer("b", 2000, 0.06, THUMB),
    )

    patterns = opt.analyze_patterns(min_views=1)

    tag = _by_type(patterns, "tags")[0]
    assert tag.sample_size == 2
    assert tag.avg_views == pytest.approx(1500)


def test_a_rate_is_normalized_against_its_own_denominator():
    """The blended score divides each rate by the largest rate of the SAME
    kind. Pooled, the thumbnail rate below would set the base for the card
    rates and crush them toward zero -- a ranking artifact of nothing but
    which denominators happened to be present."""
    pooled = _optimizer(
        _performer("a", 1000, 0.05, CARD),
        _performer("b", 1000, 0.50, THUMB),
    )
    alone = _optimizer(_performer("a", 1000, 0.05, CARD))

    with_thumb = _by_type(pooled.analyze_patterns(min_views=1), "title_lengths")
    without = _by_type(alone.analyze_patterns(min_views=1), "title_lengths")

    scored = {p.value: p.perf_score for p in with_thumb}
    assert scored[len("A title here")] == pytest.approx(without[0].perf_score)


def test_an_unknown_denominator_is_its_own_group_not_a_wildcard():
    """UNKNOWN means "nobody declared", which is not evidence that it matches
    whatever else is here. It must not silently join the card cohort."""
    opt = _optimizer(
        _performer("a", 1000, 0.04, CARD),
        _performer("b", 1000, 0.06, metric_source.UNKNOWN),
    )

    patterns = opt.analyze_patterns(min_views=1)

    tag = _by_type(patterns, "tags")[0]
    assert tag.avg_ctr is None
    assert tag.ctr_source == MIXED_DENOMINATORS


def test_a_prediction_says_which_denominator_it_is_predicting():
    opt = _optimizer(
        _performer("a", 1000, 0.04, CARD),
        _performer("b", 1000, 0.06, CARD),
    )
    opt.analyze_patterns(min_views=1)

    predictions = opt.predict_performance(
        SimpleNamespace(
            title="A title here", tags=["travel"], keywords=[], quality_score=0.8
        )
    )

    assert predictions["estimated_ctr_source"] == CARD


def test_a_prediction_over_disagreeing_patterns_estimates_no_rate():
    opt = _optimizer(
        _performer("a", 1000, 0.04, CARD, title="Short one"),
        _performer("b", 1000, 0.06, THUMB, title="A much longer title here"),
    )
    opt.analyze_patterns(min_views=1)

    predictions = opt.predict_performance(
        SimpleNamespace(
            title="A title here", tags=["travel"], keywords=[], quality_score=0.8
        )
    )

    assert predictions["estimated_ctr_sample_size"] == 0
    assert predictions["estimated_ctr_source"] == MIXED_DENOMINATORS


def test_a_prediction_with_no_rates_at_all_says_unknown_not_mixed():
    """ "Nobody measured" and "two different things were measured" are
    different answers and the screen has to be able to tell them apart."""
    opt = _optimizer(_performer("a", 1000, None, metric_source.UNKNOWN))
    opt.analyze_patterns(min_views=1)

    predictions = opt.predict_performance(
        SimpleNamespace(
            title="A title here", tags=["travel"], keywords=[], quality_score=0.8
        )
    )

    assert predictions["estimated_ctr_sample_size"] == 0
    assert predictions["estimated_ctr_source"] == metric_source.UNKNOWN
