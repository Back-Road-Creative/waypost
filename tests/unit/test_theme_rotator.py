"""Tests for ThemeRotator with voice style A/B testing."""

import json
from pathlib import Path

import pytest

from seo_manager.analytics.theme_rotator import (
    ContentTheme,
    ThemeRotator,
)


class TestVoiceStyleEnums:
    def test_voice_styles_exist(self):
        assert ContentTheme.VOICE_FIRST_PERSON.value == "voice_first_person"
        assert ContentTheme.VOICE_MINIMAL_ZEN.value == "voice_minimal_zen"
        assert ContentTheme.VOICE_ENGAGING.value == "voice_engaging"

    def test_video_themes_unchanged(self):
        assert ContentTheme.GUESS_LOCATION.value == "guess_location"
        assert ContentTheme.POV_DRIVE.value == "pov_drive"
        assert ContentTheme.HIDDEN_GEM.value == "hidden_gem"


class TestThemeRotatorCategory:
    @pytest.fixture
    def rotator(self, tmp_path):
        return ThemeRotator(state_dir=tmp_path, strategy="explore")

    def test_photo_category_returns_voice_themes(self, rotator):
        theme = rotator.get_next_theme(category="photo")
        assert theme.value.startswith("voice_")

    def test_video_category_returns_video_themes(self, rotator):
        theme = rotator.get_next_theme(category="video")
        assert not theme.value.startswith("voice_")

    def test_photo_themes_rotate(self, rotator):
        themes = set()
        for _ in range(6):
            theme = rotator.get_next_theme(category="photo", exclude_recent=0)
            themes.add(theme)
            rotator.record_usage(
                content_id=f"test-{len(themes)}",
                video_id=f"vid-{len(themes)}",
                theme=theme,
                category="photo",
            )
        # Should see all 3 voice styles used
        assert len(themes) == 3

    def test_video_themes_exclude_voice(self, rotator):
        for _ in range(20):
            theme = rotator.get_next_theme(category="video", exclude_recent=0)
            assert not theme.value.startswith("voice_")

    def test_record_usage_persists(self, tmp_path):
        rotator = ThemeRotator(state_dir=tmp_path, strategy="explore")
        rotator.record_usage(
            content_id="photo-batch-1",
            video_id="week-2026-02-23",
            theme=ContentTheme.VOICE_FIRST_PERSON,
            category="photo",
        )
        assert rotator.theme_counts[ContentTheme.VOICE_FIRST_PERSON] == 1

        # Reload from disk
        rotator2 = ThemeRotator(state_dir=tmp_path, strategy="explore")
        assert rotator2.theme_counts[ContentTheme.VOICE_FIRST_PERSON] == 1


class TestThemeAnalysisCategory:
    def test_photo_analysis_filters_voice_themes(self, tmp_path):
        rotator = ThemeRotator(state_dir=tmp_path, strategy="explore")

        # Record video usage
        for i in range(5):
            rotator.record_usage(
                content_id=f"video-{i}",
                video_id=f"vid-{i}",
                theme=ContentTheme.GUESS_LOCATION,
            )
            rotator.update_performance(f"video-{i}", views=1000)

        # Record photo usage
        for i in range(5):
            rotator.record_usage(
                content_id=f"photo-{i}",
                video_id=f"photo-vid-{i}",
                theme=ContentTheme.VOICE_FIRST_PERSON,
                category="photo",
            )
            rotator.update_performance(f"photo-{i}", views=500)

        # Photo analysis should only include voice themes
        photo_analysis = rotator.get_theme_analysis(category="photo")
        for theme in photo_analysis:
            assert theme.value.startswith("voice_")

        # Video analysis should only include non-voice themes
        video_analysis = rotator.get_theme_analysis(category="video")
        for theme in video_analysis:
            assert not theme.value.startswith("voice_")


class TestUnmeasuredCtrIsNotZero:
    """A rate whose denominator was never observed is unknown, not 0.0.

    Metric identity rule: an unknown is never averaged
    in as a zero; a measured 0.0 is evidence and still counts.
    """

    @staticmethod
    def _three_videos(rotator, ctrs):
        for i, ctr in enumerate(ctrs):
            rotator.record_usage(
                content_id=f"video-{i}",
                video_id=f"vid-{i}",
                theme=ContentTheme.POV_DRIVE,
            )
            if ctr is None:
                rotator.update_performance(f"video-{i}", views=1000)
            else:
                rotator.update_performance(f"video-{i}", views=1000, ctr=ctr)

    def test_theme_nobody_measured_has_no_avg_ctr(self, tmp_path):
        rotator = ThemeRotator(state_dir=tmp_path, strategy="explore")
        self._three_videos(rotator, [None, None, None])

        analysis = rotator.get_theme_analysis(category="video")
        assert analysis[ContentTheme.POV_DRIVE].avg_ctr is None

        report = rotator.generate_report()
        assert "Avg CTR: not measured" in report
        assert "Avg CTR: 0.00%" not in report

    def test_measured_zero_ctr_is_averaged_in(self, tmp_path):
        rotator = ThemeRotator(state_dir=tmp_path, strategy="explore")
        # One measured zero, one measured 0.10, one never measured.
        self._three_videos(rotator, [0.0, 0.10, None])

        analysis = rotator.get_theme_analysis(category="video")
        assert analysis[ContentTheme.POV_DRIVE].avg_ctr == pytest.approx(0.05)

    def test_legacy_state_zero_ctr_with_no_impressions_loads_as_unknown(self, tmp_path):
        # A state file written before ctr could say "unknown": every
        # never-measured usage carries the fabricated 0.0.
        legacy = {
            "usage": {
                "old-unmeasured": {
                    "content_id": "old-unmeasured",
                    "video_id": "v1",
                    "theme": "pov_drive",
                    "created_at": "2026-01-01T00:00:00",
                    "impressions": 0,
                    "views": 1000,
                    "ctr": 0.0,
                },
                "old-measured-zero": {
                    "content_id": "old-measured-zero",
                    "video_id": "v2",
                    "theme": "pov_drive",
                    "created_at": "2026-01-01T00:00:00",
                    "impressions": 500,
                    "views": 1000,
                    "ctr": 0.0,
                },
            },
            "theme_counts": {"pov_drive": 2},
        }
        (tmp_path / "theme_rotation_state.json").write_text(json.dumps(legacy))

        rotator = ThemeRotator(state_dir=tmp_path, strategy="explore")
        assert rotator.usage["old-unmeasured"].ctr is None
        assert rotator.usage["old-measured-zero"].ctr == 0.0

    def test_unknown_ctr_survives_a_save_and_reload(self, tmp_path):
        rotator = ThemeRotator(state_dir=tmp_path, strategy="explore")
        rotator.record_usage("new", "v", ContentTheme.POV_DRIVE)
        rotator.update_performance("new", views=1000)

        reloaded = ThemeRotator(state_dir=tmp_path, strategy="explore")
        assert reloaded.usage["new"].ctr is None
