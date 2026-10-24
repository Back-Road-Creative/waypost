"""
Base SEO Generator for keyword extraction and metadata optimization.

Provides core functionality for:
- Keyword extraction (deterministic, offline — no corpus download)
- Title/description/tag optimization
- Quality scoring
- Readability calculation (Flesch)
"""

import re
from collections import Counter
from datetime import UTC, datetime

from seo_manager.generation.stopwords import ENGLISH_STOPWORDS

# Deterministic tokenizers. Text is lowercased before `_WORD_RE` is applied,
# so a token is always alphanumeric — equivalent to the old
# `word_tokenize(...)` + `w.isalnum()` pair, minus the corpus download that
# made the result depend on network reachability.
_WORD_RE = re.compile(r"[a-z0-9]+")
_SENTENCE_END_RE = re.compile(r"[.!?]+")


# A word is a run of letters/digits, with apostrophes allowed between them
# ("Don't"), so an apostrophe never starts a new word the way it does for
# `str.title()` ("Don'T").
_TITLE_WORD_RE = re.compile(r"[^\W_]+(?:['\u2019][^\W_]+)*")


def _capitalise_word(match: "re.Match[str]") -> str:
    word = match.group(0)
    # Only an all-lowercase word is capitalised. All-caps words ("GPS", "II")
    # and mixed-case words ("iPhone", "McDonald") were typed that way on
    # purpose, and `str.title()` would flatten them to "Gps", "Ii", "Iphone".
    if word.islower():
        return word[0].upper() + word[1:]
    return word


def _title_case(title: str) -> str:
    """Capitalise each lowercase word; keep acronyms, apostrophes and numerals."""
    return _TITLE_WORD_RE.sub(_capitalise_word, title)


