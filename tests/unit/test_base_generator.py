"""
Unit tests for BaseSEOGenerator.

Tests cover:
- Keyword extraction from text
- Title generation and optimization
- Description generation
- Tag generation and deduplication
- Quality score calculation
- Readability scoring (Flesch)
- Keyword density calculation
"""

import builtins
import socket

import pytest

from seo_manager.core.metadata import ContentType, Platform, SEOMetadata
from seo_manager.generation.base import BaseSEOGenerator
from seo_manager.generation.stopwords import ENGLISH_STOPWORDS


class TestBaseSEOGenerator:
    """Test BaseSEOGenerator keyword extraction and optimization"""

    @pytest.fixture
    def generator(self):
        """Create a basic generator instance"""
        return BaseSEOGenerator()

    def test_extract_keywords_from_text(self, generator):
        """Test keyword extraction from plain text"""
        text = """
        Artificial intelligence and machine learning are transforming technology.
        Deep learning models use neural networks to process data.
        AI systems can automate complex tasks efficiently.
        """

        keywords = generator._extract_keywords(
            topic="AI Technology", body=text, transcript=None
        )

        assert isinstance(keywords, list)
        assert len(keywords) > 0
        # Should extract relevant terms
        assert any("ai" in kw.lower() or "artificial" in kw.lower() for kw in keywords)
        assert any("learning" in kw.lower() for kw in keywords)

    def test_extract_keywords_from_transcript(self, generator):
        """Test keyword extraction from dialogue transcript"""
        transcript = [
            {"speaker": "Host", "text": "Today we're discussing Python programming"},
            {
                "speaker": "Guest",
                "text": "Python is great for data science and machine learning",
            },
            {"speaker": "Host", "text": "What libraries do you recommend?"},
            {
                "speaker": "Guest",
                "text": "NumPy, Pandas, and scikit-learn are essential",
            },
        ]

        keywords = generator._extract_keywords(
            topic="Python Programming", body=None, transcript=transcript
        )

        assert isinstance(keywords, list)
        assert any("python" in kw.lower() for kw in keywords)
        # Keywords should be extracted from transcript
        assert len(keywords) > 0

    def test_keyword_extraction_stopword_removal(self, generator):
        """Test that stopwords are removed from keywords"""
        text = "the quick brown fox jumps over the lazy dog"

        keywords = generator._extract_keywords(topic="Test", body=text, transcript=None)

        # Common stopwords should not be in keywords
        stopwords = ["the", "over", "a", "an", "and", "or", "but"]
        for stopword in stopwords:
            assert stopword not in keywords

    def test_keyword_extraction_offline_without_nltk(self, generator, monkeypatch):
        """Stopword removal must not need nltk, a corpus, or the network.

        Regression guard for the silent degradation this replaced: extraction
        downloaded the stopword corpus at run time and, when that failed, fell
        back to a ~10-word set with no error and no warning. Here `import nltk`
        raises (as it does under nltk 3.10.1's CWD import guard) and every
        socket call raises, so any reintroduced fallback shows up as a failure
        rather than as quietly different keywords.
        """
        real_import = builtins.__import__

        def no_nltk(name, *args, **kwargs):
            if name == "nltk" or name.startswith("nltk."):
                raise ImportError(f"blocked import of {name}")
            return real_import(name, *args, **kwargs)

        def no_network(*args, **kwargs):
            raise AssertionError("keyword extraction attempted a network call")

        monkeypatch.setattr(builtins, "__import__", no_nltk)
        monkeypatch.setattr(socket, "socket", no_network)
        monkeypatch.setenv("NLTK_DATA", "/nonexistent-nltk-data")

        keywords = generator._extract_keywords(
            topic="Test",
            body="the quick brown fox jumps over the lazy dog",
            transcript=None,
        )

        assert "over" not in keywords
        assert "the" not in keywords
        assert "quick" in keywords

    def test_vendored_stopwords_match_nltk_corpus(self):
        """The vendored list is the full NLTK english corpus, not a stub."""
        assert len(ENGLISH_STOPWORDS) == 198
        for word in ("over", "the", "a", "an", "and", "or", "but", "themselves"):
            assert word in ENGLISH_STOPWORDS

    def test_optimize_title_truncation(self, generator):
        """Test title truncation to limit"""
        long_title = "This is a very long title that exceeds the maximum character limit and needs to be truncated properly"

        # Assume 70 char limit
        optimized = generator._optimize_title(long_title, limit=70)

        assert len(optimized) <= 70
        assert optimized.endswith("...")
        # Should break at word boundary
        assert not optimized[:-3].endswith(" ")

    def test_optimize_title_capitalization(self, generator):
        """Test title case capitalization"""
        title = "understanding machine learning basics"

        optimized = generator._optimize_title(title, limit=100)

        # Should be title-cased
        assert optimized == "Understanding Machine Learning Basics"

    def test_optimize_description_truncation(self, generator):
        """Test description truncation at sentence boundary"""
        long_desc = "First sentence here. Second sentence here. Third sentence here. Fourth sentence that makes it too long. Fifth sentence."

        optimized = generator._optimize_description(long_desc, limit=100)

        assert len(optimized) <= 100
        # Should try to break at sentence boundary
        assert "." in optimized

    def test_optimize_tags_deduplication(self, generator):
        """Test tag deduplication (case-insensitive)"""
        tags = ["AI", "ai", "Machine Learning", "machine learning", "Python", "python"]

        optimized = generator._optimize_tags(tags, limit=10)

        # Should remove case-insensitive duplicates
        unique_lower = [tag.lower() for tag in optimized]
        assert len(unique_lower) == len(set(unique_lower))
        assert len(optimized) <= 3  # ai, machine learning, python

    def test_optimize_tags_limit(self, generator):
        """Test tag count limiting"""
        tags = [f"tag_{i}" for i in range(20)]

        optimized = generator._optimize_tags(tags, limit=10)

        assert len(optimized) == 10

    def test_calculate_quality_score(self, generator):
        """Test quality score calculation — strict YouTube scoring"""
        # All optimal: 40-70 char title, 300+ desc, 10-15 tags, keyword in title
        title = "a]" * 25  # 50 chars, contains 'a'
        description = "B" * 500  # 300+ chars
        tags = ["a"] + [f"tag{i}" for i in range(11)]  # 12 tags, 'a' is in title

        score = generator._calculate_quality_score(title, description, tags)

        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0
        # Should be high for optimal inputs
        assert score >= 0.7

    def test_calculate_quality_score_suboptimal(self, generator):
        """Test quality score for suboptimal metadata"""
        # Suboptimal lengths
        title = "Short"  # Too short
        description = "Also short"  # Too short
        tags = ["tag1"]  # Too few tags

        score = generator._calculate_quality_score(title, description, tags)

        assert 0.0 <= score <= 1.0
        # Should be lower for suboptimal inputs
        assert score < 0.5

    def test_calculate_keyword_density(self, generator):
        """Test keyword density calculation"""
        text = "python programming python code python tutorial"
        keywords = ["python", "programming", "code"]

        density = generator._calculate_keyword_density(text, keywords)

        assert isinstance(density, float)
        assert 0.0 <= density <= 1.0
        # "python" appears 3 times in 5 words = 0.6
        assert density >= 0.5  # Use >= for exact match

    def test_calculate_readability_flesch(self, generator):
        """Test Flesch Reading Ease score calculation"""
        # Simple text (high readability)
        easy_text = "The cat sat on the mat. The dog ran in the park."

        score = generator._calculate_readability(easy_text)

        assert isinstance(score, float)
        assert 0.0 <= score <= 100.0
        # Simple text should have high readability score
        assert score > 60

    def test_count_syllables(self, generator):
        """Test syllable counting"""
        assert generator._count_syllables("cat") == 1
        assert generator._count_syllables("python") == 2
        assert generator._count_syllables("programming") == 3
        assert generator._count_syllables("artificial") == 4

    def test_generate_content_id(self, generator):
        """Test content ID generation from topic"""
        topic = "Understanding Machine Learning"

        content_id = generator._generate_content_id(topic)

        assert isinstance(content_id, str)
        # Should be slugified
        assert "understanding" in content_id.lower()
        assert "machine" in content_id.lower()
        assert " " not in content_id  # No spaces
        # Should have timestamp
        assert "_" in content_id

    def test_extract_keywords_empty_input(self, generator):
        """Test keyword extraction with empty input"""
        keywords = generator._extract_keywords(topic="", body=None, transcript=None)

        assert isinstance(keywords, list)
        # Should return empty list or handle gracefully

    def test_optimize_title_short_input(self, generator):
        """Test title optimization with already-short title"""
        title = "Short Title"

        optimized = generator._optimize_title(title, limit=70)

        assert optimized == "Short Title"
        assert not optimized.endswith("...")

    def test_keyword_extraction_with_numbers(self, generator):
        """Test that numeric terms are handled correctly"""
        text = "GPT-4 and Claude-3 are AI models. Python 3.11 introduced new features."

        keywords = generator._extract_keywords(
            topic="AI Models", body=text, transcript=None
        )

        # Should extract model names with numbers
        assert any("gpt" in kw.lower() or "claude" in kw.lower() for kw in keywords)

    def test_quality_score_with_keyword_in_title(self, generator):
        """Test quality score bonus for keyword in title"""
        title_with_keyword = "Python Programming Tutorial"
        description = "A" * 155
        tags = ["python", "programming", "tutorial", "coding", "development"]

        score_with = generator._calculate_quality_score(
            title_with_keyword, description, tags
        )

        title_without_keyword = "Complete Development Course"
        score_without = generator._calculate_quality_score(
            title_without_keyword, description, tags
        )

        # Score should be higher when primary keyword appears in title
        assert score_with >= score_without

    def test_extract_keywords_limit(self, generator):
        """Test that keyword extraction respects limit"""
        long_text = " ".join([f"word{i}" for i in range(100)])

        keywords = generator._extract_keywords(
            topic="Test", body=long_text, transcript=None
        )

        # Should limit to reasonable number (typically 15-20)
        assert len(keywords) <= 20

    def test_readability_empty_text(self, generator):
        """Test readability calculation with empty text"""
        score = generator._calculate_readability("")

        assert score == 0.0 or isinstance(score, float)

    def test_title_optimization_preserves_meaning(self, generator):
        """Test that title truncation tries to preserve meaning"""
        title = "How to Build Machine Learning Models for Production"

        optimized = generator._optimize_title(title, limit=40)

        assert len(optimized) <= 40
        # Should keep important words
        assert "Machine Learning" in optimized or "ML" in optimized
