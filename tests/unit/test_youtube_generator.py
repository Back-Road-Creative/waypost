"""
Unit tests for YouTubeMetadataGenerator.

Tests cover:
- Chapter marker extraction from transcript timestamps
- Citation formatting with numbered references
- Hook generation (first 125 chars for "above fold")
- Episode branding integration
- YouTube-specific title formatting
- YouTube-specific description structure
"""

import pytest

from seo_manager.core.metadata import ContentType, Platform
from seo_manager.generation.youtube_generator import YouTubeMetadataGenerator
from seo_manager.platforms.youtube import YouTubeAdapter


class TestChapterMarkerExtraction:
    """Test chapter marker extraction from transcript"""

    @pytest.fixture
    def generator(self):
        return YouTubeMetadataGenerator(adapter=YouTubeAdapter())

    def test_extract_chapters_from_transcript(self, generator):
        """Test extracting chapter markers from transcript with timestamps"""
        transcript = [
            {"speaker": "Host", "text": "Welcome to the show", "start_time": 0.0},
            {"speaker": "Host", "text": "Today we discuss AI", "start_time": 15.5},
            {"speaker": "Guest", "text": "AI is fascinating", "start_time": 45.2},
            {"speaker": "Host", "text": "Let's talk about safety", "start_time": 120.0},
            {"speaker": "Guest", "text": "Safety is crucial", "start_time": 125.0},
        ]

        chapters = generator._extract_chapters(transcript)

        assert isinstance(chapters, list)
        assert len(chapters) > 0
        # Should have timestamps and labels
        for chapter in chapters:
            assert "time" in chapter
            assert "label" in chapter
            assert isinstance(chapter["time"], str)  # e.g., "00:00"

    def test_chapter_timestamps_formatting(self, generator):
        """Test chapter timestamps are formatted as MM:SS"""
        transcript = [
            {"speaker": "Host", "text": "Introduction", "start_time": 0.0},
            {
                "speaker": "Host",
                "text": "Next, let's discuss the main topic",
                "start_time": 75.5,
            },  # Transition keyword
            {
                "speaker": "Host",
                "text": "Finally, our conclusion",
                "start_time": 150.0,
            },  # Transition keyword
        ]

        chapters = generator._extract_chapters(transcript)

        # Check timestamp format
        assert chapters[0]["time"] == "00:00"
        # Should have extracted at least one chapter
        assert len(chapters) >= 1

    def test_chapter_labels_from_text(self, generator):
        """Test chapter labels are extracted from dialogue"""
        transcript = [
            {"speaker": "Host", "text": "Welcome to episode 42", "start_time": 0.0},
            {
                "speaker": "Host",
                "text": "Today we're discussing machine learning basics",
                "start_time": 30.0,
            },
            {
                "speaker": "Guest",
                "text": "First, let's talk about neural networks",
                "start_time": 120.0,
            },
        ]

        chapters = generator._extract_chapters(transcript)

        # Labels should summarize content
        assert any(
            "introduction" in ch["label"].lower() or "welcome" in ch["label"].lower()
            for ch in chapters
        )

    def test_no_chapters_without_timestamps(self, generator):
        """Test handling transcript without timestamps"""
        transcript = [
            {"speaker": "Host", "text": "No timestamps here"},
            {"speaker": "Guest", "text": "Just dialogue"},
        ]

        chapters = generator._extract_chapters(transcript)

        # Should return empty or basic chapter
        assert isinstance(chapters, list)


class TestCitationFormatting:
    """Test citation formatting with numbered references"""

    @pytest.fixture
    def generator(self):
        return YouTubeMetadataGenerator(adapter=YouTubeAdapter())

    def test_format_citations_numbered(self, generator):
        """Test citations are formatted with numbers"""
        citations = [
            {"text": "AI Research Paper", "url": "https://example.com/paper1"},
            {"text": "Machine Learning Article", "url": "https://example.com/article2"},
            {"text": "Deep Learning Study", "url": "https://example.com/study3"},
        ]

        formatted = generator._format_citations(citations)

        assert isinstance(formatted, str)
        assert "[1]" in formatted
        assert "[2]" in formatted
        assert "[3]" in formatted
        assert "AI Research Paper" in formatted
        assert "https://example.com/paper1" in formatted

    def test_format_empty_citations(self, generator):
        """Test handling empty citation list"""
        formatted = generator._format_citations([])

        assert formatted == "" or formatted is None

    def test_citation_format_structure(self, generator):
        """Test citation follows format: [N] Title - URL"""
        citations = [{"text": "Test Article", "url": "https://test.com"}]

        formatted = generator._format_citations(citations)

        assert "[1]" in formatted
        assert "Test Article" in formatted
        assert "https://test.com" in formatted
        assert "-" in formatted  # Separator


