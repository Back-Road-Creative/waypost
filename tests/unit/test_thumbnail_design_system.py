"""
Unit tests for Master Thumbnail Design System.

Tests enforce design rules:
- Global constraints (safety zones, margins, typography)
- Mode A: Immersion (scenic/POV videos)
- Mode B: Authority (tutorials/tech)
- Validation and rule enforcement
"""

import pytest

from seo_manager.youtube.thumbnail_design_system import (
    DesignViolation,
    ThumbnailDesignSystem,
    ThumbnailMode,
    ThumbnailSpec,
    ThumbnailValidator,
)


class TestGlobalConstraints:
    """Test global design constraints"""

    def test_safety_zone_bottom_right(self):
        """Test bottom-right quadrant must be empty (timestamp overlay)"""
        validator = ThumbnailValidator()

        # Text in bottom-right (VIOLATION)
        spec = ThumbnailSpec(
            mode=ThumbnailMode.IMMERSION,
            text_position=(960, 540),  # Bottom-right quadrant
            text="TEST",
            logo_position=(100, 100),
        )

        violations = validator.validate(spec)

        # Should have violation for bottom-right placement
        assert any(
            "bottom" in v.message.lower() and "right" in v.message.lower()
            for v in violations
        )

    def test_safety_zone_margins(self):
        """Test 40px margin requirement"""
        validator = ThumbnailValidator()

        # Text too close to edge
        spec = ThumbnailSpec(
            mode=ThumbnailMode.IMMERSION,
            text_position=(20, 20),  # Only 20px from edge (< 40px requirement)
            text="TEST",
            logo_position=(100, 100),
        )

        violations = validator.validate(spec)

        # Should have margin violation
        assert any(
            "margin" in v.message.lower() or "40px" in v.message.lower()
            for v in violations
        )

    def test_max_word_count(self):
        """Test maximum 5 words constraint"""
        validator = ThumbnailValidator()

        # 6 words (VIOLATION)
        spec = ThumbnailSpec(
            mode=ThumbnailMode.IMMERSION,
            text_position=(640, 200),
            text="This Is Six Words Too Many",  # 6 words
            logo_position=(100, 100),
        )

        violations = validator.validate(spec)

        # Should have word count violation
        assert any("word" in v.message.lower() or "5" in v.message for v in violations)

    def test_valid_word_count(self):
        """Test 4 words or less passes"""
        validator = ThumbnailValidator()

        spec = ThumbnailSpec(
            mode=ThumbnailMode.IMMERSION,
            text_position=(640, 200),
            text="KILKEE CLIFFS DRIVE",  # 3 words - VALID
            logo_position=(100, 100),
        )

        violations = validator.validate(spec)

        # Should NOT have word count violation
        word_violations = [v for v in violations if "word" in v.message.lower()]
        assert len(word_violations) == 0


class TestModeImmersion:
    """Test Mode A: Immersion (scenic/POV)"""

    def test_text_position_top_center(self):
        """Test text must be in top center for immersion mode"""
        validator = ThumbnailValidator()

        # Text in bottom (VIOLATION for Immersion mode)
        spec = ThumbnailSpec(
            mode=ThumbnailMode.IMMERSION,
            text_position=(640, 600),  # Bottom area
            text="TEST CLIFFS",
            logo_position=(100, 100),
        )

        violations = validator.validate(spec)

        # Should suggest top center for immersion
        assert any("top" in v.message.lower() for v in violations)

    def test_logo_position_corners_only(self):
        """Test logo must be in corners for immersion"""
        validator = ThumbnailValidator()

        # Logo in center (VIOLATION)
        spec = ThumbnailSpec(
            mode=ThumbnailMode.IMMERSION,
            text_position=(640, 200),
            text="CLIFFS",
            logo_position=(640, 360),  # Dead center - BAD
        )

        violations = validator.validate(spec)

        # Should have logo placement violation
        assert any(
            "logo" in v.message.lower() and "corner" in v.message.lower()
            for v in violations
        )

    def test_no_solid_bars_allowed(self):
        """Test solid background bars prohibited in immersion"""
        spec = ThumbnailSpec(
            mode=ThumbnailMode.IMMERSION,
            text_position=(640, 200),
            text="SCENIC DRIVE",
            logo_position=(100, 100),
            has_background_bar=True,  # VIOLATION
        )

        validator = ThumbnailValidator()
        violations = validator.validate(spec)

        # Should prohibit background bars in immersion mode
        assert any(
            "bar" in v.message.lower() or "background" in v.message.lower()
            for v in violations
        )


