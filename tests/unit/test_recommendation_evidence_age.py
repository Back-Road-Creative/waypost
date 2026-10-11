"""A recommendation carries the age of the data behind it.

`generate_report` stamped `datetime.now()` and called it "Generated:", so a
reader saw today's date above a "RECOMMENDATION:" computed from samples that
could be four months old, from an account that stopped reporting in June.
The timestamp measured when the report was printed, not when anything was
learned.

These tests pin the four outcomes: a fresh winner is unchanged, a stale one
comes back as the last VALID recommendation *labelled with its age*, nothing
measured is its own explicit outcome rather than an arbitrary first entry,
and the return types make it impossible to hold a winner without its age.
"""

import dataclasses
from datetime import datetime, timedelta

import pytest

from seo_manager.analytics import metric_source
from seo_manager.analytics.evidence_age import STALE_AFTER_DAYS, EvidenceState
from seo_manager.analytics.theme_rotator import (
    ContentTheme,
    ThemeRotator,
    ThemeSelection,
)
from seo_manager.analytics.thumbnail_ab_tracker import (
    StyleRecommendation,
    ThumbnailABTracker,
    ThumbnailStyle,
)

THUMB = metric_source.YOUTUBE_THUMBNAIL_IMPRESSION_CTR
LONG_AGO_DAYS = STALE_AFTER_DAYS + 90


def _backdate(record, days):
    record.last_updated = (datetime.now() - timedelta(days=days)).isoformat()
    return record.last_updated


def _seed_tracker(tracker, n=4, *, ctr=0.12, style=ThumbnailStyle.BOLD):
    for i in range(n):
        cid = f"{style.value}-{i}"
        tracker.record_thumbnail(cid, f"vid-{cid}", style)
        tracker.update_performance(
            cid,
            impressions=5000,
            clicks=int(5000 * ctr),
            views=1000,
            ctr=ctr,
            ctr_source=THUMB,
        )


def _seed_rotator(rotator, n=4, *, views=1000, theme=ContentTheme.POV_DRIVE):
    for i in range(n):
        cid = f"{theme.value}-{i}"
        rotator.record_usage(cid, f"vid-{cid}", theme)
        rotator.update_performance(cid, views=views, ctr=0.05)


@pytest.fixture
def tracker(tmp_path):
    return ThumbnailABTracker(state_dir=tmp_path / "ab", min_sample_size=3)


@pytest.fixture
def rotator(tmp_path):
    return ThemeRotator(state_dir=tmp_path / "themes", strategy="exploit")


class TestThumbnailRecommendationAge:
    def test_fresh_well_sampled_winner_is_unchanged(self, tracker):
        _seed_tracker(tracker)

        rec = tracker.get_recommended_style()

        assert rec.style is ThumbnailStyle.BOLD
        assert rec.confidence > 0
        assert rec.reason is None
        assert rec.evidence is EvidenceState.FRESH
        assert rec.sample_size == 4
        assert rec.evidence_age_days == pytest.approx(0, abs=1)

    def test_stale_evidence_still_names_the_same_winner_labelled_with_its_age(
        self, tracker
    ):
        _seed_tracker(tracker)
        fresh = tracker.get_recommended_style()
        for thumb in tracker.thumbnails.values():
            _backdate(thumb, LONG_AGO_DAYS)

        stale = tracker.get_recommended_style()

        # The last VALID recommendation survives — refusing to answer and
        # inventing a fresh answer are both wrong.
        assert stale.style is fresh.style
        assert stale.sample_size == fresh.sample_size
        assert stale.evidence is EvidenceState.STALE
        assert stale.is_stale
        assert stale.evidence_age_days == pytest.approx(LONG_AGO_DAYS, abs=1)
        assert "stale" in stale.reason and "days old" in stale.reason

    def test_no_samples_is_its_own_outcome_not_a_zero_ctr_winner(self, tracker):
        rec = tracker.get_recommended_style()

        assert rec.evidence is EvidenceState.NOTHING_MEASURED
        assert rec.style is None
        assert rec.sample_size == 0
        assert rec.evidence_age_days is None
        assert rec.evidence_newest_at is None
        assert rec.reason

    def test_some_but_too_few_samples_is_distinct_from_nothing_measured(self, tracker):
        _seed_tracker(tracker, n=2)

        rec = tracker.get_recommended_style()

        assert rec.evidence is EvidenceState.INSUFFICIENT
        assert rec.style is None
        assert rec.sample_size == 2

    def test_undated_evidence_is_not_reported_as_fresh(self, tracker):
        _seed_tracker(tracker)
        for thumb in tracker.thumbnails.values():
            thumb.last_updated = None

        rec = tracker.get_recommended_style()

        assert rec.style is ThumbnailStyle.BOLD
        assert rec.evidence is EvidenceState.UNDATED
        assert rec.evidence_age_days is None

    def test_a_winner_cannot_be_obtained_without_its_age(self):
        fields = {f.name for f in dataclasses.fields(StyleRecommendation)}
        assert {
            "style",
            "evidence",
            "evidence_age_days",
            "evidence_newest_at",
            "sample_size",
        } <= fields
        # Every field is required: no call site can omit the age.
        assert not any(
            f.default is not dataclasses.MISSING
            or f.default_factory is not dataclasses.MISSING
            for f in dataclasses.fields(StyleRecommendation)
        )
        with pytest.raises(TypeError):
            StyleRecommendation(style=ThumbnailStyle.BOLD, confidence=1.0)

    def test_the_recommendation_is_frozen(self, tracker):
        _seed_tracker(tracker)
        rec = tracker.get_recommended_style()
        with pytest.raises(dataclasses.FrozenInstanceError):
            rec.evidence_age_days = 0.0

    def test_report_prints_each_styles_own_last_updated(self, tracker):
        _seed_tracker(tracker)
        stamp = None
        for thumb in tracker.thumbnails.values():
            stamp = _backdate(thumb, LONG_AGO_DAYS)

        report = tracker.generate_report()

        assert stamp in report
        assert "days old" in report
        # The report's own clock is labelled as the print time.
        assert "not data time" in report
        assert "STALE" in report.upper()

    def test_performance_summary_carries_age_beside_every_rate(self, tracker):
        _seed_tracker(tracker)

        summary = tracker.get_style_performance_summary()["bold"]

        assert summary["avg_ctr"] == pytest.approx(0.12)
        assert summary["sample_size"] == 4
        assert summary["last_updated"]
        assert summary["age_days"] == pytest.approx(0, abs=1)


