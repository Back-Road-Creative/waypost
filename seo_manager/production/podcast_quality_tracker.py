"""
Podcast Quality Tracker.

Track production quality metrics for podcast episodes.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


@dataclass
class QualityCheck:
    """Single quality check result"""

    name: str
    passed: bool
    actual_value: Any
    expected_value: Any
    message: str = ""


@dataclass
class QualityReport:
    """Complete quality validation report"""

    episode_path: str
    passed: bool
    checks: List[QualityCheck]
    metrics: Dict[str, Any]
    timestamp: str


class PodcastQualityTracker:
    """
    Track production quality metrics for podcast episodes.

    Validates episodes against production standards defined in brand config.
    """

    def __init__(self, brand_config: Dict[str, Any], event_store):
        """
        Initialize quality tracker.

        Args:
            brand_config: Brand configuration with production_standards
            event_store: SEO event store for tracking
        """
        self.brand_config = brand_config
        self.event_store = event_store
        self.standards = brand_config["production_standards"]
        self.brand_id = brand_config["brand_id"]

        # Track quality over time
        self.quality_history = []

    def validate_episode(
        self, episode_path: Path, transcript_path: Path
    ) -> QualityReport:
        """
        Run all quality checks against production standards.

        Args:
            episode_path: Path to episode video/metadata file
            transcript_path: Path to episode transcript JSON

        Returns:
            QualityReport with pass/fail for each check
        """
        # Load episode metadata and transcript
        with open(episode_path) as f:
            episode_meta = json.load(f)

        with open(transcript_path) as f:
            transcript = json.load(f)

        checks = []

        # Video duration check
        checks.append(
            QualityCheck(
                name="duration",
                passed=(
                    self.standards["min_episode_duration_sec"]
                    <= episode_meta["duration_sec"]
                    <= self.standards["max_episode_duration_sec"]
                ),
                actual_value=episode_meta["duration_sec"],
                expected_value=f"{self.standards['min_episode_duration_sec']}-{self.standards['max_episode_duration_sec']}",
                message=f"Duration: {episode_meta['duration_sec']}s",
            )
        )

        # Video resolution check
        checks.append(
            QualityCheck(
                name="video_resolution",
                passed=episode_meta["video_resolution"]
                == self.standards["video_resolution"],
                actual_value=episode_meta["video_resolution"],
                expected_value=self.standards["video_resolution"],
                message=f"Resolution: {episode_meta['video_resolution']}",
            )
        )

        # Video codec check
        checks.append(
            QualityCheck(
                name="video_codec",
                passed=episode_meta["video_codec"] == self.standards["video_codec"],
                actual_value=episode_meta["video_codec"],
                expected_value=self.standards["video_codec"],
            )
        )

        # Audio sample rate check
        checks.append(
            QualityCheck(
                name="audio_sample_rate",
                passed=episode_meta["audio_sample_rate"]
                == self.standards["audio_sample_rate"],
                actual_value=episode_meta["audio_sample_rate"],
                expected_value=self.standards["audio_sample_rate"],
                message=f"Sample rate: {episode_meta['audio_sample_rate']}Hz",
            )
        )

        # Audio codec check
        checks.append(
            QualityCheck(
                name="audio_codec",
                passed=episode_meta["audio_codec"] == self.standards["audio_codec"],
                actual_value=episode_meta["audio_codec"],
                expected_value=self.standards["audio_codec"],
            )
        )

        # LUFS check (within tolerance)
        lufs_min = (
            self.standards["audio_lufs_target"] - self.standards["audio_lufs_tolerance"]
        )
        lufs_max = (
            self.standards["audio_lufs_target"] + self.standards["audio_lufs_tolerance"]
        )
        checks.append(
            QualityCheck(
                name="audio_lufs",
                passed=lufs_min <= episode_meta["audio_lufs"] <= lufs_max,
                actual_value=episode_meta["audio_lufs"],
                expected_value=f"{lufs_min} to {lufs_max}",
                message=f"LUFS: {episode_meta['audio_lufs']}",
            )
        )

        # Turn length checks
        checks.append(
            QualityCheck(
                name="max_turn_length",
                passed=transcript["max_turn_words"] <= self.standards["max_turn_words"],
                actual_value=transcript["max_turn_words"],
                expected_value=f"<= {self.standards['max_turn_words']}",
                message=f"Max turn: {transcript['max_turn_words']} words",
            )
        )

        checks.append(
            QualityCheck(
                name="avg_turn_length",
                passed=abs(
                    transcript["avg_turn_words"]
                    - self.standards["avg_turn_words_target"]
                )
                <= 20,
                actual_value=transcript["avg_turn_words"],
                expected_value=f"~{self.standards['avg_turn_words_target']}",
                message=f"Avg turn: {transcript['avg_turn_words']} words",
            )
        )

        # Segment count check
        checks.append(
            QualityCheck(
                name="min_segments",
                passed=len(transcript["segments"]) >= self.standards["min_segments"],
                actual_value=len(transcript["segments"]),
                expected_value=f">= {self.standards['min_segments']}",
                message=f"Segments: {len(transcript['segments'])}",
            )
        )

        # Sponsor breaks check
        if self.standards["require_sponsor_breaks"]:
            checks.append(
                QualityCheck(
                    name="sponsor_breaks",
                    passed=transcript.get("has_sponsor_breaks", False),
                    actual_value=transcript.get("has_sponsor_breaks", False),
                    expected_value=True,
                    message="Sponsor breaks required",
                )
            )

        # Music checks
        if self.standards["require_intro_music"]:
            checks.append(
                QualityCheck(
                    name="intro_music",
                    passed=episode_meta.get("has_intro_music", False),
                    actual_value=episode_meta.get("has_intro_music", False),
                    expected_value=True,
                    message="Intro music required",
                )
            )

        if self.standards["require_outro_fade"]:
            checks.append(
                QualityCheck(
                    name="outro_fade",
                    passed=episode_meta.get("has_outro_fade", False),
                    actual_value=episode_meta.get("has_outro_fade", False),
                    expected_value=True,
                    message="Outro fade required",
                )
            )

        # Metadata checks
        if self.standards["require_metadata"]:
            checks.append(
                QualityCheck(
                    name="metadata",
                    passed=episode_meta.get("metadata_exists", False),
                    actual_value=episode_meta.get("metadata_exists", False),
                    expected_value=True,
                    message="YouTube metadata required",
                )
            )

        if self.standards["require_chapters"]:
            checks.append(
                QualityCheck(
                    name="chapters",
                    passed=episode_meta.get("chapters_exist", False),
                    actual_value=episode_meta.get("chapters_exist", False),
                    expected_value=True,
                    message="Chapters/timestamps required",
                )
            )

        if self.standards["require_thumbnail"]:
            checks.append(
                QualityCheck(
                    name="thumbnail",
                    passed=episode_meta.get("thumbnail_exists", False),
                    actual_value=episode_meta.get("thumbnail_exists", False),
                    expected_value=True,
                    message="Thumbnail required",
                )
            )

        # Content quality checks
        if self.standards["require_model_provenance"]:
            checks.append(
                QualityCheck(
                    name="model_provenance",
                    passed=transcript.get("has_model_provenance", False),
                    actual_value=transcript.get("has_model_provenance", False),
                    expected_value=True,
                    message="Model provenance required",
                )
            )

        if self.standards["forbid_mock_content"]:
            checks.append(
                QualityCheck(
                    name="no_mock_content",
                    passed=not transcript.get("is_mock", True),
                    actual_value=not transcript.get("is_mock", True),
                    expected_value=True,
                    message="Mock content forbidden",
                )
            )

        # Determine overall pass/fail
        all_passed = all(check.passed for check in checks)

        # Collect metrics
        metrics = {
            "duration_sec": episode_meta["duration_sec"],
            "audio_sample_rate": episode_meta["audio_sample_rate"],
            "audio_lufs": episode_meta["audio_lufs"],
            "avg_turn_words": transcript["avg_turn_words"],
            "max_turn_words": transcript["max_turn_words"],
            "segment_count": len(transcript["segments"]),
            "has_sponsor_breaks": transcript.get("has_sponsor_breaks", False),
            "has_model_provenance": transcript.get("has_model_provenance", False),
            "is_mock": transcript.get("is_mock", True),
        }

        return QualityReport(
            episode_path=str(episode_path),
            passed=all_passed,
            checks=checks,
            metrics=metrics,
            timestamp=datetime.utcnow().isoformat() + "Z",
        )

    def track_improvement(self, episode_number: int, metrics: Dict[str, Any]):
        """
        Track quality metrics over time for trend analysis.

        Args:
            episode_number: Episode number
            metrics: Quality metrics dict
        """
        self.quality_history.append(
            {
                "episode": episode_number,
                "metrics": metrics,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
        )

    def get_quality_trend(self, last_n_episodes: int = 10) -> List[Dict[str, Any]]:
        """
        Show improvement/regression trends.

        Args:
            last_n_episodes: Number of recent episodes to include

        Returns:
            List of episode metrics
        """
        return self.quality_history[-last_n_episodes:]

    def get_open_issues(self) -> List[str]:
        """
        List production issues that need fixing.

        Returns:
            List of issue descriptions
        """
        # This could integrate with the IssueTracker
        # For now, return placeholder
        return []
