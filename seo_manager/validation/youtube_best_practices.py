"""
YouTube SEO Best Practices Validator.

Validates metadata against current YouTube best practices (2026).
Provides specific recommendations for improvement.

Best Practices Source: YouTube Creator Academy + SEO research
"""

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class ValidationIssue:
    """A specific SEO best practice violation"""

    category: str  # 'title', 'description', 'tags', 'thumbnail'
    severity: str  # 'error', 'warning', 'info'
    issue: str
    recommendation: str
    best_practice: str


class YouTubeBestPracticesValidator:
    """
    Validates YouTube metadata against 2026 best practices.

    Best Practices (2026):
    - Title: 50-70 chars for mobile, front-load keywords
    - Description: Hook in first 125 chars, timestamps, value-add content
    - Tags: 10-15 relevant tags, avoid tag stuffing
    - Thumbnail: 1280x720, high contrast, readable text
    - CTR optimization: Compelling title + thumbnail
    """

    def __init__(self):
        """Initialize validator"""
        pass

    def validate(self, metadata: "SEOMetadata") -> List[ValidationIssue]:  # noqa: F821
        """
        Validate metadata against YouTube best practices.

        Args:
            metadata: SEOMetadata to validate

        Returns:
            List of issues found (empty if perfect)
        """
        issues = []

        # Validate title
        issues.extend(self._validate_title(metadata.title))

        # Validate description
        issues.extend(self._validate_description(metadata.description))

        # Validate tags
        issues.extend(self._validate_tags(metadata.tags))

        # Validate overall SEO
        issues.extend(self._validate_seo_quality(metadata))

        return issues

    def _validate_title(self, title: str) -> List[ValidationIssue]:
        """Validate title against best practices"""
        issues = []

        # Length check (50-70 optimal for mobile)
        if len(title) < 40:
            issues.append(
                ValidationIssue(
                    category="title",
                    severity="warning",
                    issue=f"Title too short ({len(title)} chars)",
                    recommendation="Aim for 50-70 characters for optimal mobile display",
                    best_practice="YouTube titles display up to 70 chars on mobile. Short titles miss SEO opportunity.",
                )
            )
        elif len(title) > 70:
            issues.append(
                ValidationIssue(
                    category="title",
                    severity="warning",
                    issue=f"Title too long ({len(title)} chars)",
                    recommendation="Shorten to 70 characters - excess will be cut off on mobile",
                    best_practice="YouTube truncates titles over 70 chars on mobile devices.",
                )
            )
        elif 50 <= len(title) <= 70:
            # Perfect length
            pass

        # Keyword front-loading
        if title and title[0].islower():
            issues.append(
                ValidationIssue(
                    category="title",
                    severity="info",
                    issue="Title starts with lowercase",
                    recommendation="Capitalize first letter for professionalism",
                    best_practice="Proper capitalization improves perceived quality and CTR.",
                )
            )

        # Check for clickbait patterns (2026: YouTube penalizes these)
        clickbait_words = ["shocking", "you won't believe", "click here", "gone wrong"]
        if any(word in title.lower() for word in clickbait_words):
            issues.append(
                ValidationIssue(
                    category="title",
                    severity="error",
                    issue="Clickbait detected in title",
                    recommendation="Remove clickbait phrases - YouTube algorithm penalizes these in 2026",
                    best_practice="Authentic, descriptive titles perform better long-term.",
                )
            )

        # HARD YouTube Data API v3 limits (rejection, not best-practice):
        # title must be <= 100 chars and contain no '<' or '>'.
        if len(title) > 100:
            issues.append(
                ValidationIssue(
                    category="title",
                    severity="error",
                    issue=f"Title exceeds YouTube API limit ({len(title)} > 100 chars)",
                    recommendation="Shorten the title to 100 characters or fewer",
                    best_practice="YouTube Data API rejects titles over 100 chars with a 400.",
                )
            )
        if any(c in title for c in "<>"):
            issues.append(
                ValidationIssue(
                    category="title",
                    severity="error",
                    issue="Title contains '<' or '>' (rejected by YouTube API)",
                    recommendation="Remove '<' and '>' from the title",
                    best_practice="YouTube Data API rejects titles containing '<' or '>'.",
                )
            )

        return issues

    def _validate_description(self, description: str) -> List[ValidationIssue]:
        """Validate description against best practices"""
        issues = []

        # Minimum length
        if len(description) < 100:
            issues.append(
                ValidationIssue(
                    category="description",
                    severity="warning",
                    issue=f"Description too short ({len(description)} chars)",
                    recommendation="Add more context - aim for 200+ characters minimum",
                    best_practice="Detailed descriptions improve SEO and viewer understanding.",
                )
            )

        # Check first 125 characters (above the fold)
        first_125 = description[:125]

        if "subscribe" in first_125.lower() or "like" in first_125.lower():
            issues.append(
                ValidationIssue(
                    category="description",
                    severity="warning",
                    issue="CTA in first 125 characters",
                    recommendation="Lead with value/hook - save CTAs for below the fold",
                    best_practice="First 125 chars show above 'Show more' - use for value proposition, not CTAs.",
                )
            )

        # Check for timestamps (good practice)
        has_timestamps = any(
            pattern in description for pattern in ["0:00", "00:00", "TIMESTAMP"]
        )
        if not has_timestamps and len(description) > 200:
            issues.append(
                ValidationIssue(
                    category="description",
                    severity="info",
                    issue="No timestamps found",
                    recommendation="Add chapter markers for better user experience",
                    best_practice="Timestamps improve watch time and enable YouTube chapters.",
                )
            )

        # HARD YouTube Data API v3 limits (rejection, not best-practice):
        # description must be <= 5000 bytes and contain no '<' or '>'.
        desc_bytes = len(description.encode("utf-8"))
        if desc_bytes > 5000:
            issues.append(
                ValidationIssue(
                    category="description",
                    severity="error",
                    issue=f"Description exceeds YouTube API limit ({desc_bytes} > 5000 bytes)",
                    recommendation="Shorten the description to 5000 bytes or fewer",
                    best_practice="YouTube Data API rejects descriptions over 5000 bytes with a 400.",
                )
            )
        if any(c in description for c in "<>"):
            issues.append(
                ValidationIssue(
                    category="description",
                    severity="error",
                    issue="Description contains '<' or '>' (rejected by YouTube API)",
                    recommendation="Remove '<' and '>' from the description",
                    best_practice="YouTube Data API rejects descriptions containing '<' or '>'.",
                )
            )

        return issues

    def _validate_tags(self, tags: List[str]) -> List[ValidationIssue]:
        """Validate tags against best practices"""
        issues = []

        # Tag count (10-15 optimal)
        if len(tags) < 5:
            issues.append(
                ValidationIssue(
                    category="tags",
                    severity="warning",
                    issue=f"Too few tags ({len(tags)})",
                    recommendation="Add more relevant tags - aim for 10-15 tags",
                    best_practice="10-15 well-chosen tags help YouTube understand your content.",
                )
            )
        elif len(tags) > 15:
            issues.append(
                ValidationIssue(
                    category="tags",
                    severity="info",
                    issue=f"Many tags ({len(tags)})",
                    recommendation="Consider reducing to 10-15 most relevant tags",
                    best_practice="Quality over quantity - 10-15 highly relevant tags outperform 20+ loose tags.",
                )
            )

        # Check for overly generic tags
        generic_tags = {"video", "content", "new", "latest", "best", "top"}
        generic_found = [tag for tag in tags if tag.lower() in generic_tags]

        if generic_found:
            issues.append(
                ValidationIssue(
                    category="tags",
                    severity="info",
                    issue=f"Generic tags detected: {', '.join(generic_found)}",
                    recommendation="Replace generic tags with specific, descriptive keywords",
                    best_practice="Specific tags help YouTube match to right audience.",
                )
            )

        # Check for duplicate tags
        unique_tags = set(tag.lower() for tag in tags)
        if len(unique_tags) < len(tags):
            issues.append(
                ValidationIssue(
                    category="tags",
                    severity="warning",
                    issue="Duplicate tags found",
                    recommendation="Remove duplicates - each tag should be unique",
                    best_practice="Duplicate tags waste your tag limit.",
                )
            )

        return issues

    def _validate_seo_quality(self, metadata: "SEOMetadata") -> List[ValidationIssue]:  # noqa: F821
        """Validate overall SEO quality"""
        issues = []

        # Quality score check
        if metadata.quality_score and metadata.quality_score < 0.6:
            issues.append(
                ValidationIssue(
                    category="quality",
                    severity="warning",
                    issue=f"Low quality score ({metadata.quality_score:.2f})",
                    recommendation="Improve title length, description detail, and tag relevance",
                    best_practice="Quality score combines multiple SEO factors.",
                )
            )

        # Keyword density
        if metadata.keyword_density:
            if metadata.keyword_density < 0.01:
                issues.append(
                    ValidationIssue(
                        category="quality",
                        severity="info",
                        issue=f"Low keyword density ({metadata.keyword_density:.3f})",
                        recommendation="Ensure primary keywords appear in title and description",
                        best_practice="Keyword presence helps YouTube matching.",
                    )
                )
            elif metadata.keyword_density > 0.05:
                issues.append(
                    ValidationIssue(
                        category="quality",
                        severity="warning",
                        issue=f"High keyword density ({metadata.keyword_density:.3f}) - may be keyword stuffing",
                        recommendation="Reduce keyword repetition for natural language",
                        best_practice="YouTube penalizes keyword stuffing in 2026.",
                    )
                )

        return issues

    def generate_validation_report(self, metadata: "SEOMetadata") -> Dict:  # noqa: F821
        """
        Generate comprehensive validation report.

        Returns:
            Dict with validation results and recommendations
        """
        issues = self.validate(metadata)

        # Group by severity
        errors = [i for i in issues if i.severity == "error"]
        warnings = [i for i in issues if i.severity == "warning"]
        info = [i for i in issues if i.severity == "info"]

        return {
            "is_valid": len(errors) == 0,
            "score": self._calculate_validation_score(issues),
            "total_issues": len(issues),
            "errors": len(errors),
            "warnings": len(warnings),
            "info": len(info),
            "issues": [
                {
                    "category": i.category,
                    "severity": i.severity,
                    "issue": i.issue,
                    "recommendation": i.recommendation,
                    "best_practice": i.best_practice,
                }
                for i in issues
            ],
        }

    def _calculate_validation_score(self, issues: List[ValidationIssue]) -> float:
        """
        Calculate validation score (0.0-1.0).

        Args:
            issues: List of validation issues

        Returns:
            Score (1.0 = perfect, 0.0 = many errors)
        """
        if not issues:
            return 1.0

        # Deduct points by severity
        score = 1.0
        for issue in issues:
            if issue.severity == "error":
                score -= 0.15
            elif issue.severity == "warning":
                score -= 0.08
            elif issue.severity == "info":
                score -= 0.03

        return max(0.0, score)
