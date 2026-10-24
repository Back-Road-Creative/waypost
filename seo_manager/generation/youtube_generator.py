"""
YouTube-specific SEO metadata generator.

Specialized features:
- Chapter marker extraction from transcript timestamps
- Citation formatting with numbered references
- Hook generation (first 125 chars for above-fold display)
- Episode branding integration (show name, episode numbers)
"""

import re
from typing import Dict, List, Optional

from .base import BaseSEOGenerator


class YouTubeMetadataGenerator(BaseSEOGenerator):
    """
    YouTube-specific SEO metadata generator.

    YouTube Optimizations:
    - Titles: 50-70 chars for mobile, keyword front-loaded
    - Descriptions: Important info in first 125 chars (above "Show more")
    - Tags: 10-15 tags covering topic + variations
    - Chapters: Extracted from transcript timestamps
    - Citations: Numbered reference format [1], [2], etc.
    """

    def __init__(self, adapter):
        super().__init__()
        self.adapter = adapter

        # YouTube-specific keyword corpus
        self.tech_keywords = [
            "ai",
            "artificial intelligence",
            "machine learning",
            "deep learning",
            "neural network",
            "llm",
            "gpt",
            "claude",
            "chatbot",
            "automation",
            "data science",
            "algorithm",
            "model",
            "training",
            "inference",
            "privacy",
            "security",
            "ethics",
            "safety",
            "bias",
            "local ai",
            "cloud",
            "api",
            "framework",
            "library",
            "python",
            "code",
            "programming",
            "development",
            "software",
            "tutorial",
            "guide",
            "course",
            "lesson",
            "education",
        ]

    def _generate_title(self, topic: str, keywords: List[str], **kwargs) -> str:
        """
        Generate YouTube title with episode branding.

        Format options:
        1. "{topic} | {show_name} #{episode_number}"
        2. "{topic} | {show_name}"
        3. "{topic}" (if no branding)

        Args:
            topic: Main topic
            keywords: Extracted keywords
            **kwargs: episode_number, show_name

        Returns:
            Optimized YouTube title
        """
        episode_number = kwargs.get("episode_number")
        show_name = kwargs.get("show_name")

        if not show_name:
            # No branding. Return the topic as-is, but give a too-short title
            # a neutral ": Overview" descriptor so cold-start titles still meet
            # YouTube's length best practice. Do NOT pad with a fabricated
            # "Scenic Drive"/"POV Drive" claim — ": Overview" asserts
            # nothing the caller didn't supply.
            title = topic
            if len(title) < 40 and len(title) + len(": Overview") <= 70:
                title = f"{title}: Overview"
            return title

        # Build title with branding
        if episode_number:
            title = f"{topic} | {show_name} #{episode_number}"
        else:
            title = f"{topic} | {show_name}"

        # If too long, abbreviate show name
        if len(title) > 70:
            # Create abbreviation from show name
            words = show_name.split()
            if len(words) > 1:
                short_name = "".join([word[0].upper() for word in words])
            else:
                short_name = show_name[:3].upper()

            if episode_number:
                title = f"{topic} | {short_name} #{episode_number}"
            else:
                title = f"{topic} | {short_name}"

        # Final truncation if still too long
        if len(title) > 70:
            max_topic_len = 70 - len(
                f" | {short_name if 'short_name' in locals() else show_name} #{episode_number or ''}"
            )
            if max_topic_len > 10:
                topic_truncated = topic[: max_topic_len - 3] + "..."
                title = f"{topic_truncated} | {short_name if 'short_name' in locals() else show_name}"
                if episode_number:
                    title += f" #{episode_number}"

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
        Generate YouTube description with structure:

        1. Hook/summary (first 125 chars - shown above "Show more")
        2. Timestamps (if available)
        3. Citations (if available)
        4. Social links (if available)
        5. Subscribe line

        Never an AI disclosure: nothing published through this manager is
        AI-generated.

        Args:
            topic: Main topic
            body: Content text
            transcript: Dialogue transcript
            keywords: Extracted keywords
            **kwargs: timestamps, citations, social_links

        Returns:
            Formatted YouTube description
        """
        description_parts = []

        # Part 1: Hook (CRITICAL for CTR - first 125 chars)
        hook = self._generate_hook(topic, transcript)
        description_parts.append(hook)
        description_parts.append("")

        # Part 2: Timestamps (chapter markers)
        timestamps = kwargs.get("timestamps", [])
        if not timestamps and transcript:
            # Auto-extract from transcript
            timestamps = self._extract_chapters(transcript)

        if timestamps:
            description_parts.append("🎯 TIMESTAMPS:")
            for ts in timestamps:
                description_parts.append(f"{ts['time']} - {ts['label']}")
            description_parts.append("")

        # Part 3: Citations
        citations = kwargs.get("citations", [])
        if citations:
            citation_text = self._format_citations(citations)
            if citation_text:
                description_parts.append("📚 CITATIONS:")
                description_parts.append(citation_text)
                description_parts.append("")

        # Part 4: Social links
        social_links = kwargs.get("social_links", {})
        if social_links:
            description_parts.append("🔗 CONNECT:")
            for platform, url in social_links.items():
                description_parts.append(f"{platform}: {url}")
            description_parts.append("")

        # Part 5: subscribe line
        description_parts.append("Subscribe for more episodes!")

        return "\n".join(description_parts)

    def _generate_hook(self, topic: str, transcript: Optional[List[Dict]]) -> str:
        """
        Generate compelling hook for first 125 characters.

        This is critical for CTR as it appears above "Show more" button.

        Args:
            topic: Main topic
            transcript: Dialogue transcript

        Returns:
            Hook text (max 125 chars)
        """
        if transcript and len(transcript) >= 1:
            # Use first dialogue line as hook
            first_line = transcript[0].get("text", "")

            # Build hook
            if len(first_line) > 80:
                hook = f"{first_line[:80]}..."
            else:
                hook = f"In this episode: {topic}. {first_line}"

        else:
            # Fallback: topic-based hook. Must NOT make fabricated claims
            # about the content — the downstream consumer (drive videos,
            # photo posts, etc.) doesn't necessarily have "developments,
            # insights, and discussions" at all. Keep it minimal and true;
            # let the description body carry project-specific content.
            hook = f"Exploring {topic}."

        # Ensure under 125 chars
        if len(hook) > 125:
            hook = hook[:122] + "..."

        return hook

    def _extract_chapters(self, transcript: List[Dict]) -> List[Dict]:
        """
        Extract chapter markers from transcript with timestamps.

        Looks for:
        - Topic transitions (keywords like "next", "now", "let's talk about")
        - Time gaps (>30 seconds between dialogues)
        - Speaker changes at significant timestamps

        Args:
            transcript: Transcript with start_time fields

        Returns:
            List of {"time": "MM:SS", "label": "Chapter Name"}
        """
        chapters = []

        # Check if transcript has timestamps
        if not transcript or "start_time" not in transcript[0]:
            return chapters

        # Always start with intro
        chapters.append({"time": "00:00", "label": "Introduction"})

        # Look for topic transitions
        transition_keywords = [
            "next",
            "now",
            "let's talk about",
            "moving on",
            "another topic",
            "let's discuss",
            "first",
            "second",
            "finally",
            "in conclusion",
        ]

        previous_time = 0.0

        for entry in transcript:
            start_time = entry.get("start_time", 0.0)
            text = entry.get("text", "").lower()

            # Check for transition keywords
            if any(keyword in text for keyword in transition_keywords):  # noqa: SIM102
                # Only add if significant time has passed (>30 seconds)
                if start_time - previous_time > 30:
                    timestamp = self._format_timestamp(start_time)

                    # Extract topic from text
                    label = self._extract_chapter_label(entry.get("text", ""))

                    chapters.append({"time": timestamp, "label": label})

                    previous_time = start_time

        return chapters

    def _format_timestamp(self, seconds: float) -> str:
        """
        Format seconds as MM:SS timestamp.

        Args:
            seconds: Time in seconds

        Returns:
            Formatted timestamp (e.g., "01:23")
        """
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes:02d}:{secs:02d}"

    def _extract_chapter_label(self, text: str) -> str:
        """
        Extract chapter label from dialogue text.

        Args:
            text: Dialogue text

        Returns:
            Short chapter label
        """
        # Remove common phrases
        text = re.sub(
            r"(let's talk about|next,|now,|first,)", "", text, flags=re.IGNORECASE
        )
        text = text.strip()

        # Take first 40 chars
        if len(text) > 40:
            text = text[:37] + "..."

        # Capitalize
        return text.capitalize()

    def _format_citations(self, citations: List[Dict]) -> str:
        """
        Format citations with numbered references.

        Args:
            citations: List of {"text": "Title", "url": "https://..."}

        Returns:
            Formatted citation text

        Example:
            [1] AI Research Paper - https://example.com/paper
            [2] ML Article - https://example.com/article
        """
        if not citations:
            return ""

        formatted_citations = []

        for i, citation in enumerate(citations, 1):
            text = citation.get("text", "Source")
            url = citation.get("url", "")

            formatted_citations.append(f"[{i}] {text} - {url}")

        return "\n".join(formatted_citations)

    def _generate_tags(
        self, keywords: List[str], existing_tags: Optional[List[str]], **kwargs
    ) -> List[str]:
        """
        Generate YouTube tags with multi-level strategy:

        1. Base tags (podcast, AI, technology)
        2. Topic tags (from keywords)
        3. Transcript tags (from dialogue analysis)
        4. Capitalized terms (proper nouns, brands)

        Args:
            keywords: Extracted keywords
            existing_tags: User-provided tags
            **kwargs: base_tags, transcript

        Returns:
            List of optimized tags
        """
        tags = []

        # Level 1: Base tags (always include - passed as parameter or kwarg)
        base_tags_param = kwargs.get("base_tags", [])
        if base_tags_param:
            tags.extend(base_tags_param)

        # Level 2: Topic tags (from keyword extraction)
        topic_keywords = [kw for kw in keywords if kw in self.tech_keywords]
        tags.extend(topic_keywords[:5])

        # Level 3: Existing tags (user-provided)
        if existing_tags:
            tags.extend(existing_tags)

        # Level 4: Transcript-specific tags
        transcript = kwargs.get("transcript", [])
        if transcript:
            transcript_tags = self._extract_transcript_tags(transcript)
            tags.extend(transcript_tags[:3])

        # Level 5: Remaining keywords
        for kw in keywords:
            if kw not in tags and len(tags) < 15:
                tags.append(kw)

        # Deduplicate, lowercase, limit to 15
        seen = set()
        unique_tags = []
        for tag in tags:
            tag_lower = tag.lower()
            if tag_lower not in seen:
                seen.add(tag_lower)
                unique_tags.append(tag_lower)

        return unique_tags[:15]

    def _extract_transcript_tags(self, transcript: List[Dict]) -> List[str]:
        """
        Extract specific terms mentioned in transcript.

        Looks for capitalized terms (brands, products, technologies).

        Args:
            transcript: Dialogue transcript

        Returns:
            List of extracted tags
        """
        all_text = " ".join([entry.get("text", "") for entry in transcript])

        # Find capitalized multi-word terms (likely brands/products)
        capitalized_terms = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", all_text)

        # Filter to tech-relevant terms
        relevant = []
        for term in capitalized_terms:
            term_lower = term.lower()
            # Check if contains tech keywords
            if any(keyword in term_lower for keyword in self.tech_keywords):  # noqa: SIM102
                if term_lower not in relevant:
                    relevant.append(term_lower)

        return relevant[:5]

    def _get_platform_metadata(self, **kwargs) -> Dict:
        """
        Extract YouTube-specific metadata.

        Args:
            **kwargs: category, playlist, visibility, etc.

        Returns:
            Dict with youtube_metadata
        """
        youtube_meta = {}

        if "category" in kwargs:
            youtube_meta["category"] = kwargs["category"]
        if "playlist" in kwargs:
            youtube_meta["playlist"] = kwargs["playlist"]
        if "visibility" in kwargs:
            youtube_meta["visibility"] = kwargs["visibility"]

        # Default values
        if "category" not in youtube_meta:
            youtube_meta["category"] = "Science & Technology"
        if "visibility" not in youtube_meta:
            youtube_meta["visibility"] = "public"

        youtube_meta["made_for_kids"] = kwargs.get("made_for_kids", False)
        youtube_meta["age_restriction"] = kwargs.get("age_restriction")

        return {"youtube_metadata": youtube_meta}
