"""
Theme Rotator - A/B Testing for Title/Caption Themes.

Rotates through different caption/title themes for videos, tracks performance,
and recommends the best-performing themes. Learns over time from YouTube Analytics.

Themes are more than just titles - they include:
- Title format/template
- Caption style
- Hashtag strategy
- Emoji usage
- Call-to-action style

Integrates with:
- YouTube Analytics (views, CTR, engagement)
- SEOManager (storage)
- ThumbnailABTracker (coordinate with thumbnail tests)
"""

import json
import logging
import random
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from seo_manager.fsio import atomic_write_json

from . import evidence_age
from .evidence_age import EvidenceState

logger = logging.getLogger(__name__)


class ContentTheme(Enum):
    """Pre-defined content themes for a scenic-drive channel."""

    # Interactive/Engagement themes
    GUESS_LOCATION = "guess_location"
    NAME_THAT_TOWN = "name_that_town"
    WHERE_AM_I = "where_am_i"

    # Descriptive themes
    POV_DRIVE = "pov_drive"
    SCENIC_DRIVE = "scenic_drive"
    ROAD_TRIP = "road_trip"

    # Emotional/Experience themes
    RELAXING_DRIVE = "relaxing_drive"
    PEACEFUL_JOURNEY = "peaceful_journey"

    # Utility themes
    TRIP_PLANNING = "trip_planning"
    VIRTUAL_TOUR = "virtual_tour"

    # Location-focused
    EXPLORE_LOCATION = "explore_location"
    HIDDEN_GEM = "hidden_gem"

    # Photo voice styles (category="photo")
    VOICE_FIRST_PERSON = "voice_first_person"
    VOICE_MINIMAL_ZEN = "voice_minimal_zen"
    VOICE_ENGAGING = "voice_engaging"


@dataclass
class ThemeTemplate:
    """Template for a content theme."""

    theme: ContentTheme
    title_template: str  # Use {location}, {date}, {weather} placeholders
    caption_template: str
    hashtags: list[str]
    emojis: list[str]
    cta: str  # Call to action
    thumbnail_text: str
    description: str


