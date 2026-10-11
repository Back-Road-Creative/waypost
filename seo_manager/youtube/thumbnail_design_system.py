"""
Master Thumbnail Design System v1.0

Enforces design rules for maximum CTR and mobile visibility.

Modes:
- Mode A: Immersion (scenic, POV, travel)
- Mode B: Authority (tutorials, tech, educational)

Global Constraints:
- Bottom-right quadrant empty (YouTube timestamp)
- 40px margins from edges
- Max 5 words (4 preferred)
- Pure white text (#FFFFFF)
- Heavy black outline or drop shadow
- 1280x720 resolution (16:9)
"""

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple


class ThumbnailMode(Enum):
    """Thumbnail design modes"""

    IMMERSION = "immersion"  # Scenic, POV, travel
    AUTHORITY = "authority"  # Tutorials, tech, educational


@dataclass
class DesignViolation:
    """Represents a design rule violation"""

    severity: str  # "error" or "warning"
    rule: str
    message: str


@dataclass
class ThumbnailSpec:
    """Specification for thumbnail generation"""

    mode: ThumbnailMode
    text: str
    text_position: Tuple[int, int]  # (x, y) coordinates
    logo_position: Tuple[int, int]

    # Optional attributes
    has_background_bar: bool = False
    subject_position: Optional[Tuple[int, int]] = None
    font_size: int = 120  # Default: 120pt
    text_color: Tuple[int, int, int] = (255, 255, 255)  # Pure white
    outline_thickness: int = 12


