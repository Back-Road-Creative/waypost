"""
YouTube Thumbnail Generator.

Automatically generates eye-catching thumbnails with:
- Episode branding
- Mobile-optimized text
- High contrast for visibility
- Consistent styling
"""

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


class ThumbnailStyle(Enum):
    """Thumbnail text overlay styles for A/B testing."""

    MINIMAL = "minimal"  # Clean, subtle text at bottom
    BOLD = "bold"  # High contrast text with border
    IMMERSIVE = "immersive"  # Very subtle text, focus on image


@dataclass
class TestCompareVariant:
    """One of the 3 candidate images prepared for Studio's Test & Compare."""

    style: str
    path: str


@dataclass
class TestCompareManifest:
    """Which variants were prepared for one video's Test & Compare run.

    Studio's Test & Compare has no API to upload variants or to say which
    the operator picked -- this manifest is the record of what was
    *prepared*; ``ThumbnailABTracker.record_test_compare_selection`` is the
    separate, manual record of which one actually got uploaded.
    """

    video_title: str
    generated_at: str
    variants: list[TestCompareVariant]
    manifest_path: str


class YouTubeThumbnailGenerator:
    """
    Generates YouTube thumbnails optimized for CTR.

    Best practices (2026):
    - 1280x720 resolution (16:9)
    - Max 4 words of text (mobile readability)
    - High contrast colors
    - Consistent branding
    - Safe zone (1120x630) for mobile crop
    """

    THUMBNAIL_SIZE = (1280, 720)  # YouTube standard
    SAFE_ZONE = (1120, 630)  # Avoid edges for mobile

    def __init__(self, branding_config: dict | None = None):
        """
        Initialize thumbnail generator.

        Args:
            branding_config: Optional branding settings
        """
        self.branding = branding_config or {}

        # Color schemes
        self.background_colors = {
            "classic": (20, 25, 35),  # Dark blue-gray
            "vibrant": (0, 120, 215),  # Bright blue
            "dark": (15, 15, 20),  # Near black
        }

    def generate_thumbnail(
        self,
        video_path: str | None,
        title: str,
        episode_number: int | None = None,
        output_path: str | None = None,
        key_frame_time: float = 30.0,
        style: str = "classic",
        logo_path: str | None = None,
    ) -> str:
        """
        Generate YouTube thumbnail.

        Args:
            video_path: Path to video file (None = no video frame)
            title: Episode title (shortened to 4 words max)
            episode_number: Episode number for badge
            output_path: Where to save (auto-generated if None)
            key_frame_time: Second to extract frame from video
            style: Color scheme (classic/vibrant/dark)
            logo_path: Path to logo image

        Returns:
            Path to generated thumbnail
        """
        # Create base image
        bg_color = self.background_colors.get(style, self.background_colors["classic"])
        img = Image.new("RGB", self.THUMBNAIL_SIZE, bg_color)

        # Add gradient background
        img = self._add_gradient(img)

        # Add title text (max 4 words)
        img = self._add_title_text(img, title)

        # Add episode badge if provided
        if episode_number:
            img = self._add_episode_badge(img, episode_number)

        # Add logo if provided
        if logo_path and os.path.exists(logo_path):
            img = self._add_logo(img, logo_path)

        # Validate mobile readability
        if not self._validate_mobile_readability(img):
            print("Warning: Thumbnail may not be readable on mobile")

        # Generate output path if not provided
        if not output_path:
            ep_suffix = f"_ep{episode_number}" if episode_number else ""
            output_path = f"thumbnail{ep_suffix}.png"

        # Save
        img.save(output_path, "PNG", quality=95)

        return output_path

    def _shorten_title(self, title: str, max_chars: int = 30) -> str:
        """
        Shorten an over-long title for mobile thumbnail readability.

        Truncates on a WORD boundary within ``max_chars`` — not a blunt
        first-N-words cut, which dropped meaningful trailing tokens (e.g. a
        road number) and left dangling connectors ("Riverton to Lakeside
        via"). Titles within budget are returned unchanged, matching the A/B
        path that burns the full title. A trailing connector word is stripped.

        Args:
            title: Full title
            max_chars: Character budget before word-boundary truncation

        Returns:
            Shortened title
        """
        connectors = {"via", "to", "and", "the", "of", "on", "at", "for", "&", "-", "|"}
        title = title.strip()
        if len(title) <= max_chars:
            return title

        kept: list[str] = []
        length = 0
        for word in title.split():
            add = len(word) + (1 if kept else 0)
            if length + add > max_chars and kept:
                break
            kept.append(word)
            length += add

        # Never end a truncated title on a dangling connector.
        while len(kept) > 1 and kept[-1].lower().strip(".,") in connectors:
            kept.pop()

        return " ".join(kept)

    def _add_gradient(self, img: Image.Image) -> Image.Image:
        """
        Add subtle gradient to background.

        Args:
            img: Base image

        Returns:
            Image with gradient
        """
        draw = ImageDraw.Draw(img)
        width, height = img.size

        # Vertical gradient from top to bottom
        for y in range(height):
            # Gradually lighten towards bottom
            brightness_increase = int((y / height) * 25)

            # Get base color
            base_color = img.getpixel((width // 2, 0))

            # Apply brightness increase
            new_color = tuple(min(c + brightness_increase, 255) for c in base_color)

            draw.line([(0, y), (width, y)], fill=new_color)

        return img

    def _add_title_text(self, img: Image.Image, title: str) -> Image.Image:
        """
        Add title text overlay.

        Args:
            img: Base image
            title: Title text (will be shortened)

        Returns:
            Image with text
        """
        draw = ImageDraw.Draw(img)

        # Shorten over-long titles on a word boundary (keeps short titles whole)
        title = self._shorten_title(title)

        # Load large bold font
        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 80
            )
        except:  # noqa: E722
            # Fallback to default if DejaVu not available
            font = ImageFont.load_default()

        # Calculate centered position
        bbox = draw.textbbox((0, 0), title, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        x = (self.THUMBNAIL_SIZE[0] - text_width) // 2
        y = (self.THUMBNAIL_SIZE[1] - text_height) // 2

        # Draw shadow for contrast
        shadow_offset = 4
        draw.text(
            (x + shadow_offset, y + shadow_offset), title, fill=(0, 0, 0), font=font
        )

        # Draw main text
        draw.text((x, y), title, fill=(255, 255, 255), font=font)

        return img

    def _add_episode_badge(self, img: Image.Image, episode_number: int) -> Image.Image:
        """
        Add episode number badge.

        Args:
            img: Base image
            episode_number: Episode number

        Returns:
            Image with badge
        """
        draw = ImageDraw.Draw(img)

        # Badge text
        badge_text = f"#{episode_number}"

        # Load font
        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 60
            )
        except:  # noqa: E722
            font = ImageFont.load_default()

        # Position in top-right corner
        bbox = draw.textbbox((0, 0), badge_text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        x = self.THUMBNAIL_SIZE[0] - text_width - 60
        y = 40

        # Draw circle background
        circle_radius = 50
        circle_center = (x + text_width // 2, y + text_height // 2)

        draw.ellipse(
            [
                (circle_center[0] - circle_radius, circle_center[1] - circle_radius),
                (circle_center[0] + circle_radius, circle_center[1] + circle_radius),
            ],
            fill=(0, 180, 255),
        )

        # Draw text
        draw.text((x, y), badge_text, fill=(255, 255, 255), font=font)

        return img

    def _add_logo(self, img: Image.Image, logo_path: str) -> Image.Image:
        """
        Add logo to thumbnail.

        Args:
            img: Base image
            logo_path: Path to logo

        Returns:
            Image with logo
        """
        try:
            logo = Image.open(logo_path).convert("RGBA")

            # Resize logo to max 200px height
            max_height = 120
            aspect_ratio = logo.width / logo.height
            new_height = min(logo.height, max_height)
            new_width = int(new_height * aspect_ratio)

            logo = logo.resize((new_width, new_height), Image.Resampling.LANCZOS)

            # Position in bottom-left corner
            x = 40
            y = self.THUMBNAIL_SIZE[1] - new_height - 40

            # Composite logo
            img.paste(logo, (x, y), logo)

        except Exception as e:
            print(f"Could not add logo: {e}")

        return img

    def _validate_mobile_readability(self, img: Image.Image) -> bool:
        """
        Validate thumbnail is readable on mobile.

        Args:
            img: Thumbnail image

        Returns:
            True if readable on mobile
        """
        # Mobile preview size (YouTube mobile)
        mobile_size = (360, 202)

        try:
            # Create mobile preview
            mobile_preview = img.resize(mobile_size, Image.Resampling.LANCZOS)

            # Basic validation: image created successfully
            # (In production, could use OCR to verify text readability)
            return mobile_preview.size == mobile_size

        except Exception:
            return False

    def generate_variations_from_background(
        self,
        background_image: Path,
        title: str,
        subtitle: str | None = None,
        output_dir: Path = Path("./thumbnails"),
    ) -> list[Path]:
        """
        Generate 3 thumbnail variations from user-provided background image.

        Creates A/B test variations with different text styling:
        - Minimal: Clean text at bottom with semi-transparent overlay
        - Bold: High contrast text with strong border and background
        - Immersive: Very subtle text, lets image shine

        Args:
            background_image: Path to background image (from video frame)
            title: Journey title (e.g., "Riverton to Lakeside")
            subtitle: Optional subtitle (e.g., "67 km")
            output_dir: Where to save variations

        Returns:
            List of 3 Paths to generated thumbnails
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        thumbnails = []

        # Load and resize background
        bg = Image.open(background_image).convert("RGB")
        bg = bg.resize(self.THUMBNAIL_SIZE, Image.Resampling.LANCZOS)

        # Generate 3 variations
        for style in ThumbnailStyle:
            thumb_path = output_dir / f"variation_{style.value}.png"
            thumbnail = self._generate_variation(bg.copy(), title, subtitle, style)
            thumbnail.save(thumb_path, "PNG", quality=95)
            thumbnails.append(thumb_path)

        return thumbnails

    def prepare_test_and_compare_variants(
        self,
        background_image: Path,
        title: str,
        subtitle: str | None = None,
        output_dir: Path = Path("./thumbnails"),
    ) -> TestCompareManifest:
        """
        Prepare exactly 3 thumbnail variants for YouTube Studio's Test &
        Compare, plus a manifest recording which style is which.

        Test & Compare is UI-only -- there is no API to upload the 3
        variants or to read back which one an operator selected, so this
        only prepares the images an operator uploads by hand (see
        ``ThumbnailABTracker.record_test_compare_selection`` for the manual
        record of what actually got uploaded). All drawing is delegated to
        ``generate_variations_from_background`` -- no PIL logic is
        duplicated here.

        Args:
            background_image: Path to background image (from video frame)
            title: Journey/video title
            subtitle: Optional subtitle
            output_dir: Where to save the variants and manifest

        Returns:
            TestCompareManifest describing the 3 prepared variants

        Raises:
            ValueError: if the underlying generator did not produce exactly
                3 variants (robust-by-construction: never manifest a wrong
                count silently).
        """
        output_dir = Path(output_dir)
        variant_paths = self.generate_variations_from_background(
            background_image=background_image,
            title=title,
            subtitle=subtitle,
            output_dir=output_dir,
        )

        if len(variant_paths) != 3:
            raise ValueError(
                f"Test & Compare needs exactly 3 variants, got {len(variant_paths)}"
            )

        variants = [
            TestCompareVariant(style=style.value, path=str(path))
            for style, path in zip(ThumbnailStyle, variant_paths, strict=False)
        ]

        manifest_path = output_dir / "test_compare_manifest.json"
        manifest = TestCompareManifest(
            video_title=title,
            generated_at=datetime.now().isoformat(),
            variants=variants,
            manifest_path=str(manifest_path),
        )

        with open(manifest_path, "w") as f:
            json.dump(asdict(manifest), f, indent=2)

        return manifest

    def _generate_variation(
        self,
        image: Image.Image,
        title: str,
        subtitle: str | None,
        style: ThumbnailStyle,
    ) -> Image.Image:
        """
        Generate single thumbnail variation with specific style.

        Args:
            image: Background image
            title: Main title text
            subtitle: Optional subtitle
            style: Which style to apply

        Returns:
            Styled thumbnail image
        """
        draw = ImageDraw.Draw(image, "RGBA")

        if style == ThumbnailStyle.MINIMAL:
            return self._draw_minimal_style(image, draw, title, subtitle)
        elif style == ThumbnailStyle.BOLD:
            return self._draw_bold_style(image, draw, title, subtitle)
        else:  # IMMERSIVE
            return self._draw_immersive_style(image, draw, title, subtitle)

    def _draw_minimal_style(
        self,
        image: Image.Image,
        draw: ImageDraw.Draw,
        title: str,
        subtitle: str | None,
    ) -> Image.Image:
        """Minimal style: Clean text at bottom with semi-transparent overlay."""
        width, height = image.size

        # Add semi-transparent dark overlay at bottom
        overlay_height = 200
        overlay_y = height - overlay_height

        # Draw gradient overlay
        for y in range(overlay_height):
            alpha = int((y / overlay_height) * 200)  # 0 to 200 transparency
            overlay_color = (0, 0, 0, alpha)
            draw.line([(0, overlay_y + y), (width, overlay_y + y)], fill=overlay_color)

        # Load fonts
        try:
            title_font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 72
            )
            subtitle_font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 40
            )
        except:  # noqa: E722
            title_font = ImageFont.load_default()
            subtitle_font = ImageFont.load_default()

        # Draw title centered at bottom
        title_bbox = draw.textbbox((0, 0), title, font=title_font)
        title_width = title_bbox[2] - title_bbox[0]
        title_x = (width - title_width) // 2
        title_y = height - 140

        draw.text((title_x, title_y), title, fill=(255, 255, 255), font=title_font)

        # Draw subtitle if provided
        if subtitle:
            subtitle_bbox = draw.textbbox((0, 0), subtitle, font=subtitle_font)
            subtitle_width = subtitle_bbox[2] - subtitle_bbox[0]
            subtitle_x = (width - subtitle_width) // 2
            subtitle_y = height - 70

            draw.text(
                (subtitle_x, subtitle_y),
                subtitle,
                fill=(200, 200, 200),
                font=subtitle_font,
            )

        return image

    def _draw_bold_style(
        self,
        image: Image.Image,
        draw: ImageDraw.Draw,
        title: str,
        subtitle: str | None,
    ) -> Image.Image:
        """Bold style: High contrast text with strong border and background."""
        width, height = image.size

        # Load fonts
        try:
            title_font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 80
            )
            subtitle_font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 45
            )
        except:  # noqa: E722
            title_font = ImageFont.load_default()
            subtitle_font = ImageFont.load_default()

        # Draw title centered
        title_bbox = draw.textbbox((0, 0), title, font=title_font)
        title_width = title_bbox[2] - title_bbox[0]
        title_height = title_bbox[3] - title_bbox[1]
        title_x = (width - title_width) // 2
        title_y = (height - title_height) // 2 - 30

        # Draw solid background rectangle
        padding = 30
        rect_coords = [
            (title_x - padding, title_y - padding),
            (
                title_x + title_width + padding,
                title_y + title_height + padding + (70 if subtitle else 0),
            ),
        ]
        draw.rectangle(rect_coords, fill=(0, 0, 0, 220))

        # Draw thick border
        border_width = 6
        for i in range(border_width):
            border_rect = [
                (rect_coords[0][0] - i, rect_coords[0][1] - i),
                (rect_coords[1][0] + i, rect_coords[1][1] + i),
            ]
            draw.rectangle(border_rect, outline=(255, 200, 0), width=1)

        # Draw title with shadow
        shadow_offset = 5
        draw.text(
            (title_x + shadow_offset, title_y + shadow_offset),
            title,
            fill=(0, 0, 0),
            font=title_font,
        )
        draw.text((title_x, title_y), title, fill=(255, 255, 255), font=title_font)

        # Draw subtitle if provided
        if subtitle:
            subtitle_bbox = draw.textbbox((0, 0), subtitle, font=subtitle_font)
            subtitle_width = subtitle_bbox[2] - subtitle_bbox[0]
            subtitle_x = (width - subtitle_width) // 2
            subtitle_y = title_y + title_height + 15

            draw.text(
                (subtitle_x, subtitle_y),
                subtitle,
                fill=(255, 200, 0),
                font=subtitle_font,
            )

        return image

    def _draw_immersive_style(
        self,
        image: Image.Image,
        draw: ImageDraw.Draw,
        title: str,
        subtitle: str | None,
    ) -> Image.Image:
        """Immersive style: Very subtle text, focus on scenic image."""
        _width, _height = image.size

        # Load fonts (smaller than other styles)
        try:
            title_font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 56
            )
            subtitle_font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 32
            )
        except:  # noqa: E722
            title_font = ImageFont.load_default()
            subtitle_font = ImageFont.load_default()

        # Draw title in top-left corner with subtle shadow
        title_x = 40
        title_y = 40

        # Very subtle shadow
        shadow_offset = 2
        draw.text(
            (title_x + shadow_offset, title_y + shadow_offset),
            title,
            fill=(0, 0, 0, 150),
            font=title_font,
        )
        draw.text((title_x, title_y), title, fill=(255, 255, 255, 230), font=title_font)

        # Draw subtitle if provided (top-left, below title)
        if subtitle:
            subtitle_y = title_y + 70
            draw.text(
                (title_x, subtitle_y),
                subtitle,
                fill=(240, 240, 240, 200),
                font=subtitle_font,
            )

        return image