# Default theme templates
THEME_TEMPLATES: dict[ContentTheme, ThemeTemplate] = {
    ContentTheme.GUESS_LOCATION: ThemeTemplate(
        theme=ContentTheme.GUESS_LOCATION,
        title_template="Guess the Location 📍",
        caption_template="""Guess the Location! 📍

Can you guess where this is? Drop your answer below! 👇

📅 Filmed: {date}""",
        hashtags=["GuessTheLocation", "POVDriving", "ScenicDrive", "RoadTrip"],
        emojis=["📍", "🚗", "🇺🇸", "👇"],
        cta="Can you guess where this is? Comment below!",
        thumbnail_text="GUESS THE LOCATION",
        description="Interactive guessing game format - highest engagement",
    ),
    ContentTheme.NAME_THAT_TOWN: ThemeTemplate(
        theme=ContentTheme.NAME_THAT_TOWN,
        title_template="Name That Town 👀",
        caption_template="""Name that town! 👀

Can you name the town before we reach it? 🤔

🚗 POV Drive""",
        hashtags=["NameThatTown", "POVDriving", "GuessWhere", "RoadTrip"],
        emojis=["👀", "🤔", "📍", "🚗"],
        cta="Name the town in the comments!",
        thumbnail_text="NAME THAT TOWN",
        description="Town-focused guessing variant",
    ),
    ContentTheme.WHERE_AM_I: ThemeTemplate(
        theme=ContentTheme.WHERE_AM_I,
        title_template="Where Am I? 🤔 Mystery Drive",
        caption_template="""Where am I? 🤔

First to guess correctly wins! 🏆

🎥 Full POV Drive""",
        hashtags=["WhereAmI", "GuessTheLocation", "POVDrive", "Travel"],
        emojis=["🤔", "🏆", "🌍", "🎥"],
        cta="Can you figure out where I am?",
        thumbnail_text="WHERE AM I?",
        description="Mystery/challenge format",
    ),
    ContentTheme.POV_DRIVE: ThemeTemplate(
        theme=ContentTheme.POV_DRIVE,
        title_template="POV Drive: {location} 🚗",
        caption_template="""POV driving through {location} 🚗

Experience the journey from the driver's seat!

📍 {location}
📅 {date}
🎥 Full POV Experience""",
        hashtags=["POVDrive", "POVDriving", "DashCam", "ScenicDrive"],
        emojis=["🚗", "📍", "🎥", "🛣️"],
        cta="Enjoy the ride!",
        thumbnail_text="POV DRIVE",
        description="Straightforward POV driving content",
    ),
    ContentTheme.SCENIC_DRIVE: ThemeTemplate(
        theme=ContentTheme.SCENIC_DRIVE,
        title_template="Scenic Drive: {location} 🌄",
        caption_template="""Scenic drive through {location} 🌄

Sit back, relax, and enjoy the views!

📍 {location}
🌊 Scenery ahead""",
        hashtags=["ScenicDrive", "ScenicRoute", "NatureDrive", "Travel"],
        emojis=["🌄", "🌊", "🏔️", "🌲"],
        cta="Which view is your favorite?",
        thumbnail_text="SCENIC DRIVE",
        description="Focus on beautiful scenery",
    ),
    ContentTheme.RELAXING_DRIVE: ThemeTemplate(
        theme=ContentTheme.RELAXING_DRIVE,
        title_template="Relaxing Drive 🍀 {location}",
        caption_template="""Peaceful POV drive through {location} 🍀

Perfect for:
🧘 Relaxation
📚 Study background
🌙 Wind down time

No music, just the road 🎧""",
        hashtags=["RelaxingDrive", "PeacefulDrive", "CalmingVideo", "ASMR"],
        emojis=["🍀", "🧘", "🌙", "🎧"],
        cta="Let this peaceful drive calm your mind",
        thumbnail_text="RELAXING DRIVE",
        description="Calm, stress-relief focused",
    ),
    ContentTheme.ROAD_TRIP: ThemeTemplate(
        theme=ContentTheme.ROAD_TRIP,
        title_template="{location} Road Trip 🗺️",
        caption_template="""Road trip through {location}! 🗺️

Join me on this adventure.

📍 {location}
🚗 Full journey
💾 Save for your trip planning!""",
        hashtags=["RoadTrip", "TravelUSA", "DriveWithMe", "Adventure"],
        emojis=["🗺️", "🚗", "💾", "🌟"],
        cta="Save this for your next road trip!",
        thumbnail_text="ROAD TRIP",
        description="Adventure/journey focused",
    ),
    ContentTheme.TRIP_PLANNING: ThemeTemplate(
        theme=ContentTheme.TRIP_PLANNING,
        title_template="{location} Road Trip Guide 📍",
        caption_template="""Planning a trip to {location}? 🗺️

This POV drive shows you exactly what to expect on this route!

📍 Route: {location}
💾 Save for your itinerary!""",
        hashtags=["TripPlanning", "TravelGuide", "RoadTripPlanning", "Itinerary"],
        emojis=["📍", "🗺️", "💾", "✈️"],
        cta="Save this for your trip planning!",
        thumbnail_text="TRIP GUIDE",
        description="Practical/planning focused",
    ),
    ContentTheme.VIRTUAL_TOUR: ThemeTemplate(
        theme=ContentTheme.VIRTUAL_TOUR,
        title_template="Virtual Tour: {location} 🎥",
        caption_template="""Take a virtual tour of {location}! 🎥

Experience this drive from anywhere in the world.

🌍 {location}
🚗 POV Driving Tour""",
        hashtags=["VirtualTour", "VirtualTravel", "ArmchairTravel", "Explore"],
        emojis=["🎥", "🌍", "🚗", "✨"],
        cta="Where should I drive next?",
        thumbnail_text="VIRTUAL TOUR",
        description="Armchair travel focused",
    ),
    ContentTheme.HIDDEN_GEM: ThemeTemplate(
        theme=ContentTheme.HIDDEN_GEM,
        title_template="Off the Beaten Path: {location} 💎",
        caption_template="""Off the beaten path 💎 {location}

A quieter route worth the detour.

📍 {location}
🚗 POV Drive""",
        hashtags=["HiddenGem", "SecretSpot", "OffTheBeatenPath", "LocalSecret"],
        emojis=["💎", "🤫", "✨", "📍"],
        cta="Have you been here? Share in comments!",
        thumbnail_text="HIDDEN GEM",
        description="Discovery/exclusive feel",
    ),
    ContentTheme.PEACEFUL_JOURNEY: ThemeTemplate(
        theme=ContentTheme.PEACEFUL_JOURNEY,
        title_template="Peaceful Journey: {location} 🌿",
        caption_template="""Peaceful journey through {location} 🌿

Let the road unwind your mind...

📍 {location}
🎧 Perfect for meditation or focus""",
        hashtags=["PeacefulJourney", "MindfulDriving", "CalmVibes", "ZenDrive"],
        emojis=["🌿", "🎧", "☮️", "🧘"],
        cta="Save for when you need a mental escape",
        thumbnail_text="PEACEFUL JOURNEY",
        description="Mindfulness/zen focused",
    ),
    ContentTheme.EXPLORE_LOCATION: ThemeTemplate(
        theme=ContentTheme.EXPLORE_LOCATION,
        title_template="Explore {location} 🧭",
        caption_template="""Explore {location} with me! 🧭

Join this journey.

📍 {location}
🚗 Full exploration drive""",
        hashtags=["Explore", "Discovery", "TravelDrive", "Adventure"],
        emojis=["🧭", "🗺️", "🚗", "✨"],
        cta="Where should I explore next?",
        thumbnail_text="EXPLORE",
        description="Discovery/exploration focused",
    ),
}


