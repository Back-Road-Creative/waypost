"""
YouTube Shorts SEO Metadata Generator.

Specialized generator for YouTube Shorts with different optimization
strategies than long-form videos.

YouTube Shorts requirements (2026):
- Duration: <60 seconds
- Aspect ratio: 9:16 (vertical)
- Title: 40-50 chars (shorter, punchier than long-form)
- Description: 100-150 chars with 3-5 hashtags inline
- Tags: 5-10 focused tags (fewer than long-form 10-15)
- NO chapters/timestamps (too short)
- Mobile-first optimization (most Shorts viewed on mobile)
"""

from .base import BaseSEOGenerator


class ShortsMetadataGenerator(BaseSEOGenerator):
    """
    YouTube Shorts-specific SEO metadata generator.

    Key differences from long-form videos:
    - Shorter, punchier titles (40-50 chars vs 50-70)
    - Brief descriptions with inline hashtags (100-150 chars)
    - Fewer, more focused tags (5-10 vs 10-15)
    - NO timestamp chapters
    - Emoji-friendly for mobile engagement
    - Immediate hook optimization (first 3 seconds critical)
    """

    def __init__(self, adapter):
        super().__init__()
        self.adapter = adapter

        # Shorts-relevant keywords
        self.shorts_keywords = [
            "short",
            "shorts",
            "quick",
            "fast",
            "viral",
            "trending",
            "fyp",
            "foryou",
            "explore",
            "discover",
            "watch",
        ]

    def _generate_title(self, topic: str, keywords: list[str], **kwargs) -> str:
        """
        Generate punchy Short title (40-50 chars).

        Shorts titles should be:
        - Shorter than long-form (40-50 vs 50-70 chars)
        - Front-loaded with hook
        - No episode branding (too long)
        - Optimized for mobile display

        Args:
            topic: Main topic
            keywords: Extracted keywords
            **kwargs: Additional parameters (ignored for Shorts)

        Returns:
            Optimized Short title
        """
        # Shorts don't use episode branding - too long for mobile.
        # Keep it simple and punchy. Do NOT prepend a fabricated hook word
        # ("Hidden"/"Secret"/...) or blanket title-case the topic — that
        # invents claims and garbles proper nouns.
        title = topic

        # Ensure title is at least 30 chars for engagement
        # (Shorts benefit from slightly longer titles for context)
        if len(title) < 30 and keywords:
            # Add descriptive words from keywords
            for kw in keywords[:3]:
                if kw.lower() not in title.lower():
                    test_title = f"{title} - {kw.title()}"
                    if len(test_title) <= 50:
                        title = test_title
                        if len(title) >= 30:
                            break

        # Shorts titles should be 40-50 chars (shorter than long-form)
        # But allow 30-55 range for flexibility
        if len(title) > 55:
            # Smart truncation at word boundary
            title = title[:52].rsplit(" ", 1)[0] + "..."

        return title

    def _generate_description(
        self,
        topic: str,
        body: str | None,
        transcript: list[dict] | None,
        keywords: list[str],
        **kwargs,
    ) -> str:
        """
        Generate brief Short description (100-150 chars) with hashtags.

        Shorts descriptions should be:
        - Very brief (100-150 chars vs 5000 for long-form)
        - Include 3-5 hashtags inline
        - Lead with hook/summary
        - NO timestamps (Shorts don't support chapters)
        - Mobile-optimized

        Args:
            topic: Main topic
            body: Content text (optional)
            transcript: Dialogue transcript (optional)
            keywords: Extracted keywords
            **kwargs: timestamps (ignored), citations (ignored)

        Returns:
            Brief description with inline hashtags
        """
        # Part 1: Hook/summary (50-100 chars to meet minimum length)
        if transcript and len(transcript) >= 1:
            # Use first dialogue line
            first_line = transcript[0].get("text", "")
            hook = first_line[:77] + "..." if len(first_line) > 80 else first_line
        else:
            # Use the topic itself as the hook. Do NOT prepend a fabricated
            # hook word — the topic is the only truthful summary we have.
            hook = topic

        # Ensure hook is long enough (at least 40 chars for context)
        if len(hook) < 40 and body:
            # Add some body content
            extra = body[:50]
            hook = f"{hook}. {extra}"

        # Ensure hook is not too long (leave room for hashtags)
        if len(hook) > 100:
            hook = hook[:97] + "..."

        # Part 2: Generate hashtags (3-5 focused tags)
        hashtags = self._generate_hashtags(keywords)

        # Combine hook + hashtags
        description = f"{hook} {hashtags}"

        # Ensure total length is 100-150 chars (mobile-optimized)
        if len(description) > 150:
            # Trim hook to make room
            max_hook_len = 150 - len(hashtags) - 1
            hook = hook[: max_hook_len - 3] + "..."
            description = f"{hook} {hashtags}"

        # NOTE: Shorts don't support timestamps/chapters, so we ignore them
        # NO citations section (too long for Shorts)
        # NO social links (keep it ultra-brief)

        return description

    def _generate_hashtags(self, keywords: list[str]) -> str:
        """
        Generate 3-5 inline hashtags for Shorts.

        Args:
            keywords: Extracted keywords

        Returns:
            Space-separated hashtags (e.g., "#coast #travel #scenic")
        """
        # Select top 3-5 keywords for hashtags
        hashtag_keywords = []

        # Prioritize Shorts-specific keywords if present
        for kw in keywords:
            if kw.lower() in self.shorts_keywords:
                hashtag_keywords.append(kw)

        # Add remaining top keywords
        for kw in keywords:
            if kw not in hashtag_keywords and len(hashtag_keywords) < 5:
                hashtag_keywords.append(kw)

        # Limit to 3-5 hashtags
        hashtag_keywords = hashtag_keywords[:5]

        # Format as hashtags
        hashtags = " ".join(
            f"#{kw.lower().replace(' ', '')}" for kw in hashtag_keywords
        )

        return hashtags

    def _generate_tags(
        self, keywords: list[str], existing_tags: list[str] | None, **kwargs
    ) -> list[str]:
        """
        Generate focused tags for Shorts (5-10 tags).

        Shorts use fewer, more focused tags than long-form videos.

        Args:
            keywords: Extracted keywords
            existing_tags: User-provided tags
            **kwargs: Additional parameters

        Returns:
            List of 5-10 optimized tags
        """
        tags = []

        # Always include Shorts indicators
        tags.extend(["shorts", "short", "ytshorts"])

        # Add top keywords (limit to high-impact terms)
        for kw in keywords:
            if kw.lower() not in tags and len(tags) < 10:
                tags.append(kw.lower())

        # Add existing tags if provided
        if existing_tags:
            for tag in existing_tags:
                if tag.lower() not in tags and len(tags) < 10:
                    tags.append(tag.lower())

        # Deduplicate and limit to 5-10 tags
        seen = set()
        unique_tags = []
        for tag in tags:
            tag_lower = tag.lower()
            if tag_lower not in seen:
                seen.add(tag_lower)
                unique_tags.append(tag_lower)

        # Ensure at least 5 tags, at most 10
        return unique_tags[:10]

    def _get_platform_metadata(self, **kwargs) -> dict:
        """
        Extract YouTube Shorts-specific metadata.

        Args:
            **kwargs: shorts_enabled, vertical_format, category, etc.

        Returns:
            Dict with youtube_metadata
        """
        youtube_meta = {}

        # Mark as Shorts content
        youtube_meta["is_short"] = True
        youtube_meta["shorts_enabled"] = kwargs.get("shorts_enabled", True)
        youtube_meta["vertical_format"] = kwargs.get("vertical_format", True)

        # Category (default to Entertainment for Shorts)
        youtube_meta["category"] = kwargs.get("category", "Entertainment")

        # Visibility
        youtube_meta["visibility"] = kwargs.get("visibility", "public")

        # Shorts are typically not made for kids
        youtube_meta["made_for_kids"] = kwargs.get("made_for_kids", False)

        # No age restriction by default
        youtube_meta["age_restriction"] = kwargs.get("age_restriction")

        return {"youtube_metadata": youtube_meta}
