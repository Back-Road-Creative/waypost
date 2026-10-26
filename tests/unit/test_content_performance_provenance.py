"""A per-video click rate says what it measured, or says nothing.

`_get_top_performers` was fixed to preserve nulls and carry `ctr_source`: a
rate with no denominator is meaningless, and a `0` reads as the real
measurement "nobody clicked". `get_content_performance` -- the per-video read,
and the one a console Insights screen calls -- kept the old shape: it computed
`clicks / impressions if impressions else 0` and dropped the provenance
entirely. So the two readers of the same events disagreed about the same video:
one reported "unmeasured", the other "0%, source unstated".

The fix is one shared resolver rather than the same rule written twice, so the
two cannot drift again. It also names the denominator (`ctr_basis`): a rate
recomputed from aggregated impressions and a rate a platform reported are
different quantities, and a screen that shows them in one column without
saying which is the pooling this service already refused elsewhere.
"""

import pytest

from seo_manager.analytics import metric_source
from seo_manager.analytics.tracker import AnalyticsTracker
from seo_manager.storage.event_store import SEOEventStore


@pytest.fixture
def tracker(tmp_path):
    return AnalyticsTracker(
        store=SEOEventStore(storage_path=tmp_path), site_id="test_site"
    )


def test_a_rate_with_no_denominator_is_none_not_zero(tracker):
    """The defect. `0` is a measurement -- it says every viewer shown this
    declined to click. "We have no impressions for it" is not that."""
    tracker.track_performance("video_1", views=100)

    performance = tracker.get_content_performance("video_1")

    assert performance["ctr"] is None
    assert performance["ctr_basis"] is None


def test_a_reported_rate_keeps_its_provenance(tracker):
    tracker.track_performance(
        "video_1", views=100, ctr=0.02, ctr_source=metric_source.YOUTUBE_CARD_CLICK_RATE
    )

    performance = tracker.get_content_performance("video_1")

    assert performance["ctr"] == 0.02
    assert performance["ctr_source"] == metric_source.YOUTUBE_CARD_CLICK_RATE
    assert performance["ctr_basis"] == "reported_rate"


def test_an_undeclared_source_is_unknown_never_thumbnail(tracker):
    """The allowlist rule, restated at this reader: a rate is thumbnail
    evidence only by declaring itself so."""
    tracker.track_performance("video_1", views=100, ctr=0.02)

    performance = tracker.get_content_performance("video_1")

    assert performance["ctr_source"] == metric_source.UNKNOWN
    assert not metric_source.is_thumbnail_click_through(performance["ctr_source"])


def test_a_recomputed_rate_says_it_was_recomputed(tracker):
    """Aggregated clicks over aggregated impressions is a different quantity
    from a rate a platform handed us, so it is labelled differently even when
    both are called `ctr`."""
    tracker.track_performance("video_1", views=100, clicks=5, impressions=500)
    tracker.track_performance("video_1", views=150, clicks=8, impressions=600)

    performance = tracker.get_content_performance("video_1")

    # The latest snapshot (8 over 600), not the sum of both: the counts are
    # lifetime. 4 dp, the precision `_get_top_performers` has always used: the
    # two readers have to agree to the digit, not merely approximately.
    assert performance["ctr"] == round(8 / 600, 4)
    assert performance["ctr_basis"] == "clicks_over_impressions"


def test_counted_impressions_win_over_a_reported_rate(tracker):
    """Same precedence the top-performers list already uses: a rate we can
    compute from counts beats one we were told."""
    tracker.track_performance(
        "video_1",
        views=100,
        clicks=10,
        impressions=200,
        ctr=0.99,
        ctr_source=metric_source.YOUTUBE_CARD_CLICK_RATE,
    )

    performance = tracker.get_content_performance("video_1")

    assert performance["ctr"] == pytest.approx(0.05, rel=1e-4)
    assert performance["ctr_basis"] == "clicks_over_impressions"


def test_the_reader_reports_when_it_last_heard_anything(tracker):
    """Freshness, which a screen needs to say whether a number is current. It
    is the newest event's own timestamp, never the time of the read."""
    tracker.track_performance("video_1", views=100, clicks=5, impressions=500)

    performance = tracker.get_content_performance("video_1")

    assert performance["last_updated"] is not None
    assert performance["data_points"] == 1


def test_no_data_still_says_why_and_carries_the_keys(tracker):
    """A screen must be able to render the absent case without a KeyError, and
    the reason has to be readable rather than inferred from a missing name."""
    performance = tracker.get_content_performance("nonexistent")

    assert performance["content_id"] == "nonexistent"
    assert performance["error"]
    assert performance["ctr"] is None
    assert performance["ctr_source"] == metric_source.UNKNOWN
    assert performance["ctr_basis"] is None
    assert performance["data_points"] == 0


def test_both_readers_of_the_same_events_agree(tracker):
    """The point of sharing one resolver. These two aggregate the same events
    for the same video; before this they disagreed about whether its rate was
    `None` or `0`."""
    tracker.track_performance("video_1", views=100)

    per_video = tracker.get_content_performance("video_1")
    top = tracker.get_summary()["top_performers"]
    listed = next(row for row in top if row["content_id"] == "video_1")

    assert per_video["ctr"] == listed["ctr"]
    assert per_video["ctr_source"] == listed["ctr_source"]
