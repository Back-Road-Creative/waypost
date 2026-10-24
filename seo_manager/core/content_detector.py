"""
Content Type Detector for YouTube Videos.

Auto-detects if a video is a YouTube Short or long-form video
based on duration and aspect ratio.

YouTube Shorts criteria (2026):
- Duration: <=60 seconds
- Aspect ratio: 9:16 (vertical, portrait)
"""

import json
import subprocess
from pathlib import Path
from typing import Literal

ContentTypeResult = Literal["SHORT", "VIDEO"]

# Shared with the video pipeline: a clip of at most this many seconds is a Short.
SHORTS_MAX_DURATION = 60


class ContentDetector:
    """
    Automatically detect if video is a YouTube Short or long-form video.

    Detection signals:
    1. Duration: <=60 seconds = Short
    2. Aspect ratio: 9:16 (vertical) = Short
    3. Manual override support
    """

    def __init__(self):
        """Initialize content detector"""
        pass

    def detect_from_duration(self, duration_seconds: float) -> ContentTypeResult:
        """
        Detect content type based on video duration.

        YouTube Shorts must be <= 60 seconds.

        Args:
            duration_seconds: Video duration in seconds

        Returns:
            'SHORT' if <=60 seconds, 'VIDEO' otherwise
        """
        if duration_seconds <= SHORTS_MAX_DURATION:
            return "SHORT"
        return "VIDEO"

    def detect_from_dimensions(self, width: int, height: int) -> ContentTypeResult:
        """
        Detect content type based on video dimensions.

        YouTube Shorts are typically vertical (9:16 aspect ratio).
        Portrait orientation (height > width) suggests Short format.

        Args:
            width: Video width in pixels
            height: Video height in pixels

        Returns:
            'SHORT' if vertical/portrait, 'VIDEO' if horizontal/square
        """
        # Calculate aspect ratio
        aspect_ratio = width / height if height > 0 else 1.0

        # Vertical videos (height > width) are Shorts
        # Typical Short dimensions: 1080x1920 (0.5625 ratio)
        # Allow some tolerance for different resolutions
        if aspect_ratio < 0.75:  # More portrait than 4:3
            return "SHORT"

        return "VIDEO"

    def get_video_duration(self, video_path: str) -> float:
        """
        Extract video duration using ffprobe.

        Args:
            video_path: Path to video file

        Returns:
            Duration in seconds

        Raises:
            FileNotFoundError: If video file doesn't exist
            ValueError: If ffprobe fails or returns invalid data
        """
        path = Path(video_path)
        if not path.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")

        try:
            # Use ffprobe to get duration
            cmd = [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, check=True)

            data = json.loads(result.stdout)
            duration = float(data["format"]["duration"])

            return duration

        except subprocess.CalledProcessError as e:
            raise ValueError(f"ffprobe failed: {e.stderr}")  # noqa: B904
        except (KeyError, json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"Failed to parse duration: {e}")  # noqa: B904

    def get_video_dimensions(self, video_path: str) -> tuple[int, int]:
        """
        Extract video dimensions (width, height) using ffprobe.

        Args:
            video_path: Path to video file

        Returns:
            Tuple of (width, height) in pixels

        Raises:
            FileNotFoundError: If video file doesn't exist
            ValueError: If ffprobe fails or returns invalid data
        """
        path = Path(video_path)
        if not path.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")

        try:
            # Use ffprobe to get video stream dimensions
            cmd = [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "json",
                str(path),
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, check=True)

            data = json.loads(result.stdout)
            stream = data["streams"][0]
            width = int(stream["width"])
            height = int(stream["height"])

            return (width, height)

        except subprocess.CalledProcessError as e:
            raise ValueError(f"ffprobe failed: {e.stderr}")  # noqa: B904
        except (KeyError, json.JSONDecodeError, ValueError, IndexError) as e:
            raise ValueError(f"Failed to parse dimensions: {e}")  # noqa: B904

    def detect_from_file(
        self, video_path: str, force_type: ContentTypeResult | None = None
    ) -> ContentTypeResult:
        """
        Detect content type from video file.

        Combines multiple signals:
        1. Duration (primary signal - more reliable)
        2. Aspect ratio (secondary signal)

        Duration takes priority as it's the official YouTube requirement.

        Args:
            video_path: Path to video file
            force_type: Manual override ('SHORT' or 'VIDEO')

        Returns:
            Detected content type ('SHORT' or 'VIDEO')

        Raises:
            FileNotFoundError: If video file doesn't exist
            ValueError: If video analysis fails
        """
        # If manual override provided, use it
        if force_type:
            return force_type

        # Get duration (primary signal)
        duration = self.get_video_duration(video_path)
        duration_type = self.detect_from_duration(duration)

        # Duration is the definitive signal for Shorts
        # (<=60 seconds is the official requirement)
        if duration_type == "SHORT":
            return "SHORT"

        # For longer videos, also check aspect ratio
        # (in case of a vertical long-form video)
        width, height = self.get_video_dimensions(video_path)
        self.detect_from_dimensions(width, height)

        # If duration says VIDEO but it's vertical, still treat as VIDEO
        # (Shorts MUST be <60s, so duration is authoritative)
        return "VIDEO"