class ThumbnailValidator:
    """
    Validates thumbnail specs against Master Design System rules.

    Enforces:
    - Global constraints (safety zones, typography, margins)
    - Mode-specific rules (Immersion vs Authority)
    """

    CANVAS_SIZE = (1280, 720)  # YouTube standard
    MIN_MARGIN = 40  # Pixels from edge
    MAX_WORDS = 5
    PREFERRED_MAX_WORDS = 4
    MIN_FONT_SIZE = 80  # Minimum for mobile
    PURE_WHITE = (255, 255, 255)

    # Safety zones
    BOTTOM_RIGHT_ZONE = {  # noqa: RUF012
        "x_min": 640,  # Right half
        "y_min": 360,  # Bottom half
        "x_max": 1280,
        "y_max": 720,
    }

    def validate(self, spec: ThumbnailSpec) -> List[DesignViolation]:
        """
        Validate thumbnail spec against all rules.

        Args:
            spec: Thumbnail specification

        Returns:
            List of violations (empty if all valid)
        """
        violations = []

        # Global constraints
        violations.extend(self._check_global_constraints(spec))

        # Mode-specific rules
        if spec.mode == ThumbnailMode.IMMERSION:
            violations.extend(self._check_immersion_mode(spec))
        elif spec.mode == ThumbnailMode.AUTHORITY:
            violations.extend(self._check_authority_mode(spec))

        return violations

    def _check_global_constraints(self, spec: ThumbnailSpec) -> List[DesignViolation]:
        """Check global constraints that apply to ALL thumbnails"""
        violations = []

        # 1. Bottom-right quadrant (dead zone)
        if self._is_in_bottom_right_zone(spec.text_position):
            violations.append(
                DesignViolation(
                    severity="error",
                    rule="Bottom Right Dead Zone",
                    message="Text must NOT be in bottom-right quadrant (reserved for YouTube timestamp overlay)",
                )
            )

        if spec.logo_position and self._is_in_bottom_right_zone(spec.logo_position):
            violations.append(
                DesignViolation(
                    severity="error",
                    rule="Bottom Right Dead Zone",
                    message="Logo must NOT be in bottom-right quadrant",
                )
            )

        # 2. Margin check (40px from edges)
        if not self._check_margins(spec.text_position):
            violations.append(
                DesignViolation(
                    severity="error",
                    rule="40px Margin",
                    message=f"Text must be at least {self.MIN_MARGIN}px from canvas edges",
                )
            )

        if spec.logo_position and not self._check_margins(spec.logo_position):
            violations.append(
                DesignViolation(
                    severity="warning",
                    rule="40px Margin",
                    message=f"Logo should be at least {self.MIN_MARGIN}px from edges",
                )
            )

        # 3. Word count (max 5, prefer 4)
        word_count = len(spec.text.split())
        if word_count > self.MAX_WORDS:
            violations.append(
                DesignViolation(
                    severity="error",
                    rule="Max Word Count",
                    message=f"Text has {word_count} words. Maximum is {self.MAX_WORDS} words",
                )
            )
        elif word_count > self.PREFERRED_MAX_WORDS:
            violations.append(
                DesignViolation(
                    severity="warning",
                    rule="Preferred Word Count",
                    message=f"Text has {word_count} words. {self.PREFERRED_MAX_WORDS} words or less recommended for mobile",
                )
            )

        # 4. Typography standards
        if spec.font_size < self.MIN_FONT_SIZE:
            violations.append(
                DesignViolation(
                    severity="error",
                    rule="Minimum Font Size",
                    message=f"Font size {spec.font_size}pt too small. Minimum {self.MIN_FONT_SIZE}pt for mobile readability",
                )
            )

        if spec.text_color != self.PURE_WHITE:
            violations.append(
                DesignViolation(
                    severity="warning",
                    rule="Text Color",
                    message=f"Text color should be pure white {self.PURE_WHITE} for maximum contrast",
                )
            )

        if spec.outline_thickness < 8:
            violations.append(
                DesignViolation(
                    severity="warning",
                    rule="Outline Thickness",
                    message=f"Outline {spec.outline_thickness}px may be too thin. Recommend 10-12px for visibility",
                )
            )

        return violations

    def _check_immersion_mode(self, spec: ThumbnailSpec) -> List[DesignViolation]:
        """Check Mode A: Immersion-specific rules"""
        violations = []

        # 1. Text placement: Top center (sky area)
        text_x, text_y = spec.text_position

        # Should be in top third
        if text_y > (self.CANVAS_SIZE[1] // 3):
            violations.append(
                DesignViolation(
                    severity="error",
                    rule="Immersion: Text Placement",
                    message="Text must be in TOP CENTER (sky area) for immersion mode. Current position blocks the road.",
                )
            )

        # Should be horizontally centered (±200px tolerance)
        center_x = self.CANVAS_SIZE[0] // 2
        if abs(text_x - center_x) > 200:
            violations.append(
                DesignViolation(
                    severity="warning",
                    rule="Immersion: Text Centering",
                    message="Text should be centered horizontally for immersion mode",
                )
            )

        # 2. No solid background bars
        if spec.has_background_bar:
            violations.append(
                DesignViolation(
                    severity="error",
                    rule="Immersion: No Bars",
                    message="Solid background bars PROHIBITED in immersion mode (breaks immersion)",
                )
            )

        # 3. Logo in corners only
        if spec.logo_position:
            logo_x, logo_y = spec.logo_position

            # Check if in corners (top-left, top-right only for immersion)
            in_corner = (
                (logo_x < 200 and logo_y < 150)  # Top-left
                or (logo_x > self.CANVAS_SIZE[0] - 200 and logo_y < 150)  # Top-right
            )

            if not in_corner:
                violations.append(
                    DesignViolation(
                        severity="error",
                        rule="Immersion: Logo Placement",
                        message="Logo must be in TOP-LEFT or TOP-RIGHT corner only",
                    )
                )

        return violations

    def _check_authority_mode(self, spec: ThumbnailSpec) -> List[DesignViolation]:
        """Check Mode B: Authority-specific rules"""
        violations = []

        # 1. Subject must be in rule of thirds
        if spec.subject_position:
            subject_x, _subject_y = spec.subject_position

            # Check if in left third or right third (not center)
            left_third = subject_x < (self.CANVAS_SIZE[0] // 3)
            right_third = subject_x > (self.CANVAS_SIZE[0] * 2 // 3)

            if not (left_third or right_third):
                violations.append(
                    DesignViolation(
                        severity="error",
                        rule="Authority: Rule of Thirds",
                        message="Subject must be in LEFT or RIGHT third (not center) for authority mode",
                    )
                )

        # 2. Text should be opposite subject
        if spec.subject_position and spec.text_position:
            text_x = spec.text_position[0]
            subject_x = spec.subject_position[0]

            # Both on same side
            both_left = text_x < 640 and subject_x < 640
            both_right = text_x > 640 and subject_x > 640

            if both_left or both_right:
                violations.append(
                    DesignViolation(
                        severity="warning",
                        rule="Authority: Text/Subject Separation",
                        message="Text should be OPPOSITE subject for balanced composition",
                    )
                )

        return violations

    def _is_in_bottom_right_zone(self, position: Tuple[int, int]) -> bool:
        """Check if position is in bottom-right dead zone"""
        x, y = position
        zone = self.BOTTOM_RIGHT_ZONE

        return (
            zone["x_min"] <= x <= zone["x_max"] and zone["y_min"] <= y <= zone["y_max"]
        )

    def _check_margins(self, position: Tuple[int, int]) -> bool:
        """Check if position has adequate margins"""
        x, y = position

        # Check all edges
        too_close_left = x < self.MIN_MARGIN
        too_close_top = y < self.MIN_MARGIN
        too_close_right = x > (self.CANVAS_SIZE[0] - self.MIN_MARGIN)
        too_close_bottom = y > (self.CANVAS_SIZE[1] - self.MIN_MARGIN)

        return not (
            too_close_left or too_close_top or too_close_right or too_close_bottom
        )


class ThumbnailDesignSystem:
    """
    Master Thumbnail Design System.

    Creates validated thumbnail specifications based on video type.
    """

    VIDEO_TYPE_MODE_MAP = {  # noqa: RUF012
        "drive": ThumbnailMode.IMMERSION,
        "hike": ThumbnailMode.IMMERSION,
        "scenic": ThumbnailMode.IMMERSION,
        "drone": ThumbnailMode.IMMERSION,
        "travel": ThumbnailMode.IMMERSION,
        "vlog": ThumbnailMode.IMMERSION,
        "tutorial": ThumbnailMode.AUTHORITY,
        "coding": ThumbnailMode.AUTHORITY,
        "tech": ThumbnailMode.AUTHORITY,
        "business": ThumbnailMode.AUTHORITY,
        "educational": ThumbnailMode.AUTHORITY,
        "review": ThumbnailMode.AUTHORITY,
    }

    def create_spec_auto(self, title: str, video_type: str) -> ThumbnailSpec:
        """
        Automatically create spec based on video type.

        Args:
            title: Video title
            video_type: Type (drive, tutorial, etc.)

        Returns:
            Validated ThumbnailSpec
        """
        mode = self.VIDEO_TYPE_MODE_MAP.get(video_type.lower(), ThumbnailMode.IMMERSION)
        return self.create_spec(mode.value, title, video_type)

    def create_spec(self, mode: str, title: str, video_type: str) -> ThumbnailSpec:
        """
        Create thumbnail spec for a mode.

        Args:
            mode: "immersion" or "authority"
            title: Video title (will be shortened if needed)
            video_type: Video category

        Returns:
            ThumbnailSpec with validated positions
        """
        mode_enum = ThumbnailMode(mode)

        # Shorten title to 4 words max
        words = title.split()
        if len(words) > 4:
            title = " ".join(words[:4])

        if mode_enum == ThumbnailMode.IMMERSION:
            # Top center text, top-left logo
            spec = ThumbnailSpec(
                mode=mode_enum,
                text=title,
                text_position=(640, 150),  # Top center
                logo_position=(100, 80),  # Top-left corner
                has_background_bar=False,  # NO bars in immersion
                font_size=120,
                text_color=(255, 255, 255),
                outline_thickness=12,
            )

        else:  # AUTHORITY
            # Left text, right subject (default)
            spec = ThumbnailSpec(
                mode=mode_enum,
                text=title,
                text_position=(200, 360),  # Left third, vertically centered
                logo_position=(100, 80),  # Top-left
                subject_position=(960, 360),  # Right third
                font_size=100,
                text_color=(255, 255, 255),
                outline_thickness=10,
            )

        return spec