class TestModeAuthority:
    """Test Mode B: Authority (tech/tutorials)"""

    def test_rule_of_thirds_layout(self):
        """Test subject must be in rule of thirds for authority"""
        validator = ThumbnailValidator()

        # Subject dead center (VIOLATION)
        spec = ThumbnailSpec(
            mode=ThumbnailMode.AUTHORITY,
            text_position=(200, 360),  # Left side - GOOD
            text="FIX LAG",
            logo_position=(100, 100),
            subject_position=(640, 360),  # Dead center - BAD
        )

        violations = validator.validate(spec)

        # Should suggest rule of thirds
        assert any(
            "third" in v.message.lower() or "subject" in v.message.lower()
            for v in violations
        )

    def test_text_opposite_subject(self):
        """Test text must be opposite subject"""
        validator = ThumbnailValidator()

        # Both on same side (VIOLATION)
        spec = ThumbnailSpec(
            mode=ThumbnailMode.AUTHORITY,
            text_position=(200, 360),  # Left
            text="TUTORIAL",
            logo_position=(100, 100),
            subject_position=(300, 360),  # Also left - BAD
        )

        violations = validator.validate(spec)

        # Should warn about text/subject on same side
        assert any(
            "opposite" in v.message.lower() or "space" in v.message.lower()
            for v in violations
        )


class TestThumbnailDesignSystem:
    """Test main design system"""

    def test_create_immersion_spec(self):
        """Test creating valid immersion spec"""
        system = ThumbnailDesignSystem()

        spec = system.create_spec(
            mode="immersion", title="COASTAL DRIVE", video_type="scenic"
        )

        assert spec.mode == ThumbnailMode.IMMERSION
        assert spec.text_position[1] < 300  # Top area
        assert len(spec.text.split()) <= 4

    def test_create_authority_spec(self):
        """Test creating valid authority spec"""
        system = ThumbnailDesignSystem()

        spec = system.create_spec(
            mode="authority", title="EDIT FASTER DAVINCI", video_type="tutorial"
        )

        assert spec.mode == ThumbnailMode.AUTHORITY
        # Should use rule of thirds

    def test_auto_select_mode_from_video_type(self):
        """Test automatic mode selection"""
        system = ThumbnailDesignSystem()

        # Scenic video -> Immersion
        spec_scenic = system.create_spec_auto(title="IRELAND DRIVE", video_type="drive")
        assert spec_scenic.mode == ThumbnailMode.IMMERSION

        # Tutorial -> Authority
        spec_tutorial = system.create_spec_auto(
            title="FIX ERRORS", video_type="tutorial"
        )
        assert spec_tutorial.mode == ThumbnailMode.AUTHORITY

    def test_validate_before_generation(self):
        """Test validation catches violations before generation"""
        ThumbnailDesignSystem()
        validator = ThumbnailValidator()

        # Create invalid spec
        spec = ThumbnailSpec(
            mode=ThumbnailMode.IMMERSION,
            text_position=(960, 540),  # Bottom-right - VIOLATION
            text="Way Too Many Words Here Bad",  # 6 words - VIOLATION
            logo_position=(640, 360),  # Center - VIOLATION
        )

        violations = validator.validate(spec)

        # Should catch all 3 violations
        assert len(violations) >= 3

    def test_typography_standards_enforced(self):
        """Test typography standards are validated"""
        spec = ThumbnailSpec(
            mode=ThumbnailMode.IMMERSION,
            text_position=(640, 200),
            text="TEST",
            logo_position=(100, 100),
            font_size=30,  # Too small for mobile (< 80pt minimum)
            text_color=(200, 200, 200),  # Not pure white - VIOLATION
        )

        validator = ThumbnailValidator()
        violations = validator.validate(spec)

        # Should warn about font size and color
        assert any(
            "font" in v.message.lower() or "size" in v.message.lower()
            for v in violations
        )
