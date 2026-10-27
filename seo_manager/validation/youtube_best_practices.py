"""
YouTube SEO Best Practices Validator.

Validates metadata against current YouTube best practices (2026).
Provides specific recommendations for improvement.

Best Practices Source: YouTube Creator Academy + SEO research
"""

from dataclasses import dataclass

from ..platforms.youtube import (
    YOUTUBE_TAG_COUNT_STYLE_RANGE,
    YOUTUBE_TAGS_API_MAX_CHARS,
    YOUTUBE_TITLE_API_MAX_CHARS,
    YOUTUBE_TITLE_DISPLAY_CHARS,
    YOUTUBE_TITLE_STYLE_MIN_CHARS,
)

_TAG_MIN, _TAG_MAX = YOUTUBE_TAG_COUNT_STYLE_RANGE


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

    Each issue is one of: a hard API limit (severity "error", cited as such),
    or editorial STYLE advice (never an error, labelled "style advice, not
    measured"). Nothing here is a measured effect on this channel.

    Style advice (2026):
    - Title: 50-70 chars for mobile, front-load keywords
    - Description: Hook in first 125 chars, timestamps, value-add content
    - Tags: 10-15 relevant tags, avoid tag stuffing
    - Thumbnail: 1280x720, high contrast, readable text
    - CTR optimization: Compelling title + thumbnail
    """

    def __init__(self):
        """Initialize validator"""
        pass

    def validate(self, metadata: "SEOMetadata") -> list[ValidationIssue]:  # noqa: F821
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

    def _validate_title(self, title: str) -> list[ValidationIssue]:
        """Validate title against best practices"""
        issues = []

        # Length check -- STYLE advice (mobile display), not an API rule.
        if len(title) < 40:
            issues.append(
                ValidationIssue(
                    category="title",
                    severity="warning",
                    issue=f"Title too short ({len(title)} chars)",
                    recommendation=(
                        f"Style advice: aim for {YOUTUBE_TITLE_STYLE_MIN_CHARS}-"
                        f"{YOUTUBE_TITLE_DISPLAY_CHARS} characters for full mobile display"
                    ),
                    best_practice=(
                        "Style advice, not measured: titles display up to "
                        f"{YOUTUBE_TITLE_DISPLAY_CHARS} chars on mobile."
                    ),
                )
            )
        elif len(title) > YOUTUBE_TITLE_DISPLAY_CHARS:
            issues.append(
                ValidationIssue(
                    category="title",
                    severity="warning",
                    issue=f"Title too long ({len(title)} chars)",
                    recommendation=(
                        f"Style advice: shorten to {YOUTUBE_TITLE_DISPLAY_CHARS} "
                        "characters to avoid being cut off on mobile"
                    ),
                    best_practice=(
                        "Style advice, not an API rule: mobile display cuts titles "
                        f"over {YOUTUBE_TITLE_DISPLAY_CHARS} chars."
                    ),
                )
            )

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
        if len(title) > YOUTUBE_TITLE_API_MAX_CHARS:
            issues.append(
                ValidationIssue(
                    category="title",
                    severity="error",
                    issue=(
                        f"Title exceeds YouTube API limit "
                        f"({len(title)} > {YOUTUBE_TITLE_API_MAX_CHARS} chars)"
                    ),
                    recommendation=(
                        f"Shorten the title to {YOUTUBE_TITLE_API_MAX_CHARS} "
                        "characters or fewer"
                    ),
                    best_practice=(
                        "API limit: YouTube Data API rejects titles over "
                        f"{YOUTUBE_TITLE_API_MAX_CHARS} chars with a 400."
                    ),
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

    def _validate_description(self, description: str) -> list[ValidationIssue]:
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

    def _validate_tags(self, tags: list[str]) -> list[ValidationIssue]:
        """Validate tags against best practices"""
        issues = []

        # HARD API limit: tags are bounded by total characters, not by count.
        total_chars = sum(len(t) for t in tags)
        if total_chars > YOUTUBE_TAGS_API_MAX_CHARS:
            issues.append(
                ValidationIssue(
                    category="tags",
                    severity="error",
                    issue=(
                        f"Tags exceed YouTube API limit "
                        f"({total_chars} > {YOUTUBE_TAGS_API_MAX_CHARS} chars)"
                    ),
                    recommendation="Remove tags until the total is within the limit",
                    best_practice=(
                        "API limit: the YouTube Data API bounds tags by total "
                        f"characters ({YOUTUBE_TAGS_API_MAX_CHARS})."
                    ),
                )
            )

        # Tag count -- STYLE advice, not an API rule and not measured.
        if len(tags) < 5:
            issues.append(
                ValidationIssue(
                    category="tags",
                    severity="warning",
                    issue=f"Too few tags ({len(tags)})",
                    recommendation=(
                        f"Style advice: add more relevant tags - aim for "
                        f"{_TAG_MIN}-{_TAG_MAX}"
                    ),
                    best_practice=(
                        f"Style advice, not measured: {_TAG_MIN}-{_TAG_MAX} "
                        "well-chosen tags is a common habit."
                    ),
                )
            )
        elif len(tags) > _TAG_MAX:
            issues.append(
                ValidationIssue(
                    category="tags",
                    severity="info",
                    issue=f"Many tags ({len(tags)})",
                    recommendation=(
                        f"Style advice: consider reducing to the {_TAG_MIN}-"
                        f"{_TAG_MAX} most relevant tags"
                    ),
                    best_practice=(
                        "Style advice, not measured: fewer, relevant tags "
                        "beat a long loose list."
                    ),
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

    def _validate_seo_quality(self, metadata: "SEOMetadata") -> list[ValidationIssue]:  # noqa: F821
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

    def generate_validation_report(self, metadata: "SEOMetadata") -> dict:  # noqa: F821
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

    def _calculate_validation_score(self, issues: list[ValidationIssue]) -> float:
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
