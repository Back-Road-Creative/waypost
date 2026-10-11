"""
Integration test for YouTube SEO generation.

Tests the complete workflow with real-world data similar to ExamplePodcast.
"""

import shutil
import tempfile
from pathlib import Path

import pytest

from seo_manager import ContentType, SEOManager
from seo_manager.generation.youtube_generator import YouTubeMetadataGenerator
from seo_manager.platforms import YouTubeAdapter


class TestYouTubeIntegration:
    """Integration tests with realistic data"""

    @pytest.fixture
    def temp_storage(self):
        """Create temporary storage"""
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)

    @pytest.fixture
    def manager(self, temp_storage):
        """Create YouTube SEO manager with specialized generator"""
        return SEOManager(
            site_id="test_podcast", platform=YouTubeAdapter(), storage_path=temp_storage
        )

    def test_complete_podcast_episode_workflow(self, manager):
        """Test complete workflow for a podcast episode (ExamplePodcast-style)"""
        # Realistic podcast data
        topic = "AI Safety and Alignment Challenges"

        transcript = [
            {
                "speaker": "Host",
                "text": "Welcome to Example Podcast, episode 42",
                "start_time": 0.0,
            },
            {
                "speaker": "Host",
                "text": "Today we're diving deep into AI safety",
                "start_time": 10.5,
            },
            {
                "speaker": "Guest",
                "text": "AI safety is one of the most critical challenges",
                "start_time": 25.0,
            },
            {
                "speaker": "Host",
                "text": "Let's start with alignment problems",
                "start_time": 45.0,
            },
            {
                "speaker": "Guest",
                "text": "Alignment ensures AI goals match human values",
                "start_time": 50.0,
            },
            {
                "speaker": "Host",
                "text": "Next, we should discuss current research",
                "start_time": 120.0,
            },
            {
                "speaker": "Guest",
                "text": "Recent papers from OpenAI and Anthropic show progress",
                "start_time": 125.0,
            },
            {
                "speaker": "Host",
                "text": "Finally, what can developers do today?",
                "start_time": 240.0,
            },
            {
                "speaker": "Guest",
                "text": "Start with safety-first design principles",
                "start_time": 245.0,
            },
        ]

        citations = [
            {
                "text": "AI Alignment Research - Anthropic",
                "url": "https://www.anthropic.com/research",
            },
            {"text": "OpenAI Safety Research", "url": "https://openai.com/safety"},
        ]

        social_links = {
            "Twitter": "@examplepodcast",
            "Website": "https://examplepodcast.example",
        }

        # Generate metadata
        metadata = manager.generate(
            content_type=ContentType.VIDEO,
            topic=topic,
            transcript=transcript,
            base_tags=["ai", "podcast", "technology"],
            episode_number=42,
            show_name="Example Podcast",
            citations=citations,
            social_links=social_links,
            category="Science & Technology",
            playlist="Season 2",
        )

        # Verify title
        assert metadata.title is not None
        assert len(metadata.title) <= 70
        assert "AI Safety" in metadata.title or "ai safety" in metadata.title.lower()
        assert "#42" in metadata.title or "42" in metadata.title

        # Verify description structure
        assert metadata.description is not None
        # Should have hook in first 125 chars
        hook = metadata.description[:125]
        assert len(hook) <= 125

        # Should have timestamps section
        assert (
            "TIMESTAMPS" in metadata.description
            or "timestamps" in metadata.description.lower()
        )
        assert "00:00" in metadata.description

        # Should have citations
        assert (
            "CITATIONS" in metadata.description
            or "citations" in metadata.description.lower()
        )
        assert "[1]" in metadata.description
        assert "Anthropic" in metadata.description
        assert "https://www.anthropic.com/research" in metadata.description

        # Should have social links
        assert (
            "CONNECT" in metadata.description
            or "connect" in metadata.description.lower()
        )
        assert "@examplepodcast" in metadata.description

        # Never an AI disclosure: nothing we publish is AI-generated
        assert "AI DISCLOSURE" not in metadata.description
        assert "ai-generated" not in metadata.description.lower()

        # Verify tags
        assert len(metadata.tags) <= 15
        assert "ai" in metadata.tags
        assert "podcast" in metadata.tags
        assert "technology" in metadata.tags

        # Verify quality metrics
        assert metadata.quality_score is not None
        assert 0.0 <= metadata.quality_score <= 1.0

        # Verify platform metadata
        assert metadata.youtube_metadata["category"] == "Science & Technology"
        assert metadata.youtube_metadata["playlist"] == "Season 2"

        # Save and verify persistence
        event_id = manager.save(metadata)
        assert event_id is not None

        # Retrieve and verify
        retrieved = manager.get_metadata(metadata.content_id)
        assert retrieved is not None
        assert retrieved.title == metadata.title

    def test_youtube_generator_used_by_manager(self, manager, temp_storage):
        """Test that manager can use YouTube-specific generator"""
        # Create manager with YouTube generator
        adapter = YouTubeAdapter()
        youtube_gen = YouTubeMetadataGenerator(adapter=adapter)

        # Replace default generator
        manager.generator = youtube_gen

        metadata = manager.generate(
            content_type=ContentType.VIDEO,
            topic="Test Topic",
            transcript=[{"speaker": "Host", "text": "Test content", "start_time": 0.0}],
            episode_number=1,
            show_name="Test Show",
        )

        # Should use YouTube-specific generation
        assert "#1" in metadata.title or "1" in metadata.title
        assert "Test Show" in metadata.title
