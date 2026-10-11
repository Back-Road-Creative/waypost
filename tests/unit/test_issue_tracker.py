"""
Unit tests for ProductionIssueTracker.

Tests issue management for production pipeline improvements.
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from seo_manager.production.issue_tracker import ProductionIssueTracker


@pytest.fixture
def temp_storage(tmp_path):
    """Temporary storage for tests"""
    return tmp_path / "issues"


@pytest.fixture
def tracker(temp_storage):
    """Create tracker instance"""
    return ProductionIssueTracker(temp_storage)


def test_add_issue(tracker):
    """Test adding a new issue"""
    issue_id = tracker.add_issue(
        brand_id="examplepodcast",
        category="audio",
        title="48kHz audio required",
        description="Edge TTS outputs 24kHz, need to upsample to 48kHz",
        priority="P0",
        phase="Phase 2A",
    )

    assert issue_id is not None
    assert issue_id.startswith("issue_")

    # Verify issue was stored
    issues = tracker.get_open_issues()
    assert len(issues) == 1
    assert issues[0]["issue_id"] == issue_id
    assert issues[0]["brand_id"] == "examplepodcast"
    assert issues[0]["category"] == "audio"
    assert issues[0]["priority"] == "P0"
    assert issues[0]["status"] == "open"


def test_resolve_issue(tracker):
    """Test resolving an issue"""
    issue_id = tracker.add_issue(
        brand_id="examplepodcast",
        category="pipeline",
        title="Correct model usage",
        description="Use actual Anthropic API for Claude",
        priority="P0",
        phase="Phase 7A",
    )

    tracker.resolve_issue(issue_id, "Implemented multi_provider_client.py")

    # Verify issue is resolved
    issues = tracker.get_open_issues()
    assert len(issues) == 0

    # Verify resolution recorded
    storage_path = tracker.issues_file
    with open(storage_path) as f:
        data = json.load(f)

    resolved_issue = data["issues"][issue_id]
    assert resolved_issue["status"] == "resolved"
    assert resolved_issue["resolution_notes"] == "Implemented multi_provider_client.py"
    assert "resolved_at" in resolved_issue


def test_filter_by_brand(tracker):
    """Test filtering issues by brand"""
    tracker.add_issue("examplepodcast", "audio", "Test 1", "Desc", "P0")
    tracker.add_issue("examplebrand", "video", "Test 2", "Desc", "P1")
    tracker.add_issue("examplepodcast", "pipeline", "Test 3", "Desc", "P2")

    podcast_issues = tracker.get_open_issues(brand_id="examplepodcast")
    assert len(podcast_issues) == 2
    assert all(issue["brand_id"] == "examplepodcast" for issue in podcast_issues)


def test_filter_by_category(tracker):
    """Test filtering issues by category"""
    tracker.add_issue("examplepodcast", "audio", "Test 1", "Desc", "P0")
    tracker.add_issue("examplepodcast", "video", "Test 2", "Desc", "P1")
    tracker.add_issue("examplepodcast", "audio", "Test 3", "Desc", "P2")

    audio_issues = tracker.get_open_issues(category="audio")
    assert len(audio_issues) == 2
    assert all(issue["category"] == "audio" for issue in audio_issues)


def test_filter_by_brand_and_category(tracker):
    """Test filtering by both brand and category"""
    tracker.add_issue("examplepodcast", "audio", "Test 1", "Desc", "P0")
    tracker.add_issue("examplebrand", "audio", "Test 2", "Desc", "P1")
    tracker.add_issue("examplepodcast", "video", "Test 3", "Desc", "P2")

    filtered = tracker.get_open_issues(brand_id="examplepodcast", category="audio")
    assert len(filtered) == 1
    assert filtered[0]["brand_id"] == "examplepodcast"
    assert filtered[0]["category"] == "audio"


def test_get_report(tracker):
    """Test report generation"""
    tracker.add_issue("examplepodcast", "audio", "Test 1", "Desc", "P0")
    tracker.add_issue("examplepodcast", "video", "Test 2", "Desc", "P0")
    tracker.add_issue("examplepodcast", "pipeline", "Test 3", "Desc", "P1")
    tracker.add_issue("examplepodcast", "music_sfx", "Test 4", "Desc", "P2")

    # Resolve one issue
    issues = tracker.get_open_issues()
    tracker.resolve_issue(issues[0]["issue_id"], "Fixed")

    report = tracker.get_report(brand_id="examplepodcast")

    assert report["total"] == 4
    assert report["open"] == 3
    assert report["resolved"] == 1
    assert "by_category" in report
    assert "by_priority" in report


def test_issue_count_by_priority(tracker):
    """Test counting issues by priority"""
    tracker.add_issue("examplepodcast", "audio", "Test 1", "Desc", "P0")
    tracker.add_issue("examplepodcast", "video", "Test 2", "Desc", "P0")
    tracker.add_issue("examplepodcast", "pipeline", "Test 3", "Desc", "P1")
    tracker.add_issue("examplepodcast", "music_sfx", "Test 4", "Desc", "P2")

    report = tracker.get_report(brand_id="examplepodcast")

    assert report["by_priority"]["P0"] == 2
    assert report["by_priority"]["P1"] == 1
    assert report["by_priority"]["P2"] == 1


def test_persistence_across_instances(temp_storage):
    """Test that issues persist across tracker instances"""
    tracker1 = ProductionIssueTracker(temp_storage)
    issue_id = tracker1.add_issue("examplepodcast", "audio", "Test", "Desc", "P0")

    # Create new tracker instance
    tracker2 = ProductionIssueTracker(temp_storage)
    issues = tracker2.get_open_issues()

    assert len(issues) == 1
    assert issues[0]["issue_id"] == issue_id


def test_invalid_category(tracker):
    """Test that invalid category raises error"""
    with pytest.raises(ValueError, match="Invalid category"):
        tracker.add_issue("examplepodcast", "invalid_category", "Test", "Desc", "P0")


def test_invalid_priority(tracker):
    """Test that invalid priority raises error"""
    with pytest.raises(ValueError, match="Invalid priority"):
        tracker.add_issue("examplepodcast", "audio", "Test", "Desc", "P99")
