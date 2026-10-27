"""The "top patterns" a prediction is built from are actually the top ones.

`predict_performance` estimated views and the click rate from
`self.patterns["title_lengths"][:5]`, calling them "the top patterns". That
list was never ordered: `analyze_patterns` appends one pattern per content
item in content-iteration order and only ever sorted `tags` in place, so
`[:5]` took the first five videos the analytics summary happened to list --
an arbitrary sample -- while `get_optimal_title_length` did its own local
sort and was the only reader that got the top performers it asked for.

Every test below orders its content deliberately WORST-first, so the first
five patterns and the best five patterns are different sets and the old
behaviour and the new one cannot agree on a number.
"""

from datetime import datetime
from types import SimpleNamespace

import pytest

from seo_manager.analytics import metric_source
from seo_manager.analytics.seo_optimizer import SEOOptimizer

CARD = metric_source.YOUTUBE_CARD_CLICK_RATE

# All six performers share a publish date and a fresh `last_updated`, so the
# age terms cancel and perf_score tracks views (and, where present, the click
# rate) alone. That keeps "which five are the top five" a fact the test states
# rather than an artifact of when it ran.
_PUBLISHED = "2026-09-01T00:00:00"


def _performer(views, ctr=None):
    performer = {
        "content_id": f"v{views}",
        "views": views,
        "published_at": _PUBLISHED,
        "last_updated": datetime.now().isoformat(),
    }
    if ctr is not None:
        performer["ctr"] = ctr
        performer["ctr_source"] = CARD
    return performer


class _Manager:
    """Hands the optimizer its performers in exactly the order given."""

    def __init__(self, performers):
        self._meta = {
            p["content_id"]: SimpleNamespace(
                title="A title of a fixed length",
                tags=["travel"],
                keywords=[],
                quality_score=0.8,
            )
            for p in performers
        }
        self.analytics = SimpleNamespace(
            get_summary=lambda: {"top_performers": list(performers)}
        )

    def get_metadata(self, content_id):
        return self._meta.get(content_id)


def _candidate():
    return SimpleNamespace(
        title="A title of a fixed length",
        tags=["travel"],
        keywords=[],
        quality_score=0.8,
    )


def test_analyze_patterns_ranks_title_lengths_like_it_ranks_tags():
    """`tags` has always been sorted in place; `title_lengths` never was, so
    two lists documented as ranked by the same key disagreed about order."""
    opt = SEOOptimizer(
        _Manager([_performer(v) for v in (100, 200, 300, 400, 500, 600)])
    )

    patterns = opt.analyze_patterns(min_views=1)

    scores = [p.perf_score for p in patterns["title_lengths"]]
    assert scores == sorted(scores, reverse=True)
    assert [p.avg_views for p in patterns["title_lengths"]] == [
        600,
        500,
        400,
        300,
        200,
        100,
    ]


def test_the_views_estimate_averages_the_best_five_not_the_first_five():
    """Content order here is the exact reverse of performance order. The
    first five average 300 views; the best five average 400."""
    opt = SEOOptimizer(
        _Manager([_performer(v) for v in (100, 200, 300, 400, 500, 600)])
    )
    opt.analyze_patterns(min_views=1)

    predictions = opt.predict_performance(_candidate())

    assert predictions["evidence_sample_size"] == 6
    # the observed mean of the best five, unscaled by any invented factor
    assert predictions["estimated_views"] == pytest.approx(400)


def test_the_click_rate_estimate_averages_the_best_five_too():
    """Same shape one metric over: views and rate both climb with content
    order, so the worst five lead the list. Their rates average 0.03; the
    best five average 0.04."""
    opt = SEOOptimizer(
        _Manager(
            [
                _performer(views, ctr=rate)
                for views, rate in (
                    (100, 0.01),
                    (200, 0.02),
                    (300, 0.03),
                    (400, 0.04),
                    (500, 0.05),
                    (600, 0.06),
                )
            ]
        )
    )
    opt.analyze_patterns(min_views=1)

    predictions = opt.predict_performance(_candidate())

    assert predictions["estimated_ctr_sample_size"] == 5
    assert predictions["estimated_ctr"] == pytest.approx(0.04)
