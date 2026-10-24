"""
Video SEO Generator for Journey Videos.

Generates optimized YouTube metadata for long-form videos and shorts
using journey context (locations, distance, highlights).
"""

from ..core.metadata import ContentType, Platform, SEOMetadata
from ..generation.base import BaseSEOGenerator
from ..platforms.youtube import YouTubeAdapter

# Country strings whose journeys read in miles rather than km. US footage
# would otherwise get a km-only "150km drive".
_US_COUNTRY_NAMES = frozenset(
    {
        "usa",
        "us",
        "u.s.",
        "u.s.a.",
        "united states",
        "united states of america",
        "america",
    }
)
_KM_TO_MILES = 0.621371


class VideoSEOGenerator:
    """
    Generate SEO metadata for journey videos using context from GPS service.

    Integrates with existing SEO infrastructure to leverage:
    - Keyword extraction (NLP-based)
    - Quality scoring
    - Readability analysis
    - Self-improving analytics system
    """

    def __init__(self, site_id: str = "default"):
        """
        Initialize video SEO generator.

        Args:
            site_id: Site identifier for multi-site isolation
        """
        self.site_id = site_id
        self.base_generator = BaseSEOGenerator()
        self.platform_adapter = YouTubeAdapter()

    @staticmethod
    def _require_country(journey_data: dict) -> str:
        """Return the journey's country, or raise if it is absent.

        Refuses to silently default to a hardcoded location: a journey dict
        without a country must fail loudly rather than ship Irish framing on
        (for example) a US drive.
        """
        country = journey_data.get("country")
        if not country:
            raise ValueError(
                "journey_data must include a non-empty 'country'; "
                "refusing to default to a hardcoded location."
            )
        return country

    def generate_from_journey(
        self,
        journey_id: str,
        journey_data: dict,
        video_duration: int,
        content_type: ContentType = ContentType.VIDEO,
    ) -> SEOMetadata:
        """
        Generate SEO metadata from journey context.

        Args:
            journey_id: Unique journey identifier (e.g., "coast-route-001")
            journey_data: Journey metadata dict with keys:
                - title: Journey title
                - start_location: Starting point
                - end_location: Ending point
                - highlights: List of interesting locations
                - total_distance_km: Distance in kilometers
                - country: Country name
                - description: Journey description
            video_duration: Video length in seconds
            content_type: VIDEO (long-form) or SHORT

        Returns:
            SEOMetadata with optimized title, description, tags
        """
        # Build topic and body based on journey data
        if content_type == ContentType.VIDEO:
            topic, body = self._build_longform_content(journey_data)
        else:  # SHORT
            topic, body = self._build_short_content(journey_data)

        # Extract keywords using NLP
        keywords = self.base_generator._extract_keywords(
            topic=topic,
            body=body,
            transcript=None,  # Could add narration transcript in future
        )

        # Optimize title for YouTube
        optimized_title = self.base_generator._optimize_title(
            title=topic, limit=self.platform_adapter.get_title_limit()
        )

        # Optimize description
        optimized_description = self.base_generator._optimize_description(
            description=body, limit=self.platform_adapter.get_description_limit()
        )

        # Generate tags from keywords
        tags = self.base_generator._optimize_tags(
            tags=keywords, limit=self.platform_adapter.get_tag_limit()
        )

        # Calculate quality metrics
        quality_score = self.base_generator._calculate_quality_score(
            title=optimized_title, description=optimized_description, tags=tags
        )

        keyword_density = self.base_generator._calculate_keyword_density(
            text=optimized_description, keywords=keywords
        )

        readability_score = self.base_generator._calculate_readability(
            text=optimized_description
        )

        # Build YouTube-specific metadata
        youtube_metadata = {
            "category": "Travel & Events",
            "duration": video_duration,
            "video_type": "VIDEO" if content_type == ContentType.VIDEO else "SHORT",
            "journey_id": journey_id,
            "country": journey_data.get("country", "Unknown"),
        }

        # Create SEO metadata
        metadata = SEOMetadata(
            site_id=self.site_id,
            content_id=journey_id,
            platform=Platform.YOUTUBE,
            content_type=content_type,
            title=optimized_title,
            description=optimized_description,
            tags=tags,
            youtube_metadata=youtube_metadata,
            quality_score=quality_score,
            keyword_density=keyword_density,
            readability_score=readability_score,
        )

        return metadata

    def generate_short_metadata(
        self,
        journey_data: dict,
        short_index: int,
        total_shorts: int,
        variation: str | None = None,
    ) -> dict[str, any]:
        """
        Generate metadata for a single short/reel.

        Uses proven post variations for optimal engagement.

        Args:
            journey_data: Journey metadata dict
            short_index: Index of this short (1-based)
            total_shorts: Total number of shorts for this journey
            variation: Post variation type (optional, auto-selects if None)

        Returns:
            Dict with title, description, caption, tags
        """
        # Determine which variation to use
        variations = [
            "name_that_town",
            "pov_drive",
            "coastal_scenery",
            "alternative_route",
            "relaxation",
            "guess_location",
        ]

        if variation is None:
            # Cycle through variations
            variation = variations[(short_index - 1) % len(variations)]

        # Get highlights for this short
        highlights = journey_data.get("highlights", [])
        highlight = (
            highlights[(short_index - 1) % len(highlights)] if highlights else None
        )
        country = self._require_country(journey_data)

        # Build content based on variation
        if variation == "name_that_town":
            title = f"Name That Town 👀 {country} #{short_index}"
            caption = "Can you guess where this is? Drop your answer below! 👇"
            description = f"Driving through {highlight or country}."

        elif variation == "pov_drive":
            title = f"POV Through {country} 🚗 #{short_index}"
            caption = f"POV driving through {highlight or country} 🚗✨"
            description = (
                f"Join me for this scenic drive through {highlight or country}."
            )

        elif variation == "coastal_scenery":
            title = f"{highlight or 'Scenic View'} 🌊 {country}"
            caption = f"The views in {country} never disappoint 😍"
            description = f"Breathtaking scenery in {highlight or country}."

        elif variation == "alternative_route":
            title = f"Taking the Scenic Route 🚗 {country}"
            caption = "Sometimes the long way is the best way 🌄"
            description = f"Exploring {highlight or country} via the scenic route."

        elif variation == "relaxation":
            title = f"Peaceful Drive 🚗 {country} ASMR"
            caption = "Just pure driving relaxation 😌🚗"
            description = (
                f"Relaxing drive through {highlight or country}. Perfect for unwinding."
            )

        else:  # guess_location
            title = f"Guess This Location 📍 {country}"
            caption = "Where am I? 🤔 First correct answer wins! 🏆"
            description = f"Driving through {highlight or country}."

        # Generate tags
        base_tags = [
            country.lower(),
            "road trip",
            "pov drive",
            "travel",
            "scenic drive",
        ]

        if highlight:
            base_tags.append(highlight.lower())

        # Return metadata dict
        return {
            "title": title,
            "description": description,
            "caption": caption,
            "tags": base_tags,
            "variation": variation,
        }

    @staticmethod
    def _format_distance(distance_km, country: str) -> str:
        """Country-aware distance string.

        US journeys read in miles; everywhere else stays metric. Non-US output is
        byte-identical to the old ``{distance_km}km``; US footage converts to a
        rounded ``{miles}mi`` so a km-only distance never ships on a US drive.
        """
        if (country or "").strip().lower() in _US_COUNTRY_NAMES:
            try:
                miles = round(float(distance_km) * _KM_TO_MILES)
            except (TypeError, ValueError):
                miles = 0
            return f"{miles}mi"
        return f"{distance_km}km"

    def _build_longform_content(self, journey_data: dict) -> tuple[str, str]:
        """Build topic and body for long-form video."""
        start = journey_data.get("start_location", "")
        end = journey_data.get("end_location", "")
        country = self._require_country(journey_data)
        distance_km = journey_data.get("total_distance_km", 0)
        highlights = journey_data.get("highlights", [])

        # Build compelling title
        topic = f"POV Drive: {start} to {end} 🚗 {country}"

        # Build rich description
        description_parts = [
            f"Join me for a scenic {self._format_distance(distance_km, country)} drive "
            f"from {start} to {end} in {country}.",
            "",
        ]

        if highlights:
            description_parts.append("Highlights along the way:")
            for highlight in highlights:
                description_parts.append(f"• {highlight}")
            description_parts.append("")

        description_parts.extend(
            [
                "🚗 Driving Experience:",
                f"This {distance_km}km journey takes you across {country}.",
                "Perfect for road trip planning or just enjoying the scenery from home.",
                "",
                "📍 Route:",
                f"Starting Point: {start}",
                f"Ending Point: {end}",
                "",
                "👍 If you enjoyed this drive, don't forget to like and subscribe for more scenic journeys!",
                "",
                "#POVDrive #RoadTrip #ScenicDrive #Travel",
            ]
        )

        body = "\n".join(description_parts)

        return topic, body

    def _build_short_content(self, journey_data: dict) -> tuple[str, str]:
        """Build topic and body for short/reel.

        Uses the journey's own title/route rather than labelling every short
        with the journey's first highlight, and makes no subjective scenery
        claims about footage this method never sees.
        """
        country = self._require_country(journey_data)
        title = journey_data.get("title", "")
        start = journey_data.get("start_location", "")
        end = journey_data.get("end_location", "")

        if title:
            topic = f"{title} 🚗 {country}"
        elif start and end:
            topic = f"{start} to {end} 🚗 {country}"
        else:
            topic = f"{country} 🚗"

        if start and end:
            body = f"A drive from {start} to {end} in {country}."
        else:
            body = f"A drive in {country}."

        return topic, body
