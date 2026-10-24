"""
SEO Validation system.

Validates metadata against platform-specific rules and SEO best practices.
"""

from dataclasses import dataclass, replace

from ..core.metadata import SEOMetadata
from ..platforms.base import BasePlatformAdapter


@dataclass
class ValidationResult:
    """Result of SEO validation"""

    is_valid: bool
    errors: list[str]
    warnings: list[str]
    metadata: SEOMetadata


class PlatformValidator:
    """
    Validates SEO metadata against platform-specific rules.

    Checks:
    - Title length limits
    - Description length limits
    - Tag count limits
    - SEO best practices
    """

    def __init__(self, platform: BasePlatformAdapter):
        """
        Initialize validator for a platform.

        Args:
            platform: Platform adapter with constraints
        """
        self.platform = platform

    def validate(self, metadata: SEOMetadata) -> ValidationResult:
        """
        Validate metadata against platform rules.

        Args:
            metadata: SEO metadata to validate

        Returns:
            ValidationResult with errors and warnings
        """
        errors = []
        warnings = []

        # Validate title length
        title_limit = self.platform.get_title_limit()
        if len(metadata.title) > title_limit:
            errors.append(
                f"Title exceeds {title_limit} character limit "
                f"(current: {len(metadata.title)} chars)"
            )
        elif len(metadata.title) < 30:
            warnings.append(
                f"Title is short ({len(metadata.title)} chars). "
                f"Recommended: {title_limit - 10}-{title_limit} chars"
            )

        # Validate description length
        desc_limit = self.platform.get_description_limit()
        if len(metadata.description) > desc_limit:
            errors.append(
                f"Description exceeds {desc_limit} character limit "
                f"(current: {len(metadata.description)} chars)"
            )
        elif len(metadata.description) < 100:
            warnings.append("Description is short. More detail improves SEO.")

        # Validate tag count
        tag_limit = self.platform.get_tag_limit()
        if len(metadata.tags) > tag_limit:
            errors.append(
                f"Tag count exceeds {tag_limit} limit "
                f"(current: {len(metadata.tags)} tags)"
            )
        elif len(metadata.tags) < 3:
            warnings.append("Few tags provided. 5-10 tags recommended for SEO.")

        # Validate quality score (warning only)
        if metadata.quality_score and metadata.quality_score < 0.5:
            warnings.append(
                f"Low quality score ({metadata.quality_score:.2f}). "
                "Consider optimizing title, description, or tags."
            )

        # Determine overall validity
        is_valid = len(errors) == 0

        # Update metadata validation status
        updated_metadata = replace(
            metadata,
            validation_status="valid" if is_valid else "invalid",
            validation_errors=errors,
        )

        return ValidationResult(
            is_valid=is_valid,
            errors=errors,
            warnings=warnings,
            metadata=updated_metadata,
        )
