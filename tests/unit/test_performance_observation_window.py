"""The age-normalization fallback the contract promises actually fires.

`seo_optimizer._views_per_day` normalizes views by days-since-publish so an
old video's stale lifetime total cannot outrank a new one that is converting
better right now. It prefers the event's `published_at` and, per its
own docstring, falls back to "`first_updated` -- the earliest
performance-update timestamp tracker._get_top_performers has seen for this
content_id".

That fallback was dead. `_views_per_day` reads `performer.get("first_updated")`
and no aggregate this tracker builds ever had that key: `_blank_aggregate`
carried `last_updated` and nothing else about time, and `_aggregate` only ever
moved the newest timestamp forward. `.get` returned `None` on every row, so
every video without a `published_at` fell through to RAW LIFETIME VIEWS --
exactly the ranking age normalization exists to remove, while the contract said it was
handled.

So the tracker emits the other end of the window it already half-had. Both
readers get it, from the shared `_aggregate`. It is the same timestamps
`last_updated` reads, compared rather than taken from store order (which is
not sorted), and `None` when no sample carried one -- substituting
`datetime.now()` would claim the sample was taken today and make an
unmeasured video look brand-new, which is the fabrication this tracker
refuses everywhere else.
"""

import pytest

from seo_manager.analytics import seo_optimizer
from seo_manager.analytics.tracker import AnalyticsTracker
from seo_manager.core.events import EventType, SEOEvent
from seo_manager.storage.event_store import SEOEventStore


@pytest.fixture
def tracker(tmp_path):
    return AnalyticsTracker(
        store=SEOEventStore(storage_path=tmp_path), site_id="test_site"
    )


def _record(tracker, content_id, when, **data):
    """One performance update at a chosen instant. `track_performance` stamps
    `datetime.now()`, so a test about elapsed time has to build the event."""
    tracker.store.append_event(
        SEOEvent(
            event_id=f"e-{content_id}-{when}",
            event_type=EventType.PERFORMANCE_UPDATED,
            site_id=tracker.site_id,
            content_id=content_id,
            timestamp=when,
            data=data,
        )
    )


def test_the_window_brackets_every_sample(tracker):
    """The defect: out of order on purpose, because the oldest is not simply
    the first row read any more than the newest is the last."""
    _record(tracker, "video_1", "2026-06-01T00:00:00", views=100)
    _record(tracker, "video_1", "2026-01-15T00:00:00", views=100)
    _record(tracker, "video_1", "2026-03-20T00:00:00", views=100)

    performance = tracker.get_content_performance("video_1")

    assert performance["first_updated"] == "2026-01-15T00:00:00"
    assert performance["last_updated"] == "2026-06-01T00:00:00"


def test_top_performers_carry_the_window_too(tracker):
    """Both readers of these events, or the screen that ranks by rate still
    cannot see the window the rate was earned over."""
    _record(
        tracker, "fast", "2026-09-01T00:00:00", views=900, clicks=40, impressions=1000
    )
    _record(
        tracker, "slow", "2024-09-01T00:00:00", views=900, clicks=40, impressions=1000
    )
    _record(tracker, "slow", "2026-09-01T00:00:00", views=1, clicks=0, impressions=0)

    rows = {row["content_id"]: row for row in tracker.get_summary()["top_performers"]}

    assert rows["fast"]["first_updated"] == "2026-09-01T00:00:00"
    assert rows["slow"]["first_updated"] == "2024-09-01T00:00:00"
    # Same rate, same sample count -- only the window separates them.
    assert rows["fast"]["ctr"] == rows["slow"]["ctr"]


def test_a_single_sample_has_a_window_of_one_instant(tracker):
    """Not `None`: we know exactly when the one observation happened. A
    zero-length window is a real fact about a brand-new video, and a consumer
    dividing by elapsed time needs to see it rather than guess."""
    _record(tracker, "video_1", "2026-09-01T00:00:00", views=100)

    performance = tracker.get_content_performance("video_1")

    assert performance["first_updated"] == performance["last_updated"]


def test_an_untimestamped_aggregate_reports_no_window(tracker):
    """An event store row with no timestamp is the one case with nothing to
    report. Substituting `datetime.now()` would claim the sample was taken
    today, which is the fabrication this tracker refuses elsewhere."""
    _record(tracker, "video_1", "", views=100)

    performance = tracker.get_content_performance("video_1")

    assert performance["first_updated"] is None
    assert performance["last_updated"] is None


def test_a_video_we_have_never_heard_of_has_no_window(tracker):
    performance = tracker.get_content_performance("nope")

    assert performance["first_updated"] is None
    assert performance["last_updated"] is None


def test_the_optimizer_s_age_normalization_finally_has_its_timestamp():
    """The consumer end, and the reason this matters. With the key absent
    `_views_per_day` returned raw lifetime views, so a two-year-old video with
    900 views outranked a week-old one with 500 -- the inversion age normalization was
    written to remove."""
    old = {"views": 900, "first_updated": "2024-09-01T00:00:00"}
    new = {"views": 500, "first_updated": "2026-09-05T00:00:00"}

    assert seo_optimizer._views_per_day(new) > seo_optimizer._views_per_day(old)
    # And the broken behaviour, stated so it cannot come back quietly: with no
    # timestamp on either row the ranking inverts.
    assert seo_optimizer._views_per_day({"views": 900}) > seo_optimizer._views_per_day(
        {"views": 500}
    )


def test_a_real_tracker_row_is_the_shape_the_optimizer_reads(tracker):
    """End to end, no hand-built dict: `_views_per_day` is handed an actual
    `top_performers` row, which is where the missing key bit."""
    _record(tracker, "video_1", "2025-09-01T00:00:00", views=730)

    row = tracker.get_summary()["top_performers"][0]

    assert row["first_updated"] == "2025-09-01T00:00:00"
    assert seo_optimizer._views_per_day(row) < row["views"]
