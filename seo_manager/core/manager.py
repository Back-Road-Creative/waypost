"""
SEO Manager - Main orchestrator for SEO operations.

Provides the primary API interface for:
- Generating SEO metadata
- Saving to event store
- Retrieving metadata
- Site-scoped operations
"""

import importlib.util
import os
import sys
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..analytics.tracker import AnalyticsTracker
from ..generation.base import BaseSEOGenerator
from ..platforms.base import BasePlatformAdapter
from ..storage.event_store import SEOEventStore
from .events import EventType, SEOEvent
from .metadata import ContentType, Platform, SEOMetadata

# Directory holding ``intel_db.py``. No default: the IntelDB integration is
# opt-in, and an unset INTEL_SCRIPTS_DIR means "not configured".
_INTEL_SCRIPTS_DIR = os.getenv("INTEL_SCRIPTS_DIR", "")


def _get_intel_db(db_path: str):
    """Load IntelDB from ``$INTEL_SCRIPTS_DIR/intel_db.py``; returns None on any failure
    (including an unset INTEL_SCRIPTS_DIR).

    Loaded by file path, never by module name: a plain ``from intel_db import``
    after a sys.path insert is satisfied by any ``intel_db`` already in
    sys.modules or earlier on sys.path, so a missing configured file could
    still produce a database. Nothing is added to sys.path or sys.modules.
    """
    if not _INTEL_SCRIPTS_DIR:
        print(
            "WARNING: Could not import IntelDB: INTEL_SCRIPTS_DIR is not set",
            file=sys.stderr,
        )
        return None
    module_path = Path(_INTEL_SCRIPTS_DIR) / "intel_db.py"
    if not module_path.is_file():
        print(
            f"WARNING: Could not import IntelDB: {module_path} not found",
            file=sys.stderr,
        )
        return None
    spec = importlib.util.spec_from_file_location("intel_db", module_path)
    if spec is None or spec.loader is None:
        print(
            f"WARNING: Could not import IntelDB: no loader for {module_path}",
            file=sys.stderr,
        )
        return None
    try:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as exc:
        print(
            f"WARNING: Could not import IntelDB from {module_path}: {exc}",
            file=sys.stderr,
        )
        return None
    try:
        return module.IntelDB(db_path)
    except Exception as exc:
        print(f"WARNING: Could not open IntelDB at {db_path!r}: {exc}", file=sys.stderr)
        return None


def _log_seo_to_intel_db(
    content_id: str,
    platform: str,
    title: str,
    tag_count: int,
    generated_at: str,
) -> None:
    """Write a saved SEO metadata item to IntelDB. Non-fatal — prints warnings only."""
    db_path = os.getenv("INTEL_DB_PATH", "")
    if not db_path:
        return

    db = _get_intel_db(db_path)
    if db is None:
        return

    try:
        run_id = db.start_scrape_run(f"seo-{platform}", source_name=content_id)
        date_str = generated_at[:10]  # "YYYY-MM-DD"
        summary = f"SEO metadata: {title} [{platform}]"
        db.upsert_news_item(
            date=date_str, summary=summary, article_links=[], source_count=tag_count
        )
        db.finish_scrape_run(run_id, items_found=1, items_imported=1)
    except Exception as exc:
        print(f"WARNING: IntelDB write failed for {content_id}: {exc}", file=sys.stderr)
    finally:
        db.close()


