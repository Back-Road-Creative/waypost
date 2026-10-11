"""
Core SEO Metadata structures.

Defines the SEOMetadata dataclass and related enums for platform and content types.
"""

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional


class Platform(Enum):
    """Supported platforms for SEO optimization"""

    YOUTUBE = "youtube"
    BLOG = "blog"
    TWITTER = "twitter"
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"
    LINKEDIN = "linkedin"


class ContentType(Enum):
    """Types of content that can be optimized"""

    VIDEO = "video"
    SHORT = "short"
    BLOG_POST = "blog_post"
    SOCIAL_POST = "social_post"
    PODCAST = "podcast"
    TRAIL_GUIDE = "trail_guide"


@dataclass(frozen=True)
class SEOMetadata:
    """
    Complete SEO metadata for any content type across any platform.

    Frozen (immutable) to ensure event sourcing integrity and thread safety.
    All state changes must create new instances.
    """

    # Identity (REQUIRED)
    site_id: str  # Multi-site isolation key
    content_id: str  # Unique within site
    platform: Platform  # Target platform
    content_type: ContentType  # Content category

    # Core SEO Fields (REQUIRED)
    title: str  # Primary headline
    description: str  # Long-form description
    tags: List[str]  # Keywords/hashtags

    # Extended Metadata (OPTIONAL)
    url: Optional[str] = None  # Canonical URL
    canonical_url: Optional[str] = None  # SEO canonical if different
    thumbnail_url: Optional[str] = None  # Visual preview
    author: Optional[str] = None  # Content creator
    published_date: Optional[str] = None  # ISO 8601 timestamp

    # Platform-Specific Data (OPTIONAL)
    youtube_metadata: Dict = field(default_factory=dict)
    # Example: {"playlist": "Season 1", "category": "Science & Technology"}

    blog_metadata: Dict = field(default_factory=dict)
    # Example: {"categories": ["Tech"], "series": "Trail Reviews"}

    social_metadata: Dict = field(default_factory=dict)
    # Example: {"cta": "Link in bio", "mention": "@examplehandle"}

    # SEO Quality Metrics (AUTO-GENERATED)
    quality_score: Optional[float] = None  # 0.0-1.0
    keyword_density: Optional[float] = None  # Primary keyword %
    readability_score: Optional[float] = None  # Flesch reading ease

    # System Metadata (AUTO-GENERATED)
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    generated_by: str = "seo_manager"
    version: int = 1  # Schema version
    validation_status: str = "pending"  # pending/valid/invalid
    validation_errors: List[str] = field(default_factory=list)

    # Auto-Fix Tracking (AUTO-GENERATED)
    fixes_applied: List[str] = field(default_factory=list)
    # Example: ["title_truncated_to_100", "added_missing_hashtags"]

    def to_dict(self) -> Dict:
        """
        Serialize to JSON-compatible dict.

        Converts enums to string values for JSON serialization.
        """
        data = asdict(self)
        # Convert enums to strings
        data["platform"] = self.platform.value
        data["content_type"] = self.content_type.value
        return data

    @classmethod
    def from_dict(cls, data: Dict) -> "SEOMetadata":
        """
        Deserialize from dict.

        Converts string values back to enums.
        """
        # Create a copy to avoid modifying input
        data = data.copy()

        # Convert strings back to enums
        data["platform"] = Platform(data["platform"])
        data["content_type"] = ContentType(data["content_type"])

        return cls(**data)

    def with_fixes(self, fixes: List[str]) -> "SEOMetadata":
        """
        Create new instance with fixes applied (immutable pattern).

        Since dataclass is frozen, we use dataclasses.replace() to create
        a new instance with updated fixes_applied list.

        Args:
            fixes: List of fix IDs to append to fixes_applied

        Returns:
            New SEOMetadata instance with updated fixes_applied
        """
        updated_fixes = list(self.fixes_applied) + fixes
        return replace(self, fixes_applied=updated_fixes)