class TestThemeSelectionAge:
    def test_best_performer_selection_carries_its_evidence(self, rotator):
        _seed_rotator(rotator)

        selection = rotator.get_next_theme_with_evidence(exclude_recent=0)

        assert isinstance(selection, ThemeSelection)
        assert selection.theme is ContentTheme.POV_DRIVE
        assert selection.basis == "performance"
        assert selection.sample_size == 4
        assert selection.evidence is EvidenceState.FRESH
        assert selection.evidence_age_days == pytest.approx(0, abs=1)

    def test_stale_evidence_still_selects_the_same_theme_labelled(self, rotator):
        _seed_rotator(rotator)
        fresh = rotator.get_next_theme_with_evidence(exclude_recent=0)
        for usage in rotator.usage.values():
            _backdate(usage, LONG_AGO_DAYS)

        stale = rotator.get_next_theme_with_evidence(exclude_recent=0)

        assert stale.theme is fresh.theme
        assert stale.evidence is EvidenceState.STALE
        assert stale.evidence_age_days == pytest.approx(LONG_AGO_DAYS, abs=1)

    def test_selection_with_no_data_is_exploration_not_a_measured_best(self, rotator):
        selection = rotator.get_next_theme_with_evidence(exclude_recent=0)

        assert selection.basis == "least_used"
        assert selection.evidence is EvidenceState.NOTHING_MEASURED
        assert selection.sample_size == 0
        assert selection.evidence_age_days is None

    def test_a_theme_selection_cannot_be_obtained_without_its_age(self):
        fields = {f.name for f in dataclasses.fields(ThemeSelection)}
        assert {
            "theme",
            "basis",
            "evidence",
            "evidence_age_days",
            "evidence_newest_at",
            "sample_size",
        } <= fields
        assert not any(
            f.default is not dataclasses.MISSING
            or f.default_factory is not dataclasses.MISSING
            for f in dataclasses.fields(ThemeSelection)
        )
        with pytest.raises(TypeError):
            ThemeSelection(theme=ContentTheme.POV_DRIVE, basis="performance")

    def test_get_next_theme_still_returns_a_bare_theme(self, rotator):
        _seed_rotator(rotator)
        assert rotator.get_next_theme(exclude_recent=0) is ContentTheme.POV_DRIVE

    def test_nothing_measured_yields_no_ranking_not_an_arbitrary_default(self, rotator):
        # The old code returned three hard-coded themes with no analysis —
        # an arbitrary first entry rendered as "#1".
        assert rotator.get_top_themes(3) == []
        assert "NO THEME PERFORMANCE MEASURED" in rotator.generate_report().upper()

    def test_report_prints_each_themes_own_last_updated(self, rotator):
        _seed_rotator(rotator)
        stamp = None
        for usage in rotator.usage.values():
            stamp = _backdate(usage, LONG_AGO_DAYS)

        report = rotator.generate_report()

        assert stamp in report
        assert "days old" in report
        assert "not data time" in report
        assert "STALE" in report.upper()
