"""
Unit tests for PodcastQualityTracker.

Tests episode validation against production standards.
"""

import json
from pathlib import Path

import pytest

from seo_manager.production.podcast_quality_tracker import (
    PodcastQualityTracker,
    QualityCheck,
    QualityReport,
)
from seo_manager.storage.event_store import SEOEventStore


@pytest.fixture
def brand_config():
    """Podcast brand configuration"""
    return {
        "brand_id": "examplepodcast",
        "production_standards": {
            "min_episode_duration_sec": 480,
            "max_episode_duration_sec": 1080,
            "target_duration_sec": 720,
            "video_resolution": [1920, 1080],
            "video_codec": "h264",
            "audio_sample_rate": 48000,
            "audio_codec": "aac",
            "audio_lufs_target": -16,
            "audio_lufs_tolerance": 1,
            "max_turn_words": 120,
            "avg_turn_words_target": 70,
            "min_segments": 3,
            "require_sponsor_breaks": True,
            "require_intro_music": True,
            "require_outro_fade": True,
            "require_chapters": True,
            "require_thumbnail": True,
            "require_metadata": True,
            "require_model_provenance": True,
            "forbid_mock_content": True,
        },
    }


@pytest.fixture
def event_store(tmp_path):
    """Create event store"""
    return SEOEventStore(tmp_path / "events")


@pytest.fixture
def tracker(brand_config, event_store):
    """Create quality tracker"""
    return PodcastQualityTracker(brand_config, event_store)


@pytest.fixture
def valid_transcript(tmp_path):
    """Valid episode transcript"""
    transcript_path = tmp_path / "transcript.json"
    data = {
        "episode_number": 1,
        "segments": [
            {"type": "intro", "turns": 2},
            {"type": "segment", "turns": 8, "topic": "AI Safety"},
            {"type": "sponsor_break", "sponsor_id": "cloudnine"},
            {"type": "segment", "turns": 6, "topic": "Model Training"},
            {"type": "outro", "turns": 2},
        ],
        "turns": [
            {
                "speaker": "Claude",
                "text": "Welcome to Example Podcast!",
                "word_count": 5,
                "model": "anthropic/claude-sonnet-4-5",
            },
            {
                "speaker": "Geppetto",
                "text": "Great to be here!",
                "word_count": 4,
                "model": "openai/gpt-4o",
            },
            # ... more turns with avg ~70 words
        ]
        * 10,  # Simulate 20 turns
        "total_words": 1400,
        "avg_turn_words": 70,
        "max_turn_words": 100,
        "has_sponsor_breaks": True,
        "has_model_provenance": True,
        "is_mock": False,
    }

    with open(transcript_path, "w") as f:
        json.dump(data, f)

    return transcript_path


@pytest.fixture
def valid_episode_file(tmp_path):
    """Mock valid episode video file"""
    # In real tests, this would be actual video file
    # For now, return a mock metadata file
    metadata_path = tmp_path / "episode_metadata.json"
    metadata = {
        "duration_sec": 720,
        "video_resolution": [1920, 1080],
        "video_codec": "h264",
        "audio_sample_rate": 48000,
        "audio_codec": "aac",
        "audio_lufs": -16.2,
        "has_intro_music": True,
        "has_outro_fade": True,
        "metadata_exists": True,
        "chapters_exist": True,
        "thumbnail_exists": True,
    }

    with open(metadata_path, "w") as f:
        json.dump(metadata, f)

    return metadata_path


def test_validate_episode_catches_short_duration(
    tracker, valid_episode_file, valid_transcript, tmp_path
):
    """Test that validation catches episodes that are too short"""
    # Modify metadata to be too short
    with open(valid_episode_file) as f:
        metadata = json.load(f)

    metadata["duration_sec"] = 300  # 5 minutes (below 480 min)

    with open(valid_episode_file, "w") as f:
        json.dump(metadata, f)

    report = tracker.validate_episode(valid_episode_file, valid_transcript)

    assert not report.passed
    assert any(check.name == "duration" and not check.passed for check in report.checks)


def test_validate_episode_catches_wrong_sample_rate(
    tracker, valid_episode_file, valid_transcript
):
    """Test that validation catches wrong audio sample rate"""
    with open(valid_episode_file) as f:
        metadata = json.load(f)

    metadata["audio_sample_rate"] = 24000  # Wrong sample rate

    with open(valid_episode_file, "w") as f:
        json.dump(metadata, f)

    report = tracker.validate_episode(valid_episode_file, valid_transcript)

    assert not report.passed
    assert any(
        check.name == "audio_sample_rate" and not check.passed
        for check in report.checks
    )