@dataclass
class ThemePerformance:
    """Performance data for a single video with a theme."""

    content_id: str
    video_id: str
    theme: ContentTheme
    created_at: str

    # Performance metrics. None = the platform never reported it: unknown,
    # not a measurement of zero (a reported 0 is still a 0).
    impressions: int | None = None
    views: int | None = None
    # None = never measured. A rate whose denominator was never observed is
    # unknown, not a measurement of zero.
    ctr: float | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    avg_view_duration: float = 0.0

    # Metadata
    location: str = ""
    last_updated: str | None = None


@dataclass
class ThemeAnalysis:
    """Aggregated analysis for a theme."""

    theme: ContentTheme
    sample_size: int
    total_views: int
    avg_views: float
    avg_ctr: float | None  # None when no usage in the sample was measured
    # (likes + comments + shares) / views; None when no usage in the sample
    # reported any of the three
    avg_engagement: float | None
    confidence_level: float
    vs_average: float  # Percentage better/worse than average
    is_top_performer: bool

    # Age of the evidence: the most recent `last_updated` among the usages
    # this analysis averaged -- when the data was learned, never when a
    # report was printed.
    newest_sample_at: str | None = None
    evidence_age_days: float | None = None
    evidence: EvidenceState = EvidenceState.UNDATED


@dataclass(frozen=True)
class ThemeSelection:
    """A chosen theme and the age of the evidence that chose it.

    Frozen, and every field is required, so `_select_best_performer` cannot
    hand back a bare winner: a caller holding a theme is holding its age and
    sample count too.

    `basis` says what picked it -- "performance" (a measured best) or
    "least_used" (exploration, which claims nothing). Exploration carries
    NOTHING_MEASURED and a sample size of 0 rather than a fabricated age.
    A STALE selection is still returned: the last valid answer, labelled.
    """

    theme: ContentTheme
    basis: str
    evidence: EvidenceState
    sample_size: int
    evidence_newest_at: str | None
    evidence_age_days: float | None

    @property
    def is_stale(self) -> bool:
        return self.evidence is EvidenceState.STALE

    def age_phrase(self) -> str:
        """Human-readable age of the evidence, for reports and logs."""
        return evidence_age.describe(self.evidence_newest_at, self.evidence_age_days)


