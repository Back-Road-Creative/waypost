"""
Auto-Fix Engine for SEO optimization.

Automatically detects and fixes common SEO issues with confidence scoring.
"""

from dataclasses import dataclass, replace
from enum import Enum
from typing import Dict, List, Optional

from ..core.metadata import SEOMetadata
from ..platforms.base import BasePlatformAdapter


class FixConfidence(Enum):
    """Confidence levels for fixes"""

    HIGH = "high"  # >= 0.9 - auto-apply
    MEDIUM = "medium"  # 0.6-0.89 - flag for review
    LOW = "low"  # < 0.6 - log only


@dataclass
class Fix:
    """Represents a single SEO fix"""

    fix_id: str
    rule_name: str
    description: str
    confidence: float
    before_value: str
    after_value: str
    impact: str  # "title", "description", "tags"

    @property
    def confidence_level(self) -> FixConfidence:
        """Get confidence level category"""
        if self.confidence >= 0.9:
            return FixConfidence.HIGH
        elif self.confidence >= 0.6:
            return FixConfidence.MEDIUM
        else:
            return FixConfidence.LOW


@dataclass
class AutoFixResult:
    """Result of auto-fix operation"""

    metadata: SEOMetadata
    fixes_applied: List[Fix]
    fixes_flagged: List[Fix]
    fixes_logged: List[Fix]


class AutoFixEngine:
    """
    Automatically fixes SEO issues in metadata.

    Uses rule-based fixes with confidence scoring:
    - High confidence (>= 0.9): Auto-apply
    - Medium confidence (0.6-0.89): Flag for review
    - Low confidence (< 0.6): Log only
    """

    def __init__(
        self, platform: BasePlatformAdapter, confidence_threshold: float = 0.85
    ):
        """
        Initialize auto-fix engine.

        Args:
            platform: Platform adapter
            confidence_threshold: Minimum confidence for auto-apply
        """
        self.platform = platform
        self.confidence_threshold = confidence_threshold

        # Register fix rules
        self.rules = [
            self._fix_title_length,
            self._fix_description_length,
            self._fix_tag_count,
            self._fix_duplicate_tags,
        ]

    def fix(self, metadata: SEOMetadata, validation_errors: List[str]) -> AutoFixResult:
        """
        Apply auto-fixes to metadata.

        Args:
            metadata: SEO metadata with potential issues
            validation_errors: List of validation error messages

        Returns:
            AutoFixResult with updated metadata and fix details
        """
        fixes_applied = []
        fixes_flagged = []
        fixes_logged = []

        current_metadata = metadata

        # Run each fix rule
        for rule in self.rules:
            fix = rule(current_metadata, validation_errors)

            if not fix:
                continue

            # Apply based on confidence
            if fix.confidence >= 0.9:
                # High confidence: apply immediately
                current_metadata = self._apply_fix(current_metadata, fix)
                fixes_applied.append(fix)

            elif fix.confidence >= 0.6:
                # Medium confidence: flag for review
                fixes_flagged.append(fix)

            else:
                # Low confidence: log only
                fixes_logged.append(fix)

        return AutoFixResult(
            metadata=current_metadata,
            fixes_applied=fixes_applied,
            fixes_flagged=fixes_flagged,
            fixes_logged=fixes_logged,
        )

    def suggest_improvements(self, metadata: SEOMetadata) -> List[Dict]:
        """
        Get improvement suggestions without applying fixes.

        Args:
            metadata: Metadata to analyze

        Returns:
            List of suggestions with confidence scores
        """
        suggestions = []

        for rule in self.rules:
            fix = rule(metadata, [])

            if fix:
                suggestions.append(
                    {
                        "rule": fix.rule_name,
                        "description": fix.description,
                        "confidence": fix.confidence,
                        "impact": fix.impact,
                        "before": fix.before_value,
                        "after": fix.after_value,
                    }
                )

        return suggestions

    def _fix_title_length(
        self, metadata: SEOMetadata, errors: List[str]
    ) -> Optional[Fix]:
        """Fix title if too long"""
        limit = self.platform.get_title_limit()

        if len(metadata.title) > limit:
            # Truncate at word boundary
            truncated = metadata.title[: limit - 3].rsplit(" ", 1)[0] + "..."

            return Fix(
                fix_id="title_length_truncate",
                rule_name="Title Length Limit",
                description=f"Title exceeded {limit} chars, truncated",
                confidence=0.95,  # High: platform requirement
                before_value=metadata.title,
                after_value=truncated,
                impact="title",
            )

        return None

    def _fix_description_length(
        self, metadata: SEOMetadata, errors: List[str]
    ) -> Optional[Fix]:
        """Fix description if too long"""
        limit = self.platform.get_description_limit()

        if len(metadata.description) > limit:
            # Truncate at sentence boundary
            sentences = metadata.description.split(". ")
            truncated = ""

            for sentence in sentences:
                if len(truncated) + len(sentence) + 2 <= limit:
                    truncated += sentence + ". "
                else:
                    break

            if not truncated or len(truncated) > limit:
                truncated = metadata.description[: limit - 3] + "..."

            return Fix(
                fix_id="description_length_truncate",
                rule_name="Description Length Limit",
                description=f"Description exceeded {limit} chars",
                confidence=0.95,
                before_value=metadata.description,
                after_value=truncated,
                impact="description",
            )

        return None

    def _fix_tag_count(self, metadata: SEOMetadata, errors: List[str]) -> Optional[Fix]:
        """Fix if too many tags"""
        limit = self.platform.get_tag_limit()

        if len(metadata.tags) > limit:
            trimmed_tags = metadata.tags[:limit]

            return Fix(
                fix_id="tag_count_limit",
                rule_name="Tag Count Limit",
                description=f"Reduced tags from {len(metadata.tags)} to {limit}",
                confidence=0.90,
                before_value=", ".join(metadata.tags),
                after_value=", ".join(trimmed_tags),
                impact="tags",
            )

        return None

    def _fix_duplicate_tags(
        self, metadata: SEOMetadata, errors: List[str]
    ) -> Optional[Fix]:
        """Remove duplicate tags (case-insensitive)"""
        unique_tags = []
        seen = set()

        for tag in metadata.tags:
            tag_lower = tag.lower()
            if tag_lower not in seen:
                unique_tags.append(tag)
                seen.add(tag_lower)

        if len(unique_tags) < len(metadata.tags):
            return Fix(
                fix_id="duplicate_tags",
                rule_name="Remove Duplicate Tags",
                description=f"Removed {len(metadata.tags) - len(unique_tags)} duplicates",
                confidence=1.0,  # Highest: clear duplication
                before_value=", ".join(metadata.tags),
                after_value=", ".join(unique_tags),
                impact="tags",
            )

        return None

    def _apply_fix(self, metadata: SEOMetadata, fix: Fix) -> SEOMetadata:
        """
        Apply fix to metadata (immutable update).

        Args:
            metadata: Current metadata
            fix: Fix to apply

        Returns:
            Updated metadata
        """
        updates = {}

        if fix.impact == "title":
            updates["title"] = fix.after_value
        elif fix.impact == "description":
            updates["description"] = fix.after_value
        elif fix.impact == "tags":
            updates["tags"] = fix.after_value.split(", ")

        # Add fix to tracking
        fixes_applied = [*list(metadata.fixes_applied), fix.fix_id]
        updates["fixes_applied"] = fixes_applied

        return replace(metadata, **updates)
