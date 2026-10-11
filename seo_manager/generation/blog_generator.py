"""
Blog/Hugo-specific SEO metadata generator.

Specialized features:
- SERP-optimized titles (50-60 chars)
- Meta descriptions optimized for Google snippets
- Taxonomic tag generation (slugified)
- Long-tail keyword focus for organic traffic
"""

import re
from typing import Dict, List, Optional

from ..core.metadata import ContentType
from .base import BaseSEOGenerator


class BlogMetadataGenerator(BaseSEOGenerator):
    """
    Blog/Hugo-specific SEO metadata generator.

    Blog SEO Optimizations:
    - Titles: 50-60 chars for Google SERP display
    - Meta Descriptions: 150-160 chars for snippet
    - Tags: 5-10 taxonomic keywords (slugified for URLs)
    - Focus: Long-tail keywords for organic search traffic
    - Format: Blog platform conventions (Hugo, Jekyll, WordPress)
    """

    def __init__(self, adapter):
        super().__init__()
        self.adapter = adapter

        # Common blog content keywords
        self.common_words = {
            "this",
            "that",
            "with",
            "from",
            "about",
            "your",
            "our",
            "some",
            "many",
            "more",
            "most",
            "such",
            "very",
            "also",
            "just",
            "only",
            "here",
            "there",
            "when",
            "where",
            "what",
            "who",
            "which",
            "their",
            "them",
            "these",
            "those",
        }

    def _generate_title(self, topic: str, keywords: List[str], **kwargs) -> str:
        """
        Generate blog title optimized for Google SERP.

        Strategy:
        1. Front-load primary keyword (SEO best practice)
        2. Add value proposition or descriptor
        3. Include brand suffix if space allows
        4. Target 50-60 character range (optimal SERP display)

        Args:
            topic: Main topic
            keywords: Extracted keywords
            **kwargs: title_suffix (e.g., "| ExampleSite")

        Returns:
            SERP-optimized blog title

        Examples:
            "Mount Si Trail Review" + suffix "| ExampleSite"
            -> "Mount Si Trail Review: Complete Guide | ExampleSite"
        """
        # Start with topic
        title = topic.strip()

        # Add a descriptor if the title is short. Use only the caller-supplied
        # content type as the factual basis — never auto-assert ": Complete
        # Guide" or ": Step-by-Step", which claim a completeness/structure the
        # generator cannot verify.
        if len(title) < 50:
            content_type = kwargs.get("content_type", ContentType.BLOG_POST)

            if content_type == ContentType.TRAIL_GUIDE:
                suffix = ": Trail Guide"
            else:
                suffix = ": Overview"

            # Only add if it fits within limit
            if len(title) + len(suffix) <= 60:
                title = f"{title}{suffix}"

        # Add brand suffix if provided and if space allows
        title_suffix = kwargs.get("title_suffix")
        if title_suffix and len(title) + len(title_suffix) + 1 <= 60:
            title = f"{title} {title_suffix}"

        # Ensure title is within optimal SERP range (50-60 chars)
        if len(title) > 60:
            # Truncate intelligently at word boundary
            title = title[:57].rsplit(" ", 1)[0] + "..."
        elif len(title) < 50 and not title_suffix:
            # Try to expand with primary keyword if available
            primary_kw = keywords[0] if keywords else None
            if primary_kw and primary_kw.lower() not in title.lower():
                expanded = f"{primary_kw.title()}: {title}"
                if len(expanded) <= 60:
                    title = expanded

        return title

    def _generate_description(
        self,
        topic: str,
        body: Optional[str],
        transcript: Optional[List[Dict]],
        keywords: List[str],
        **kwargs,
    ) -> str:
        """
        Generate meta description for Google SERP snippet.

        Critical: First 120 chars shown on mobile SERP.
        Must include:
        - Primary keyword (for relevance)
        - Value proposition (why read this?)
        - Call to action (optional)

        Target: 150-160 characters

        Args:
            topic: Main topic
            body: Blog post content
            transcript: Not used for blogs
            keywords: Extracted keywords
            **kwargs: Additional metadata

        Returns:
            SERP-optimized meta description

        Examples:
            "Comprehensive guide to Mount Si Trail with GPS data,
            elevation profile, and real health metrics. Learn the route."
        """
        # Try to extract summary from body
        if body:
            # Get first paragraph as base
            description = self._extract_summary_from_body(body, keywords)
        else:
            # Fallback: generate from topic and keywords
            description = self._generate_fallback_description(topic, keywords)

        # Ensure primary keyword is present
        primary_kw = keywords[0] if keywords else topic.split()[0]
        if primary_kw and primary_kw.lower() not in description.lower():
            # Prepend keyword for SEO
            description = f"{primary_kw}: {description}"

        # Truncate to 150-160 chars (SERP meta description limit)
        if len(description) > 160:
            # Try to break at sentence boundary
            sentences = description.split(". ")
            if len(sentences) > 1:
                # Use first 1-2 sentences
                description = ". ".join(sentences[:2])
                if len(description) > 160:
                    description = sentences[0]

                # Add period if missing
                if not description.endswith("."):
                    description += "."

            # Final truncation if still too long
            if len(description) > 160:
                description = description[:157] + "..."

        # Ensure minimum length for SEO value
        if len(description) < 120:  # noqa: SIM102
            # Pad with additional context if available
            if len(keywords) > 1:
                additional = f" Explore {', '.join(keywords[1:3])} and more."
                if len(description) + len(additional) <= 160:
                    description += additional

        return description

    def _extract_summary_from_body(self, body: str, keywords: List[str]) -> str:
        """
        Extract summary from blog post body.

        Looks for:
        1. First paragraph (after any headers)
        2. Cleans markdown formatting
        3. Ensures keyword presence

        Args:
            body: Blog post content
            keywords: Extracted keywords

        Returns:
            Summary text
        """
        # Split into paragraphs
        paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]

        # Skip headers (lines starting with #)
        content_paragraphs = [
            p for p in paragraphs if not p.startswith("#") and len(p) > 50
        ]

        if not content_paragraphs:
            return body[:200].strip()

        # Get first content paragraph
        first_para = content_paragraphs[0]

        # Clean markdown formatting
        # Remove markdown links: [text](url) -> text
        first_para = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", first_para)
        # Remove bold/italic: **text** or *text* -> text
        first_para = re.sub(r"\*\*([^\*]+)\*\*", r"\1", first_para)
        first_para = re.sub(r"\*([^\*]+)\*", r"\1", first_para)
        # Remove inline code: `code` -> code
        first_para = re.sub(r"`([^`]+)`", r"\1", first_para)
        # Remove extra whitespace
        first_para = " ".join(first_para.split())

        return first_para

    def _generate_fallback_description(self, topic: str, keywords: List[str]) -> str:
        """
        Generate fallback description when body is not available.

        Args:
            topic: Main topic
            keywords: Extracted keywords

        Returns:
            Generic but SEO-friendly description
        """
        # Build description from the topic and extracted keywords only.
        # Do NOT assert unverifiable qualities ("comprehensive", "expert
        # insights", "actionable recommendations") the post may not deliver.
        if keywords and len(keywords) >= 2:
            kw_list = ", ".join(keywords[:3])
            description = f"{topic}. Covers {kw_list}."
        else:
            description = f"About {topic.lower()}."

        return description

    def _generate_tags(
        self, keywords: List[str], existing_tags: Optional[List[str]], **kwargs
    ) -> List[str]:
        """
        Generate taxonomic tags for blog organization.

        Strategy:
        1. Preserve existing tags (maintain taxonomy consistency)
        2. Add relevant extracted keywords
        3. Slugify all tags (lowercase, hyphenated)
        4. Deduplicate and limit to 5-10 tags

        Blog tags are used for:
        - Site navigation (/tags/machine-learning/)
        - Related content grouping
        - RSS feed categorization

        Args:
            keywords: Extracted keywords
            existing_tags: Pre-defined tags (optional)
            **kwargs: Additional tag sources

        Returns:
            List of slugified tags

        Examples:
            Input: ["Machine Learning", "AI Tech", "Python"]
            Output: ["machine-learning", "ai-tech", "python"]
        """
        tags = []

        # Level 1: Existing tags (preserve taxonomy)
        if existing_tags:
            tags.extend(existing_tags)

        # Level 2: Extract relevant keywords
        for keyword in keywords:
            # Skip if too short or already present
            if len(keyword) < 4 or keyword.lower() in [t.lower() for t in tags]:
                continue

            # Skip common words that don't make good tags
            if keyword.lower() in self.common_words:
                continue

            tags.append(keyword)

        # Level 3: Extract from categories if provided
        categories = kwargs.get("categories", [])
        if categories:
            for cat in categories:
                if cat.lower() not in [t.lower() for t in tags]:
                    tags.append(cat)

        # Slugify all tags for URL compatibility
        tags = [self._slugify(tag) for tag in tags]

        # Deduplicate (case-insensitive)
        seen = set()
        unique_tags = []
        for tag in tags:
            tag_lower = tag.lower()
            if tag_lower not in seen:
                unique_tags.append(tag)
                seen.add(tag_lower)

        # Limit to 5-10 tags (SEO best practice)
        return unique_tags[:10]

    def _slugify(self, text: str) -> str:
        """
        Convert text to URL-safe slug.

        Rules:
        - Lowercase
        - Replace spaces with hyphens
        - Remove special characters
        - Remove consecutive hyphens
        - Strip leading/trailing hyphens

        Args:
            text: Raw text

        Returns:
            Slugified text

        Examples:
            "Machine Learning" -> "machine-learning"
            "AI & ML" -> "ai-ml"
            "Python 3.11" -> "python-311"
            "  Multi-word  Tag  " -> "multi-word-tag"
        """
        # Lowercase and strip
        text = text.lower().strip()

        # Remove special characters (keep alphanumeric, spaces, hyphens)
        text = re.sub(r"[^\w\s-]", "", text)

        # Replace spaces and underscores with hyphens
        text = re.sub(r"[\s_]+", "-", text)

        # Remove multiple consecutive hyphens
        text = re.sub(r"-+", "-", text)

        # Strip leading/trailing hyphens
        text = text.strip("-")

        return text

    def _get_platform_metadata(self, **kwargs) -> Dict:
        """
        Extract blog-specific metadata.

        Blog platforms (Hugo, Jekyll, WordPress) support additional
        metadata fields for organization and display.

        Args:
            **kwargs: Metadata fields

        Returns:
            Dict with blog_metadata structure

        Fields:
            - categories: Taxonomic categories
            - series: Multi-post series name
            - featured_image: Hero image URL
            - excerpt: Custom excerpt (if different from description)
            - reading_time: Estimated reading time in minutes
            - author: Post author name
        """
        blog_metadata = {}

        # Categories (taxonomic classification)
        if "categories" in kwargs:
            blog_metadata["categories"] = kwargs["categories"]

        # Series (multi-part content)
        if "series" in kwargs:
            blog_metadata["series"] = kwargs["series"]

        # Featured image
        if "featured_image" in kwargs:
            blog_metadata["featured_image"] = kwargs["featured_image"]

        # Custom excerpt
        if "excerpt" in kwargs:
            blog_metadata["excerpt"] = kwargs["excerpt"]

        # Reading time
        if "reading_time" in kwargs:
            blog_metadata["reading_time"] = kwargs["reading_time"]
        elif "body" in kwargs:
            # Auto-calculate reading time (250 words per minute)
            body = kwargs["body"]
            word_count = len(body.split())
            reading_time = max(1, round(word_count / 250))
            blog_metadata["reading_time"] = reading_time

        # Author
        if "author" in kwargs:
            blog_metadata["author"] = kwargs["author"]

        return {"blog_metadata": blog_metadata}