class TestHookGeneration:
    """Test hook generation for above-fold content"""

    @pytest.fixture
    def generator(self):
        return YouTubeMetadataGenerator(adapter=YouTubeAdapter())

    def test_generate_hook_from_transcript(self, generator):
        """Test generating engaging hook from transcript"""
        topic = "Understanding AI Safety"
        transcript = [
            {"speaker": "Host", "text": "What if AI becomes too powerful to control?"},
            {
                "speaker": "Guest",
                "text": "That's exactly what we need to discuss today.",
            },
        ]

        hook = generator._generate_hook(topic, transcript)

        assert isinstance(hook, str)
        assert len(hook) <= 125
        # Should include topic
        assert "AI Safety" in hook or "ai safety" in hook.lower()

    def test_hook_length_limit(self, generator):
        """Test hook is truncated to 125 chars"""
        topic = "Very Long Topic Name Here"
        transcript = [
            {
                "speaker": "Host",
                "text": "This is a very long opening statement that goes on and on and on with lots of details that would exceed the character limit",
            }
        ]

        hook = generator._generate_hook(topic, transcript)

        assert len(hook) <= 125
        # Should end with ellipsis if truncated
        if len(topic + transcript[0]["text"]) > 125:
            assert hook.endswith("...")

    def test_hook_without_transcript(self, generator):
        """Test hook generation with only topic"""
        topic = "Python Programming Basics"

        hook = generator._generate_hook(topic, transcript=None)

        assert isinstance(hook, str)
        assert len(hook) <= 125
        assert "Python" in hook

    def test_hook_without_transcript_makes_no_fabricated_claims(self, generator):
        """The no-transcript fallback must not claim content the video
        doesn't have. Prior bug: hook said "Dive into the latest
        developments, insights, and discussions" — boilerplate that's
        false for drive videos, photo posts, and any other content
        type without actual developments/insights/discussions. Violates
        the content-integrity rule (no claims the content does not support).
        """
        topic = "Sample County, Examplia"
        hook = generator._generate_hook(topic, transcript=None)

        # Forbidden boilerplate phrases that fabricate content claims
        forbidden = [
            "latest developments",
            "insights, and discussions",
            "developments, insights",
        ]
        for phrase in forbidden:
            assert phrase.lower() not in hook.lower(), (
                f"Hook contains fabricated-content phrase {phrase!r}; got {hook!r}"
            )
        # Must still reference the topic
        assert topic in hook or topic.lower() in hook.lower()

    def test_hook_includes_topic(self, generator):
        """Test hook always includes the main topic"""
        topic = "Machine Learning"
        transcript = [{"speaker": "Host", "text": "Let's dive in"}]

        hook = generator._generate_hook(topic, transcript)

        assert "Machine Learning" in hook or "machine learning" in hook.lower()


class TestEpisodeBranding:
    """Test episode branding integration"""

    @pytest.fixture
    def generator(self):
        return YouTubeMetadataGenerator(adapter=YouTubeAdapter())

    def test_title_with_episode_number(self, generator):
        """Test title includes episode branding"""
        topic = "AI Safety Discussion"
        keywords = ["ai", "safety"]

        title = generator._generate_title(
            topic, keywords, episode_number=42, show_name="Example Podcast"
        )

        # Should include episode number
        assert "#42" in title or "42" in title
        # Should include show name
        assert "Example Podcast" in title

    def test_title_format_with_branding(self, generator):
        """Test title follows format: Topic | Show #Number"""
        topic = "Understanding Neural Networks"
        keywords = ["neural", "networks"]

        title = generator._generate_title(
            topic, keywords, episode_number=10, show_name="AI Podcast"
        )

        # Should follow standard format
        assert "|" in title
        assert "#10" in title or "10" in title

    def test_title_without_episode_number(self, generator):
        """Test title works without episode number"""
        topic = "General AI Discussion"
        keywords = ["ai"]

        title = generator._generate_title(topic, keywords, show_name="Tech Talk")

        assert "Tech Talk" in title
        # Should still be valid even without episode number

    def test_title_abbreviation_for_long_show_name(self, generator):
        """Test show name abbreviation if title too long"""
        topic = "Very Long Topic Name That Takes Up Space"
        keywords = ["topic"]

        title = generator._generate_title(
            topic,
            keywords,
            episode_number=99,
            show_name="Example Podcast Podcast Network",
        )

        # Should abbreviate show name if needed
        assert len(title) <= 70


