"""A YouTube card click rate cannot pick a thumbnail.

`cardClickRate` counts in-player info-card clicks by viewers already watching;
it cannot see thumbnails. `youtube_sync` fetched it and handed it downstream
as a generic `ctr`, and `ThumbnailABTracker` averaged those per style and
named a winner. These tests pin the refusal, its stated reason, and that an
unmeasured rate stays None rather than becoming a fabricated 0.
"""

import pytest

from seo_manager.analytics import metric_source
from seo_manager.analytics.thumbnail_ab_tracker import (
    ThumbnailABTracker,
    ThumbnailStyle,
)

CARD = metric_source.YOUTUBE_CARD_CLICK_RATE
THUMB = metric_source.YOUTUBE_THUMBNAIL_IMPRESSION_CTR


@pytest.fixture
def tracker(tmp_path):
    return ThumbnailABTracker(state_dir=tmp_path / "ab", min_sample_size=3)


def _seed(tracker, n, *, ctr, source, style=ThumbnailStyle.BOLD):
    """Record n thumbnails of one style, updated with one rate."""
    for i in range(n):
        cid = f"{style.value}-{i}"
        tracker.record_thumbnail(cid, f"vid-{cid}", style)
        tracker.update_performance(
            cid,
            impressions=5000,
            clicks=int(5000 * (ctr or 0)),
            views=1000,
            ctr=ctr,
            ctr_source=source,
        )


def test_card_sourced_rate_yields_no_recommendation_and_says_why(tracker):
    _seed(tracker, 6, ctr=0.08, source=CARD)

    rec = tracker.get_recommended_style()
    style, confidence, reason = rec.style, rec.confidence, rec.reason

    # The old code returned (BOLD, 0.0) — a winner from a metric that cannot
    # see thumbnails.
    assert style is None
    assert confidence == 0.0
    assert "thumbnail click-through" in reason and CARD in reason
    # Never averaged, never scored, never rendered as a winner.
    assert tracker.get_style_analysis() == {}
    assert "NO THUMBNAIL CLICK-THROUGH DATA" in tracker.generate_report().upper()


def test_untagged_rate_reads_as_unknown_not_as_thumbnail_data(tracker):
    _seed(tracker, 6, ctr=0.08, source=None)

    assert tracker.thumbnails["bold-0"].ctr_source == metric_source.UNKNOWN
    assert tracker.get_recommended_style().style is None
    assert not metric_source.is_thumbnail_click_through(metric_source.UNKNOWN)


def test_unmeasured_rate_stays_none_across_a_reload(tracker, tmp_path):
    """None means unmeasured; a 0.0 here is a fabricated measurement."""
    tracker.record_thumbnail("c1", "vid-1", ThumbnailStyle.BOLD)
    tracker.update_performance("c1", views=1000)
    reloaded = ThumbnailABTracker(state_dir=tmp_path / "ab", min_sample_size=3)

    assert tracker.thumbnails["c1"].ctr is None
    assert reloaded.thumbnails["c1"].ctr is None
    assert reloaded.thumbnails["c1"].ctr_source == metric_source.UNKNOWN


def test_null_rates_are_excluded_not_counted_as_zero(tracker):
    _seed(tracker, 4, ctr=0.10, source=THUMB)
    for i in range(2):
        tracker.record_thumbnail(f"n{i}", f"vid-n{i}", ThumbnailStyle.BOLD)
        tracker.update_performance(f"n{i}", impressions=5000, ctr_source=THUMB)

    analysis = tracker.get_style_analysis()[ThumbnailStyle.BOLD]

    assert analysis.sample_size == 4
    assert analysis.avg_ctr == pytest.approx(0.10)


def test_thumbnail_sourced_rate_still_recommends_a_style(tracker):
    _seed(tracker, 4, ctr=0.12, source=THUMB, style=ThumbnailStyle.BOLD)
    _seed(tracker, 4, ctr=0.03, source=CARD, style=ThumbnailStyle.MINIMAL)

    rec = tracker.get_recommended_style()
    style, confidence, reason = rec.style, rec.confidence, rec.reason

    # MINIMAL is card-sourced, so it is not even in the comparison.
    assert set(tracker.get_style_analysis()) == {ThumbnailStyle.BOLD}
    assert style is ThumbnailStyle.BOLD
    assert confidence > 0
    assert reason is None


def test_record_test_compare_selection_is_a_manual_record_never_fabricated(tracker):
    """Studio's Test & Compare has no API: the operator states, after
    uploading, which of the 3 prepared variants they used. Nothing here
    invents a CTR or a winner for it."""
    thumb = tracker.record_test_compare_selection(
        "journey-1",
        "vid-1",
        ThumbnailStyle.BOLD,
        prepared_styles=[
            ThumbnailStyle.MINIMAL,
            ThumbnailStyle.BOLD,
            ThumbnailStyle.IMMERSIVE,
        ],
        manifest_path="/tmp/tc/test_compare_manifest.json",
    )

    assert thumb.style is ThumbnailStyle.BOLD
    assert thumb.ctr is None  # unmeasured until an Analytics sync writes one
    assert thumb.prepared_variants == ["minimal", "bold", "immersive"]
    assert thumb.manifest_path == "/tmp/tc/test_compare_manifest.json"
    # The manual record goes through the same record_thumbnail path, so it
    # is visible to the ordinary tracker state.
    assert tracker.thumbnails["journey-1"] is thumb


def test_get_test_compare_outcome_reuses_the_update_performance_read_path(tracker):
    """Reading the outcome reuses whatever Analytics read path the tracker
    already has (update_performance / the thumbnails store) rather than
    adding a second one."""
    tracker.record_test_compare_selection("journey-2", "vid-2", ThumbnailStyle.MINIMAL)

    outcome = tracker.get_test_compare_outcome("journey-2")
    assert outcome.ctr is None
    assert outcome.ctr_source == metric_source.UNKNOWN

    # The same update_performance path any Analytics sync uses.
    tracker.update_performance(
        "journey-2", impressions=5000, clicks=400, ctr=0.08, ctr_source=THUMB
    )

    outcome = tracker.get_test_compare_outcome("journey-2")
    assert outcome.ctr == pytest.approx(0.08)
    assert outcome.ctr_source == THUMB


def test_get_test_compare_outcome_unknown_content_id_is_none(tracker):
    assert tracker.get_test_compare_outcome("does-not-exist") is None