class ThemeRotator:
    """
    Manages theme rotation and A/B testing for content.

    Strategies:
    1. EXPLORE: Evenly distribute among all themes to gather data
    2. EXPLOIT: Favor high-performing themes
    3. BALANCED: Mix of exploration and exploitation (recommended)

    Example:
        rotator = ThemeRotator(state_dir=Path("./data/themes"))

        # Get next theme to use
        theme = rotator.get_next_theme(location="Example Region")
        template = rotator.get_template(theme)

        # Record usage
        rotator.record_usage(
            content_id="example-snow-drive",
            video_id="xyz123",
            theme=theme,
            location="Example Region"
        )

        # Update with analytics
        rotator.update_performance("example-snow-drive", views=5000, ctr=0.08)

        # Get recommendations
        best_themes = rotator.get_top_themes(n=3)
    """

    MIN_SAMPLE_SIZE = 3  # Minimum videos per theme for analysis
    MIN_VIEWS = 100  # Minimum views to count performance

    def __init__(
        self,
        state_dir: Path | None = None,
        strategy: str = "balanced",  # explore, exploit, balanced
        exploration_rate: float = 0.3,  # For balanced strategy
    ):
        """
        Initialize the theme rotator.

        Args:
            state_dir: Directory for persistent state
            strategy: Rotation strategy (explore/exploit/balanced)
            exploration_rate: Probability of trying new theme (balanced mode)
        """
        self.state_dir = state_dir or Path("./data/theme_rotation")
        self.strategy = strategy
        self.exploration_rate = exploration_rate

        self.state_dir.mkdir(parents=True, exist_ok=True)

        # Theme usage tracking
        self.usage: dict[str, ThemePerformance] = {}
        self.theme_counts: dict[ContentTheme, int] = defaultdict(int)

        self._load_state()

    def get_next_theme(
        self,
        location: str = "",
        exclude_recent: int = 2,
        weather: str = "",
        category: str = "video",
    ) -> ContentTheme:
        """
        Get the next theme to use based on rotation strategy.

        Returns the bare theme for callers that only need something to use
        next; `get_next_theme_with_evidence` returns the same choice with
        the age and size of the evidence that made it.
        """
        return self.get_next_theme_with_evidence(
            location=location,
            exclude_recent=exclude_recent,
            weather=weather,
            category=category,
        ).theme

    def get_next_theme_with_evidence(
        self,
        location: str = "",
        exclude_recent: int = 2,
        weather: str = "",
        category: str = "video",
    ) -> ThemeSelection:
        """
        Get the next theme together with the evidence behind the choice.

        Args:
            location: Location for context-aware theme selection
            exclude_recent: Don't repeat last N themes
            weather: Optional weather context
            category: "video" or "photo"

        Returns:
            ThemeSelection -- theme, what picked it, and the evidence age
        """
        analyses = self.get_theme_analysis(category=category)
        recent_themes = self._get_recent_themes(exclude_recent, category=category)

        if category == "photo":
            all_themes = [t for t in ContentTheme if t.value.startswith("voice_")]
        else:
            all_themes = [t for t in ContentTheme if not t.value.startswith("voice_")]

        available_themes = [t for t in all_themes if t not in recent_themes]

        if not available_themes:
            available_themes = list(all_themes) if all_themes else list(ContentTheme)

        if self.strategy == "explore":
            # Pure exploration - pick least-used theme
            return self._select_least_used(available_themes)

        elif self.strategy == "exploit":
            # Pure exploitation - pick best performer
            return self._select_best_performer(available_themes, analyses)

        else:  # balanced
            # Mix: explore with probability, otherwise exploit
            if random.random() < self.exploration_rate:
                return self._select_least_used(available_themes)
            else:
                return self._select_best_performer(available_themes, analyses)

    def _select_least_used(self, available: list[ContentTheme]) -> ThemeSelection:
        """Select the least-used theme -- exploration, which measures nothing.

        This is not a "best": it claims no evidence, so it carries
        NOTHING_MEASURED and no age rather than a fabricated freshness.
        """
        return ThemeSelection(
            theme=min(available, key=lambda t: self.theme_counts.get(t, 0)),
            basis="least_used",
            evidence=EvidenceState.NOTHING_MEASURED,
            sample_size=0,
            evidence_newest_at=None,
            evidence_age_days=None,
        )

    def _select_best_performer(
        self, available: list[ContentTheme], analyses: dict[ContentTheme, ThemeAnalysis]
    ) -> ThemeSelection:
        """Select the best-performing theme, with the age of its evidence.

        Returns a ThemeSelection, never a bare theme: a winner and the age
        of the data behind it cannot be separated. Stale evidence still
        wins -- it is the last valid answer, labelled, not a reason to
        invent a new one.
        """
        # Filter to themes with enough data
        valid = [t for t in available if t in analyses]

        if not valid:
            # Nothing measured for any available theme - explore instead.
            return self._select_least_used(available)

        # Pick highest average views
        best = max(valid, key=lambda t: analyses[t].avg_views)
        analysis = analyses[best]
        return ThemeSelection(
            theme=best,
            basis="performance",
            evidence=analysis.evidence,
            sample_size=analysis.sample_size,
            evidence_newest_at=analysis.newest_sample_at,
            evidence_age_days=analysis.evidence_age_days,
        )

    def _get_recent_themes(self, n: int, category: str = "video") -> list[ContentTheme]:
        """Get the N most recently used themes, filtered by category."""
        filtered = [
            u
            for u in self.usage.values()
            if (category == "photo" and u.theme.value.startswith("voice_"))
            or (category == "video" and not u.theme.value.startswith("voice_"))
        ]
        sorted_usage = sorted(filtered, key=lambda u: u.created_at, reverse=True)
        return [u.theme for u in sorted_usage[:n]]

    def get_template(self, theme: ContentTheme) -> ThemeTemplate:
        """Get the template for a theme."""
        return THEME_TEMPLATES.get(theme, THEME_TEMPLATES[ContentTheme.POV_DRIVE])

    def format_content(
        self, theme: ContentTheme, location: str, date: str = "", weather: str = ""
    ) -> dict[str, str]:
        """
        Format content using the theme template.

        Returns dict with: title, caption, hashtags, cta, thumbnail_text
        """
        template = self.get_template(theme)

        if not date:
            date = datetime.now().strftime("%Y-%m-%d")

        # Format templates
        title = template.title_template.format(
            location=location, date=date, weather=weather
        )

        caption = template.caption_template.format(
            location=location, date=date, weather=weather
        )

        hashtags = " ".join(f"#{tag}" for tag in template.hashtags)

        return {
            "title": title,
            "caption": caption,
            "hashtags": hashtags,
            "hashtag_list": template.hashtags,
            "cta": template.cta,
            "thumbnail_text": template.thumbnail_text,
            "emojis": template.emojis,
        }

    def record_usage(
        self,
        content_id: str,
        video_id: str,
        theme: ContentTheme,
        location: str = "",
        category: str = "video",
    ) -> ThemePerformance:
        """Record that a theme was used for a video."""
        usage = ThemePerformance(
            content_id=content_id,
            video_id=video_id,
            theme=theme,
            created_at=datetime.now().isoformat(),
            location=location,
        )

        self.usage[content_id] = usage
        self.theme_counts[theme] += 1
        self._save_state()

        logger.info(f"Recorded theme: {content_id} -> {theme.value}")
        return usage

    def update_performance(
        self,
        content_id: str,
        views: int | None = None,
        impressions: int | None = None,
        ctr: float | None = None,
        likes: int | None = None,
        comments: int | None = None,
        shares: int | None = None,
        avg_view_duration: float | None = None,
    ) -> ThemePerformance | None:
        """Update performance metrics for a video."""
        if content_id not in self.usage:
            logger.warning(f"No usage record for: {content_id}")
            return None

        usage = self.usage[content_id]

        if views is not None:
            usage.views = views
        if impressions is not None:
            usage.impressions = impressions
        if ctr is not None:
            usage.ctr = ctr
        if likes is not None:
            usage.likes = likes
        if comments is not None:
            usage.comments = comments
        if shares is not None:
            usage.shares = shares
        if avg_view_duration is not None:
            usage.avg_view_duration = avg_view_duration

        usage.last_updated = datetime.now().isoformat()
        self._save_state()

        return usage

    def get_theme_analysis(
        self, category: str = "video"
    ) -> dict[ContentTheme, ThemeAnalysis]:
        """Analyze performance by theme, filtered by category."""
        # Group by theme, filtered by category
        theme_data: dict[ContentTheme, list[ThemePerformance]] = defaultdict(list)

        for usage in self.usage.values():
            if usage.views is not None and usage.views >= self.MIN_VIEWS:
                is_photo = usage.theme.value.startswith("voice_")
                if (category == "photo" and is_photo) or (
                    category == "video" and not is_photo
                ):
                    theme_data[usage.theme].append(usage)

        # Calculate overall averages
        all_views = [
            u.views
            for u in self.usage.values()
            if u.views is not None and u.views >= self.MIN_VIEWS
        ]
        overall_avg_views = sum(all_views) / len(all_views) if all_views else 0

        analyses = {}

        for theme, usages in theme_data.items():
            if len(usages) < self.MIN_SAMPLE_SIZE:
                continue

            # Calculate metrics
            views_list = [u.views for u in usages]
            avg_views = sum(views_list) / len(views_list)
            total_views = sum(views_list)

            # A measured 0.0 counts; an unmeasured (None) rate is never a 0.
            ctrs = [u.ctr for u in usages if u.ctr is not None]
            avg_ctr = sum(ctrs) / len(ctrs) if ctrs else None

            # Engagement rate. Every usage here passed ``views >= MIN_VIEWS``
            # above, so the denominator is never 0. A usage that reported none
            # of likes/comments/shares is left out, not counted as no
            # engagement; the ones that did report sum what they reported.
            engagements = []
            for u in usages:
                counts = [c for c in (u.likes, u.comments, u.shares) if c is not None]
                if counts:
                    engagements.append(sum(counts) / u.views)
            avg_engagement = (
                sum(engagements) / len(engagements) if engagements else None
            )

            # Confidence
            confidence = min(1.0, len(usages) / (self.MIN_SAMPLE_SIZE * 3))

            # vs average
            vs_avg = (
                (avg_views - overall_avg_views) / overall_avg_views * 100
                if overall_avg_views > 0
                else 0
            )

            # The age of THIS theme's samples, not of the report.
            newest_at = evidence_age.newest(u.last_updated for u in usages)
            age = evidence_age.age_days(newest_at)

            analyses[theme] = ThemeAnalysis(
                theme=theme,
                sample_size=len(usages),
                total_views=total_views,
                avg_views=avg_views,
                avg_ctr=avg_ctr,
                avg_engagement=avg_engagement,
                confidence_level=confidence,
                vs_average=vs_avg,
                is_top_performer=False,
                newest_sample_at=newest_at,
                evidence_age_days=age,
                evidence=evidence_age.classify(age),
            )

        # Mark top performer
        if analyses:
            top_theme = max(analyses.keys(), key=lambda t: analyses[t].avg_views)
            analyses[top_theme].is_top_performer = True

        return analyses

    def get_top_themes(self, n: int = 3) -> list[tuple[ContentTheme, ThemeAnalysis]]:
        """Get top N performing themes, each with the age of its evidence.

        Empty when nothing has been measured. It used to return three
        hard-coded theme names with no analysis, which a report rendered as
        a "#1" ranking -- an arbitrary first entry standing in for data
        nobody had.
        """
        analyses = self.get_theme_analysis()

        sorted_themes = sorted(
            analyses.items(), key=lambda x: x[1].avg_views, reverse=True
        )

        return sorted_themes[:n]

    def get_underperforming_themes(self) -> list[ContentTheme]:
        """Get themes performing below average (candidates for retirement)."""
        analyses = self.get_theme_analysis()

        if not analyses:
            return []

        avg_views = sum(a.avg_views for a in analyses.values()) / len(analyses)

        return [
            theme
            for theme, analysis in analyses.items()
            if analysis.avg_views < avg_views * 0.7  # 30% below average
            and analysis.confidence_level > 0.5  # Confident in data
        ]

    def generate_report(self) -> str:
        """Generate a human-readable theme performance report."""
        self.get_theme_analysis()
        top_themes = self.get_top_themes(3)

        lines = [
            "=" * 60,
            "THEME ROTATION A/B TEST REPORT",
            "=" * 60,
            # When this text was printed. NOT when anything was measured --
            # every ranking below carries its own age.
            f"Generated: {datetime.now().isoformat()} "
            "(report print time, not data time)",
            f"Total videos tracked: {len(self.usage)}",
            f"Rotation strategy: {self.strategy}",
            "",
            "THEME PERFORMANCE RANKINGS:",
            "-" * 40,
        ]

        if not top_themes:
            # The gap itself, not three theme names nobody measured.
            lines.append("\n  NO THEME PERFORMANCE MEASURED YET — no ranking")

        for i, (theme, analysis) in enumerate(top_themes, 1):
            indicator = " 🏆" if analysis.is_top_performer else ""
            avg_ctr = (
                "not measured"
                if analysis.avg_ctr is None
                else f"{analysis.avg_ctr:.2%}"
            )
            lines.append(
                f"\n  #{i} {theme.value.upper()}{indicator}\n"
                f"      Sample: {analysis.sample_size} videos\n"
                f"      Avg Views: {analysis.avg_views:,.0f}\n"
                f"      Avg CTR: {avg_ctr}\n"
                f"      vs Average: {analysis.vs_average:+.1f}%\n"
                f"      Confidence: {analysis.confidence_level:.0%}\n"
                f"      Evidence: {analysis.evidence.value.upper()}, "
                f"{evidence_age.describe(analysis.newest_sample_at, analysis.evidence_age_days)}"
            )

        # Underperformers
        underperforming = self.get_underperforming_themes()
        if underperforming:
            lines.extend(["", "UNDERPERFORMING THEMES (consider reducing):", "-" * 40])
            for theme in underperforming:
                lines.append(f"  ⚠️  {theme.value}")

        # Usage distribution
        lines.extend(["", "THEME USAGE DISTRIBUTION:", "-" * 40])
        total = sum(self.theme_counts.values()) or 1
        for theme in sorted(
            self.theme_counts.keys(), key=lambda t: self.theme_counts[t], reverse=True
        ):
            count = self.theme_counts[theme]
            pct = count / total * 100
            bar = "█" * int(pct / 5)
            lines.append(f"  {theme.value:20} {bar} {count} ({pct:.1f}%)")

        lines.append("=" * 60)

        return "\n".join(lines)

    def _load_state(self):
        """Load state from disk."""
        state_file = self.state_dir / "theme_rotation_state.json"

        if state_file.exists():
            try:
                with open(state_file) as f:
                    data = json.load(f)

                for content_id, usage_data in data.get("usage", {}).items():
                    usage_data["theme"] = ContentTheme(usage_data["theme"])
                    # Legacy state (written when ctr defaulted to 0.0) stores
                    # a never-measured rate as 0.0. With zero impressions the
                    # rate has no denominator, so it is unknown, not zero.
                    if (
                        usage_data.get("ctr") == 0.0
                        and usage_data.get("impressions", 0) == 0
                    ):
                        usage_data["ctr"] = None
                    self.usage[content_id] = ThemePerformance(**usage_data)

                for theme_str, count in data.get("theme_counts", {}).items():
                    self.theme_counts[ContentTheme(theme_str)] = count

                logger.debug(f"Loaded {len(self.usage)} theme records")

            except (json.JSONDecodeError, TypeError, KeyError) as e:
                logger.warning(f"Could not load theme state: {e}")

    def _save_state(self):
        """Save state to disk."""
        state_file = self.state_dir / "theme_rotation_state.json"

        data = {
            "usage": {
                content_id: {**asdict(usage), "theme": usage.theme.value}
                for content_id, usage in self.usage.items()
            },
            "theme_counts": {
                theme.value: count for theme, count in self.theme_counts.items()
            },
            "last_updated": datetime.now().isoformat(),
            "strategy": self.strategy,
            "exploration_rate": self.exploration_rate,
        }

        atomic_write_json(state_file, data)


# Convenience function for pipeline integration
def get_rotated_theme(
    location: str, state_dir: Path | None = None
) -> tuple[ContentTheme, dict[str, str]]:
    """
    Get the next theme and formatted content for a video.

    Convenience wrapper for pipeline integration.

    Returns:
        Tuple of (theme, formatted_content_dict)
    """
    rotator = ThemeRotator(state_dir=state_dir)
    theme = rotator.get_next_theme(location=location)
    content = rotator.format_content(theme, location)

    return theme, content