class TestYouTubeDescriptionStructure:
    """Test YouTube-specific description formatting"""

    @pytest.fixture
    def generator(self):
        return YouTubeMetadataGenerator(adapter=YouTubeAdapter())

    def test_description_with_timestamps(self, generator):
        """Test description includes timestamp section"""
        topic = "AI Tutorial"
        transcript = [
            {"speaker": "Host", "text": "Welcome", "start_time": 0.0},
            {"speaker": "Host", "text": "Main content", "start_time": 60.0},
        ]

        timestamps = [
            {"time": "00:00", "label": "Introduction"},
            {"time": "01:00", "label": "Main Topic"},
        ]

        description = generator._generate_description(
            topic,
            body=None,
            transcript=transcript,
            keywords=["ai", "tutorial"],
            timestamps=timestamps,
        )

        assert "TIMESTAMPS" in description or "timestamps" in description.lower()
        assert "00:00" in description
        assert "Introduction" in description

    def test_description_with_citations(self, generator):
        """Test description includes citations section"""
        topic = "AI Research"
        citations = [{"text": "Research Paper", "url": "https://example.com/paper"}]

        description = generator._generate_description(
            topic, body=None, transcript=[], keywords=["ai"], citations=citations
        )

        assert "CITATIONS" in description or "citations" in description.lower()
        assert "[1]" in description
        assert "Research Paper" in description
        assert "https://example.com/paper" in description

    def test_description_hook_first(self, generator):
        """Test hook appears first in description (critical for CTR)"""
        topic = "Python Tutorial"
        transcript = [{"speaker": "Host", "text": "Learn Python today"}]

        description = generator._generate_description(
            topic, body=None, transcript=transcript, keywords=["python"]
        )

        # First 125 chars should be the hook
        first_125 = description[:125]
        assert "Python" in first_125

    def test_description_has_no_ai_disclosure(self, generator):
        """No AI disclosure: nothing we publish is AI-generated."""
        description = generator._generate_description(
            "Test Topic", body=None, transcript=[], keywords=[]
        )

        assert "AI DISCLOSURE" not in description
        assert "AI-generated" not in description
        assert "ai-generated" not in description.lower()
        assert "synthesized" not in description.lower()


class TestYouTubeTagStrategy:
    """Test YouTube-specific tag generation"""

    @pytest.fixture
    def generator(self):
        return YouTubeMetadataGenerator(adapter=YouTubeAdapter())

    def test_tech_keyword_corpus(self, generator):
        """Test YouTube generator has tech keyword corpus"""
        assert hasattr(generator, "tech_keywords")
        assert isinstance(generator.tech_keywords, list)
        assert "ai" in generator.tech_keywords
        assert "machine learning" in generator.tech_keywords

    def test_generate_tags_multi_level(self, generator):
        """Test multi-level tag generation strategy"""
        keywords = ["python", "programming", "tutorial"]
        base_tags = ["tech", "education"]
        transcript = [
            {"speaker": "Host", "text": "Learn Python programming"},
            {"speaker": "Guest", "text": "NumPy is essential"},
        ]

        tags = generator._generate_tags(
            keywords, existing_tags=None, base_tags=base_tags, transcript=transcript
        )

        # Should include base tags
        assert "tech" in tags
        assert "education" in tags
        # Should include keywords
        assert any("python" in tag.lower() for tag in tags)
        # Should be limited to 15
        assert len(tags) <= 15

    def test_extract_transcript_tags(self, generator):
        """Test extracting specific tags from transcript"""
        transcript = [
            {"speaker": "Host", "text": "Today we discuss ChatGPT and GPT-4"},
            {"speaker": "Guest", "text": "OpenAI and Anthropic are leaders"},
        ]

        tags = generator._extract_transcript_tags(transcript)

        assert isinstance(tags, list)
        # Should extract brand names
        # (may be lowercase based on implementation)