class SEOManager:
    """
    Main interface for SEO management operations.

    Each instance is bound to a site_id and platform for data isolation.

    Example:
        manager = SEOManager(
            site_id="my_youtube_channel",
            platform=YouTubeAdapter(),
            storage_path=Path("./data/seo")
        )

        metadata = manager.generate(
            content_type=ContentType.VIDEO,
            topic="AI Safety",
            body="Discussion about AI safety..."
        )

        event_id = manager.save(metadata)
    """

    def __init__(
        self,
        site_id: str,
        platform: BasePlatformAdapter,
        storage_path: Optional[Path] = None,
        config: Optional[Dict] = None,
    ):
        """
        Initialize SEO Manager for a specific site and platform.

        Args:
            site_id: Unique identifier for this site/channel
            platform: Platform adapter (YouTubeAdapter, BlogAdapter, etc.)
            storage_path: Where to store SEO data (defaults to ./data/seo)
            config: Optional configuration overrides
        """
        self.site_id = site_id
        self.platform = platform
        self.config = config or {}

        # Initialize storage
        storage_path = Path(storage_path) if storage_path else Path("./data/seo")
        site_storage_path = storage_path / site_id

        self.store = SEOEventStore(storage_path=site_storage_path)

        # Initialize generator (platform-specific if available)
        self.generator = self._get_platform_generator(platform)

        # Initialize analytics tracker
        self.analytics = AnalyticsTracker(store=self.store, site_id=site_id)

    def _get_platform_generator(self, platform: BasePlatformAdapter):
        """
        Get platform-specific generator if available, otherwise use base.

        Args:
            platform: Platform adapter

        Returns:
            Generator instance
        """
        from ..core.metadata import Platform as PlatformEnum

        # Use platform-specific generators
        if platform.platform_type == PlatformEnum.YOUTUBE:
            from ..generation.youtube_generator import YouTubeMetadataGenerator

            return YouTubeMetadataGenerator(adapter=platform)
        elif platform.platform_type == PlatformEnum.BLOG:
            from ..generation.blog_generator import BlogMetadataGenerator

            return BlogMetadataGenerator(adapter=platform)
        else:
            # Default to base generator
            return BaseSEOGenerator()

    def generate(
        self,
        content_type: ContentType,
        topic: str,
        body: Optional[str] = None,
        transcript: Optional[List[Dict]] = None,
        existing_tags: Optional[List[str]] = None,
        base_tags: Optional[List[str]] = None,
        title_length_range: Optional[Tuple[int, int]] = None,
        **kwargs,
    ) -> SEOMetadata:
        """
        Generate SEO metadata for content.

        Args:
            content_type: Type of content (VIDEO, BLOG_POST, etc.)
            topic: Main topic/title seed
            body: Full content text (for blog posts)
            transcript: Dialogue transcript (for videos/podcasts)
            existing_tags: Pre-defined tags to include
            base_tags: Always-included base tags
            title_length_range: Optional (min, max) title length learned from
                analytics (`SEOOptimizer.get_optimal_title_length`). Advisory:
                it can only tighten the platform's title cap, only shorten,
                and only on a word boundary — see
                `BaseSEOGenerator._optimize_title`. Omitting it reproduces the
                previous behaviour exactly. Declared explicitly rather than
                left in **kwargs so it cannot be silently swallowed by a
                generator that does not understand it.
            **kwargs: Additional platform-specific parameters

        Returns:
            SEOMetadata with optimized title, description, tags, and quality scores
        """
        # Extract keywords from all sources
        keywords = self.generator._extract_keywords(topic, body, transcript)

        # Generate title (use generator's method if available, otherwise fallback)
        if hasattr(self.generator, "_generate_title"):
            title = self.generator._generate_title(topic, keywords, **kwargs)
        else:
            title = self._generate_title(topic, keywords, **kwargs)

        # Optimize title for platform (the cap always wins over the learned
        # range; the generator clamps one against the other)
        title = self.generator._optimize_title(
            title,
            limit=self.platform.get_title_limit(),
            target_range=title_length_range,
        )

        # Generate description (use generator's method if available)
        if hasattr(self.generator, "_generate_description"):
            description = self.generator._generate_description(
                topic, body, transcript, keywords, **kwargs
            )
        else:
            description = self._generate_description(
                topic, body, transcript, keywords, **kwargs
            )

        # Optimize description for platform
        description = self.generator._optimize_description(
            description, limit=self.platform.get_description_limit()
        )

        # Generate tags (use generator's method if available)
        if hasattr(self.generator, "_generate_tags"):
            # Pass base_tags in kwargs for YouTube generator
            kwargs_with_base = {**kwargs, "base_tags": base_tags}
            tags = self.generator._generate_tags(
                keywords, existing_tags, **kwargs_with_base
            )
        else:
            tags = self._generate_tags(keywords, existing_tags, base_tags)

        # Optimize tags for platform
        tags = self.generator._optimize_tags(tags, limit=self.platform.get_tag_limit())

        # Calculate quality metrics
        quality_score = self.generator._calculate_quality_score(
            title, description, tags
        )
        keyword_density = self.generator._calculate_keyword_density(
            title + " " + description, keywords
        )
        readability_score = self.generator._calculate_readability(description)

        # Generate content ID if not provided
        content_id = kwargs.get("content_id") or self.generator._generate_content_id(
            topic
        )

        # Create metadata
        metadata = SEOMetadata(
            site_id=self.site_id,
            content_id=content_id,
            platform=self.platform.platform_type,
            content_type=content_type,
            title=title,
            description=description,
            tags=tags,
            quality_score=quality_score,
            keyword_density=keyword_density,
            readability_score=readability_score,
            validation_status="pending",  # Will be validated later
            **self._get_platform_metadata(**kwargs),
        )

        return metadata

    def save(self, metadata: SEOMetadata) -> str:
        """
        Save metadata to storage and track analytics event.

        Args:
            metadata: SEO metadata to save

        Returns:
            Event ID for tracking
        """
        # Create event
        event = SEOEvent(
            event_id=str(uuid.uuid4()),
            event_type=EventType.METADATA_GENERATED,
            site_id=self.site_id,
            content_id=metadata.content_id,
            timestamp=metadata.generated_at,
            data=metadata.to_dict(),
        )

        # Append to store
        event_id = self.store.append_event(event)

        _log_seo_to_intel_db(
            content_id=metadata.content_id,
            platform=metadata.platform.value,
            title=metadata.title,
            tag_count=len(metadata.tags),
            generated_at=metadata.generated_at,
        )

        return event_id

    def get_metadata(self, content_id: str) -> Optional[SEOMetadata]:
        """
        Retrieve latest metadata for content.

        Args:
            content_id: Content identifier

        Returns:
            SEOMetadata if found, None otherwise
        """
        # Get all events for this content
        events = self.store.get_by_content_id(
            site_id=self.site_id, content_id=content_id
        )

        if not events:
            return None

        # Find latest METADATA_GENERATED or METADATA_UPDATED event
        metadata_events = [
            e
            for e in events
            if e.event_type
            in (EventType.METADATA_GENERATED, EventType.METADATA_UPDATED)
        ]

        if not metadata_events:
            return None

        # Get most recent
        latest_event = metadata_events[-1]

        # Deserialize metadata from event data
        return SEOMetadata.from_dict(latest_event.data)

    def _generate_title(self, topic: str, keywords: List[str], **kwargs) -> str:
        """
        Generate title from topic.

        Can be overridden by platform-specific generators.
        """
        # Simple implementation: use topic as-is
        return topic

    def _generate_description(
        self,
        topic: str,
        body: Optional[str],
        transcript: Optional[List[Dict]],
        keywords: List[str],
        **kwargs,
    ) -> str:
        """
        Generate description from content.

        Can be overridden by platform-specific generators.
        """
        if body:
            # Extract first paragraph
            paragraphs = body.split("\n\n")
            first_para = paragraphs[0] if paragraphs else body[:200]
            return first_para.strip()

        elif transcript:
            # Summarize from transcript
            first_lines = [entry.get("text", "") for entry in transcript[:2]]
            return f"Discussion about {topic}. " + " ".join(first_lines)[:150]

        else:
            # Fallback
            return f"Learn about {topic}. Comprehensive overview and insights."

    def _generate_tags(
        self,
        keywords: List[str],
        existing_tags: Optional[List[str]],
        base_tags: Optional[List[str]],
    ) -> List[str]:
        """
        Generate tag list from keywords and existing tags.

        Args:
            keywords: Extracted keywords
            existing_tags: User-provided tags
            base_tags: Always-included base tags

        Returns:
            Combined and deduplicated tag list
        """
        tags = []

        # Add base tags first
        if base_tags:
            tags.extend(base_tags)

        # Add existing tags
        if existing_tags:
            tags.extend(existing_tags)

        # Add extracted keywords
        tags.extend(keywords[:10])

        # Deduplicate (case-insensitive)
        seen = set()
        unique_tags = []
        for tag in tags:
            tag_lower = tag.lower()
            if tag_lower not in seen:
                seen.add(tag_lower)
                unique_tags.append(tag)

        return unique_tags

    def _get_platform_metadata(self, **kwargs) -> Dict:
        """
        Extract platform-specific metadata from kwargs.

        Returns:
            Dict with platform_metadata keys
        """
        result = {}

        # YouTube-specific
        if self.platform.platform_type == Platform.YOUTUBE:
            youtube_meta = {}
            if "category" in kwargs:
                youtube_meta["category"] = kwargs["category"]
            if "playlist" in kwargs:
                youtube_meta["playlist"] = kwargs["playlist"]
            if "visibility" in kwargs:
                youtube_meta["visibility"] = kwargs["visibility"]
            if youtube_meta:
                result["youtube_metadata"] = youtube_meta

        # Blog-specific
        elif self.platform.platform_type == Platform.BLOG:
            blog_meta = {}
            if "categories" in kwargs:
                blog_meta["categories"] = kwargs["categories"]
            if "series" in kwargs:
                blog_meta["series"] = kwargs["series"]
            if blog_meta:
                result["blog_metadata"] = blog_meta

        # Twitter-specific
        elif self.platform.platform_type == Platform.TWITTER:
            social_meta = {}
            if "cta" in kwargs:
                social_meta["cta"] = kwargs["cta"]
            if "mention" in kwargs:
                social_meta["mention"] = kwargs["mention"]
            if social_meta:
                result["social_metadata"] = social_meta

        return result