def test_validate_episode_catches_long_turns(
    tracker, valid_episode_file, valid_transcript
):
    """Test that validation catches turns that are too long"""
    with open(valid_transcript) as f:
        transcript = json.load(f)

    transcript["max_turn_words"] = 200  # Way over limit

    with open(valid_transcript, "w") as f:
        json.dump(transcript, f)

    report = tracker.validate_episode(valid_episode_file, valid_transcript)

    assert not report.passed
    assert any(
        check.name == "max_turn_length" and not check.passed for check in report.checks
    )


def test_validate_episode_catches_missing_metadata(
    tracker, valid_episode_file, valid_transcript
):
    """Test that validation catches missing metadata"""
    with open(valid_episode_file) as f:
        metadata = json.load(f)

    metadata["metadata_exists"] = False

    with open(valid_episode_file, "w") as f:
        json.dump(metadata, f)

    report = tracker.validate_episode(valid_episode_file, valid_transcript)

    assert not report.passed
    assert any(check.name == "metadata" and not check.passed for check in report.checks)


def test_validate_episode_passes_good_episode(
    tracker, valid_episode_file, valid_transcript
):
    """Test that validation passes a properly formatted episode"""
    report = tracker.validate_episode(valid_episode_file, valid_transcript)

    assert report.passed
    assert all(check.passed for check in report.checks)


def test_quality_trend_calculation(tracker, valid_episode_file, valid_transcript):
    """Test quality trend tracking over episodes"""
    # Validate episode 1
    report1 = tracker.validate_episode(valid_episode_file, valid_transcript)
    tracker.track_improvement(1, report1.metrics)

    # Simulate improvement in episode 2
    with open(valid_transcript) as f:
        transcript = json.load(f)

    transcript["avg_turn_words"] = 65  # Better (closer to target of 70)

    with open(valid_transcript, "w") as f:
        json.dump(transcript, f)

    report2 = tracker.validate_episode(valid_episode_file, valid_transcript)
    tracker.track_improvement(2, report2.metrics)

    # Get trend
    trend = tracker.get_quality_trend(last_n_episodes=2)

    assert len(trend) == 2
    assert trend[0]["episode"] == 1
    assert trend[1]["episode"] == 2
    assert "avg_turn_words" in trend[0]["metrics"]


def test_validate_episode_checks_segment_count(
    tracker, valid_episode_file, valid_transcript
):
    """Test that validation requires minimum segments"""
    with open(valid_transcript) as f:
        transcript = json.load(f)

    transcript["segments"] = [
        {"type": "intro", "turns": 2},
        {"type": "outro", "turns": 2},
    ]  # Only 2 segments (need 3+)

    with open(valid_transcript, "w") as f:
        json.dump(transcript, f)

    report = tracker.validate_episode(valid_episode_file, valid_transcript)

    assert not report.passed
    assert any(
        check.name == "min_segments" and not check.passed for check in report.checks
    )


def test_validate_episode_requires_sponsor_breaks(
    tracker, valid_episode_file, valid_transcript
):
    """Test that validation requires sponsor breaks"""
    with open(valid_transcript) as f:
        transcript = json.load(f)

    transcript["has_sponsor_breaks"] = False

    with open(valid_transcript, "w") as f:
        json.dump(transcript, f)

    report = tracker.validate_episode(valid_episode_file, valid_transcript)

    assert not report.passed
    assert any(
        check.name == "sponsor_breaks" and not check.passed for check in report.checks
    )


def test_validate_episode_forbids_mock_content(
    tracker, valid_episode_file, valid_transcript
):
    """Test that validation rejects mock content"""
    with open(valid_transcript) as f:
        transcript = json.load(f)

    transcript["is_mock"] = True

    with open(valid_transcript, "w") as f:
        json.dump(transcript, f)

    report = tracker.validate_episode(valid_episode_file, valid_transcript)

    assert not report.passed
    assert any(
        check.name == "no_mock_content" and not check.passed for check in report.checks
    )


def test_validate_episode_requires_model_provenance(
    tracker, valid_episode_file, valid_transcript
):
    """Test that validation requires model provenance"""
    with open(valid_transcript) as f:
        transcript = json.load(f)

    transcript["has_model_provenance"] = False

    with open(valid_transcript, "w") as f:
        json.dump(transcript, f)

    report = tracker.validate_episode(valid_episode_file, valid_transcript)

    assert not report.passed
    assert any(
        check.name == "model_provenance" and not check.passed for check in report.checks
    )