class BaseSEOGenerator:
    """
    Base class for SEO metadata generation.

    Provides common functionality for keyword extraction,
    quality scoring, and metadata optimization that can be
    used across all platforms.
    """

    def __init__(self):
        """Initialize generator"""
        pass

    def _extract_keywords(
        self, topic: str, body: str | None, transcript: list[dict] | None
    ) -> list[str]:
        """
        Extract keywords by term frequency.

        Combines:
        - Topic analysis
        - Body text analysis (if provided)
        - Transcript analysis (if provided)
        - TF-based scoring
        - Capitalized word extraction (proper nouns)

        Deterministic and offline. Tokenizing uses `_WORD_RE` and stopword
        removal uses the vendored `ENGLISH_STOPWORDS`, so the result depends
        only on the input text — never on network reachability or on which
        corpora happen to be installed on the machine. There is deliberately
        no degraded fallback path; see `stopwords.py` for why.

        Args:
            topic: Main topic/title seed
            body: Full content text (optional)
            transcript: Dialogue transcript (optional)

        Returns:
            List of extracted keywords (top 15)
        """
        # Combine all text sources
        texts = [topic] if topic else []
        if body:
            texts.append(body)
        if transcript:
            texts.extend([entry.get("text", "") for entry in transcript])

        all_text = " ".join(texts)

        if not all_text.strip():
            return []

        # Tokenize, lowercase, then drop stopwords and short words
        words = [
            w
            for w in _WORD_RE.findall(all_text.lower())
            if w not in ENGLISH_STOPWORDS and len(w) > 3
        ]

        # Count word frequencies
        word_counts = Counter(words)

        # Extract capitalized words/phrases (likely proper nouns)
        capitalized = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", all_text)
        capitalized = [term.lower() for term in capitalized if len(term) > 3]

        # Combine and rank
        keywords = []

        # Add top words by frequency
        for word, _count in word_counts.most_common(20):
            if word not in keywords:
                keywords.append(word)

        # Add capitalized terms
        for term in capitalized[:10]:
            if term not in keywords:
                keywords.append(term)

        return keywords[:15]  # Top 15 keywords

    def _optimize_title(
        self,
        title: str,
        limit: int = 70,
        target_range: tuple[int, int] | None = None,
    ) -> str:
        """
        Optimize title for SEO.

        - Truncate to limit (at word boundary)
        - Apply title case capitalization
        - Optionally tighten toward a learned (min, max) length range

        Args:
            title: Raw title text
            limit: Maximum character length — the platform's HARD cap
            target_range: Optional (min, max) learned from analytics
                (`SEOOptimizer.get_optimal_title_length`). Advisory only; see
                the policy below. `None` reproduces the pre-constraint
                behaviour byte-for-byte.

        Returns:
            Optimized title
        """
        # Apply title case
        title = _title_case(title)

        # Truncate if needed
        if len(title) > limit:
            # Smart truncation at word boundary
            truncated = title[: limit - 3].rsplit(" ", 1)[0]
            title = truncated + "..."

        if target_range is None:
            return title

        # POLICY for the learned range. It is advisory, the platform cap is
        # not, so the range may only:
        #   1. TIGHTEN the cap, never widen it — `min(target_max, limit)`.
        #      A learned range of (100, 140) on a 60-char platform is a
        #      statement about content that platform will never accept.
        #   2. SHORTEN, never pad. Padding a short title to hit the minimum
        #      means inventing words the caller never supplied — the exact
        #      fabrication the generators refuse (see the comments in
        #      youtube_generator/shorts_generator). A short title stays short
        #      and out of range.
        #   3. Cut on a WORD BOUNDARY, and only if the cut still lands inside
        #      the range. When neither holds we keep the whole, platform-legal
        #      title even though it is out of range: a slightly long title
        #      still reads, a title chopped mid-word ("Supercalifragilis...")
        #      does not, and a cut that overshoots below the minimum has not
        #      satisfied the constraint either.
        target_max = min(int(target_range[1]), limit)
        target_min = min(int(target_range[0]), target_max)

        if target_max <= 0 or len(title) <= target_max:
            return title

        budget = title[: target_max - 3]
        if " " not in budget:
            # No word boundary inside the budget: cutting here splits a word.
            return title

        candidate = budget.rsplit(" ", 1)[0] + "..."
        if len(candidate) < target_min:
            # Overshoots below the range — no better than being over it.
            return title

        return candidate

    def _optimize_description(self, description: str, limit: int = 160) -> str:
        """
        Optimize description for SEO.

        - Truncate to limit (at sentence boundary if possible)

        Args:
            description: Raw description text
            limit: Maximum character length

        Returns:
            Optimized description
        """
        if len(description) <= limit:
            return description

        # Try to break at sentence boundary
        sentences = description.split(". ")
        result = ""

        for sentence in sentences:
            if len(result) + len(sentence) + 2 <= limit:
                result += sentence + ". "
            else:
                break

        if result:
            return result.strip()

        # Fallback: hard truncation
        return description[: limit - 3] + "..."

    def _optimize_tags(self, tags: list[str], limit: int = 10) -> list[str]:
        """
        Optimize tags for SEO.

        - Lowercase all tags
        - Remove case-insensitive duplicates
        - Limit count

        Args:
            tags: Raw tag list
            limit: Maximum number of tags

        Returns:
            Optimized tag list
        """
        # Lowercase and deduplicate (case-insensitive)
        seen = set()
        unique_tags = []

        for tag in tags:
            tag_lower = tag.lower()
            if tag_lower not in seen:
                seen.add(tag_lower)
                unique_tags.append(tag_lower)

        return unique_tags[:limit]

    def _calculate_seo_structure_score(
        self, title: str, description: str, tags: list[str]
    ) -> float:
        """
        Score the SEO *structure* of the metadata (0.0-1.0).

        This measures FORMAT fitness only — it does NOT assess factual
        accuracy or truthfulness. Well-formed but fabricated, inconsistent, or
        keyword-stuffed text scores as high as accurate text (a title/body that
        contradict each other still scores 1.0) — by design, because factual
        correctness is not derivable from (title, description, tags) alone.
        Factuality/accuracy is enforced elsewhere (the pipeline's content
        gates: business-name filtering, title↔location match, generic-filler
        detection). Do not treat this number as a content-quality certificate.

        Strict scoring — no participation points. Each component either
        meets the bar or scores zero for that component:
        - Title length (40-70 chars optimal)
        - Description length (300+ chars, YouTube rewards detail)
        - Tag count (10-15 optimal for discovery)
        - Keyword presence in title (critical for CTR)

        Args:
            title: Metadata title
            description: Metadata description
            tags: Metadata tags

        Returns:
            SEO structure score (0.0-1.0)
        """
        score = 0.0

        # Title length (40-70 chars ideal for YouTube)
        title_len = len(title)
        if 40 <= title_len <= 70:
            score += 0.3

        # Description length (300+ chars — YouTube rewards detailed descriptions)
        desc_len = len(description)
        if desc_len >= 300:
            score += 0.3

        # Tag count (10-15 ideal for YouTube discovery)
        tag_count = len(tags)
        if 10 <= tag_count <= 15:
            score += 0.2

        # Keyword in title (critical for search + CTR)
        if tags and any(tag.lower() in title.lower() for tag in tags[:3]):
            score += 0.2

        return score

    # Back-compat alias: the historical name over-claimed ("quality" implies
    # accuracy). The metric is purely structural (see the honest name above);
    # kept so existing callers/`metadata.quality_score` continue to resolve.
    _calculate_quality_score = _calculate_seo_structure_score

    def _calculate_keyword_density(self, text: str, keywords: list[str]) -> float:
        """
        Calculate keyword density (primary keyword / total words).

        Args:
            text: Full text content
            keywords: List of keywords

        Returns:
            Density (0.0-1.0)
        """
        if not keywords or not text:
            return 0.0

        words = text.lower().split()
        if not words:
            return 0.0

        primary_keyword = keywords[0].lower()
        count = words.count(primary_keyword)

        return count / len(words)

    def _calculate_readability(self, text: str) -> float:
        """
        Calculate Flesch Reading Ease score.

        Score interpretation:
        - 90-100: Very easy (5th grade)
        - 60-70: Standard (8th-9th grade)
        - 0-30: Very difficult (college graduate)

        Target: 60-70 for general audience

        Deterministic and offline for the same reason as `_extract_keywords`:
        the previous implementation downloaded the punkt tokenizer at run time
        and, on failure, silently switched to a coarser split that scored the
        same text differently.

        Args:
            text: Text to analyze

        Returns:
            Flesch Reading Ease score (0-100)
        """
        if not text.strip():
            return 0.0

        sentences = [s for s in _SENTENCE_END_RE.split(text) if s.strip()]
        words = _WORD_RE.findall(text.lower())

        if len(sentences) == 0 or len(words) == 0:
            return 0.0

        syllables = sum(self._count_syllables(word) for word in words)

        # Flesch Reading Ease formula
        score = (
            206.835
            - 1.015 * (len(words) / len(sentences))
            - 84.6 * (syllables / len(words))
        )

        return max(0.0, min(100.0, score))

    def _count_syllables(self, word: str) -> int:
        """
        Count syllables in a word (approximation).

        Args:
            word: Word to analyze

        Returns:
            Syllable count
        """
        word = word.lower()
        vowels = "aeiouy"
        count = 0
        previous_was_vowel = False

        for char in word:
            is_vowel = char in vowels
            if is_vowel and not previous_was_vowel:
                count += 1
            previous_was_vowel = is_vowel

        # Adjust for silent e
        if word.endswith("e") and count > 1:
            count -= 1

        # Ensure at least one syllable
        return max(1, count)

    def _generate_content_id(self, topic: str) -> str:
        """
        Generate content ID from topic.

        Args:
            topic: Topic text

        Returns:
            Slugified content ID with timestamp
        """
        # Slugify topic
        slug = re.sub(r"[^\w\s-]", "", topic.lower())
        slug = re.sub(r"[\s_]+", "-", slug)
        slug = slug[:50]  # Limit length

        # Add timestamp

        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")

        return f"{slug}_{timestamp}"
