"""
Unit tests for YouTube Thumbnail Generator.

Tests cover:
- Frame extraction from video
- Title text overlay
- Episode badge rendering
- Logo placement
- Gradient backgrounds
- Mobile readability validation
- Thumbnail dimensions (1280x720)
"""

import json
import os
import tempfile

import pytest
from PIL import Image

from seo_manager.youtube.thumbnail_generator import YouTubeThumbnailGenerator


class TestYouTubeThumbnailGenerator:
    """Test thumbnail generation functionality"""

    @pytest.fixture
    def generator(self):
        """Create thumbnail generator"""
        return YouTubeThumbnailGenerator()

    def test_generator_initialization(self, generator):
        """Test generator initializes with correct defaults"""
        assert generator.THUMBNAIL_SIZE == (1280, 720)
        assert generator.SAFE_ZONE == (1120, 630)
        assert "classic" in generator.background_colors

    def test_create_basic_thumbnail(self, generator):
        """Test creating basic thumbnail without video"""
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            output_path = tmp.name

        try:
            # Generate without video frame
            result = generator.generate_thumbnail(
                video_path=None,  # No video
                title="Test Thumbnail Title",
                output_path=output_path,
            )

            assert result == output_path
            assert os.path.exists(output_path)

            # Verify image properties
            img = Image.open(output_path)
            assert img.size == (1280, 720)
            assert img.mode == "RGB"

        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)

    def test_title_text_limit(self, generator):
        """Over-long titles are truncated on a word boundary within budget."""
        long_title = (
            "This Is A Very Long Title With Many Words That Should Be Truncated"
        )

        # Internal method test
        shortened = generator._shorten_title(long_title, max_chars=30)

        # Truncated within the character budget, on a word boundary (a prefix).
        assert 0 < len(shortened) <= 30
        assert long_title.startswith(shortened)

    def test_episode_badge_rendering(self, generator):
        """Test episode number badge is added"""
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            output_path = tmp.name

        try:
            generator.generate_thumbnail(
                video_path=None,
                title="Test Episode",
                episode_number=42,
                output_path=output_path,
            )

            assert os.path.exists(output_path)

            # Badge should be rendered (verified visually or via pixel analysis)
            img = Image.open(output_path)
            assert img.size == (1280, 720)

        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)

    def test_gradient_background(self, generator):
        """Test gradient background generation"""
        img = Image.new("RGB", (1280, 720), (20, 25, 35))

        img_with_gradient = generator._add_gradient(img)

        assert img_with_gradient.size == (1280, 720)
        # Gradient should create color variation
        # (Visual verification needed for quality)

    def test_style_variants(self, generator):
        """Test different style options"""
        styles = ["classic", "vibrant", "dark"]

        for style in styles:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                output_path = tmp.name

            try:
                generator.generate_thumbnail(
                    video_path=None, title="Test", output_path=output_path, style=style
                )

                assert os.path.exists(output_path)

            finally:
                if os.path.exists(output_path):
                    os.unlink(output_path)

    def test_shorten_title_preserves_keywords(self, generator):
        """Test that title shortening preserves important leading words"""
        title = "Machine Learning Tutorial For Beginners Complete Guide"

        shortened = generator._shorten_title(title, max_chars=30)

        # Should keep important words
        assert "Machine Learning" in shortened or "Tutorial" in shortened

    def test_shorten_title_keeps_short_route_whole_and_strips_connector(
        self, generator
    ):
        """A full route title within budget is kept whole (the blunt
        4-word cut used to drop the road number and dangle a connector), and an
        over-budget title never ends on a connector word."""
        # 29 chars — within budget, returned unchanged (road number preserved).
        assert (
            generator._shorten_title("Riverton to Lakeside via R123")
            == "Riverton to Lakeside via R123"
        )
        # Over budget: truncated on a word boundary, no trailing connector.
        out = generator._shorten_title(
            "Springfield to Shelbyville via Route Sixty Six Scenic Byway", max_chars=30
        )
        assert len(out) <= 30
        assert out.split()[-1].lower() not in {"via", "to", "and", "the", "of", "for"}

    def test_thumbnail_output_path_generation(self, generator):
        """Test automatic output path generation"""
        result = generator.generate_thumbnail(
            video_path=None, title="Test", episode_number=5
        )

        # Should create path with episode number
        assert "5" in result or "ep5" in result.lower()
        assert result.endswith(".png")

        # Cleanup
        if os.path.exists(result):
            os.unlink(result)

    def test_safe_zone_dimensions(self, generator):
        """Test safe zone is within thumbnail bounds"""
        safe_width, safe_height = generator.SAFE_ZONE
        thumb_width, thumb_height = generator.THUMBNAIL_SIZE

        # Safe zone should be smaller than full size
        assert safe_width < thumb_width
        assert safe_height < thumb_height

        # Should maintain 16:9 aspect ratio approximately
        aspect_ratio = safe_width / safe_height
        assert 1.7 < aspect_ratio < 1.8  # 16:9 ≈ 1.778

    def test_mobile_validation_check(self, generator):
        """Test mobile readability validation"""
        img = Image.new("RGB", (1280, 720), (255, 255, 255))

        is_readable = generator._validate_mobile_readability(img)

        assert isinstance(is_readable, bool)

    def test_prepare_test_and_compare_variants_writes_exactly_three_with_manifest(
        self, generator, tmp_path
    ):
        """Studio's Test & Compare is UI-only (no API to upload or pick
        a variant) -- this prepares the 3 candidate images an operator
        uploads by hand, plus a manifest recording the pairing."""
        bg_path = tmp_path / "bg.png"
        Image.new("RGB", (1920, 1080), (30, 30, 30)).save(bg_path)
        output_dir = tmp_path / "tc"

        manifest = generator.prepare_test_and_compare_variants(
            background_image=bg_path,
            title="Riverton to Lakeside",
            subtitle="67 km",
            output_dir=output_dir,
        )

        assert len(manifest.variants) == 3
        assert {v.style for v in manifest.variants} == {
            "minimal",
            "bold",
            "immersive",
        }
        for variant in manifest.variants:
            assert os.path.exists(variant.path)
            img = Image.open(variant.path)
            assert img.size == (1280, 720)

        manifest_file = output_dir / "test_compare_manifest.json"
        assert manifest_file.exists()
        with open(manifest_file) as f:
            on_disk = json.load(f)
        assert len(on_disk["variants"]) == 3
        assert on_disk["video_title"] == "Riverton to Lakeside"

    def test_prepare_test_and_compare_variants_delegates_to_existing_generator(
        self, generator, tmp_path, monkeypatch
    ):
        """No duplicated drawing code: it must call the existing
        generate_variations_from_background, not reimplement it."""
        bg_path = tmp_path / "bg.png"
        Image.new("RGB", (1920, 1080), (10, 10, 10)).save(bg_path)
        output_dir = tmp_path / "tc2"

        calls = []
        original = generator.generate_variations_from_background

        def spy(*args, **kwargs):
            calls.append((args, kwargs))
            return original(*args, **kwargs)

        monkeypatch.setattr(generator, "generate_variations_from_background", spy)

        generator.prepare_test_and_compare_variants(
            background_image=bg_path, title="Test", output_dir=output_dir
        )

        assert len(calls) == 1

    def test_prepare_test_and_compare_variants_wrong_variant_count_raises(
        self, generator, tmp_path
    ):
        """Robust-by-construction: never silently manifest something other
        than exactly 3 variants."""
        bg_path = tmp_path / "bg.png"
        output_dir = tmp_path / "tc3"
        output_dir.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1280, 720)).save(output_dir / "only_one.png")

        generator.generate_variations_from_background = lambda **kwargs: [
            output_dir / "only_one.png"
        ]

        with pytest.raises(ValueError):
            generator.prepare_test_and_compare_variants(
                background_image=bg_path, title="Test", output_dir=output_dir
            )

    def test_default_style(self, generator):
        """Test default style is used when not specified"""
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            output_path = tmp.name

        try:
            generator.generate_thumbnail(
                video_path=None,
                title="Default Style Test",
                output_path=output_path,
                # No style specified - should use default
            )

            assert os.path.exists(output_path)

        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)
