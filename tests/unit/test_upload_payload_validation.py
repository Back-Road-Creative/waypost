"""The payload YouTubeUploader.upload_video sends is validated before it is sent.

The gate is checked on the final request body handed to ``videos().insert``,
not on the SEOMetadata the caller passed in: a spy on
``PlatformValidator.validate`` records what it saw, and the test compares that
with the body the fake API client received.
"""

from unittest.mock import MagicMock

import pytest

from seo_manager.core.metadata import ContentType, Platform, SEOMetadata
from seo_manager.validation.validators import PlatformValidator
from seo_manager.youtube import uploader as uploader_mod
from seo_manager.youtube.uploader import YouTubeUploader

DESCRIPTION = "A walk along the river trail at first light. " * 5
TAGS = ["river trail", "morning walk", "hiking", "nature"]


def _metadata(title: str, **overrides) -> SEOMetadata:
    fields = {
        "site_id": "channel",
        "content_id": "video-1",
        "platform": Platform.YOUTUBE,
        "content_type": ContentType.VIDEO,
        "title": title,
        "description": DESCRIPTION,
        "tags": list(TAGS),
    }
    fields.update(overrides)
    return SEOMetadata(**fields)


@pytest.fixture
def sent(monkeypatch, tmp_path):
    """An uploader whose fake API client records every insert body."""
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\0")
    monkeypatch.setattr(uploader_mod, "MediaFileUpload", MagicMock())

    bodies = []
    request = MagicMock()
    request.next_chunk.return_value = (None, {"id": "vid123"})

    def insert(part, body, media_body):
        bodies.append(body)
        return request

    up = YouTubeUploader.__new__(YouTubeUploader)
    up.youtube = MagicMock()
    up.youtube.videos.return_value.insert.side_effect = insert
    up.quota_used = 0
    return up, str(video), bodies


@pytest.fixture
def seen(monkeypatch):
    """Spy on the validator: record each metadata it validates."""
    calls = []
    real = PlatformValidator.validate

    def spy(self, metadata):
        calls.append(metadata)
        return real(self, metadata)

    monkeypatch.setattr(PlatformValidator, "validate", spy)
    return calls


def test_validator_sees_exactly_the_snippet_that_is_sent(sent, seen):
    up, video, bodies = sent
    assert up.upload_video(video, _metadata("River trail at first light")) == "vid123"

    assert len(bodies) == 1 and len(seen) == 1
    snippet = bodies[0]["snippet"]
    assert (seen[0].title, seen[0].description, seen[0].tags) == (
        snippet["title"],
        snippet["description"],
        snippet["tags"],
    )


def test_over_limit_title_is_refused_before_any_api_call(sent):
    up, video, bodies = sent
    with pytest.raises(ValueError, match="Title exceeds 100"):
        up.upload_video(video, _metadata("x" * 101))
    assert bodies == []
    up.youtube.videos.assert_not_called()


def test_over_limit_description_is_refused(sent):
    up, video, bodies = sent
    with pytest.raises(ValueError, match="Description exceeds 5000"):
        up.upload_video(video, _metadata("River trail", description="d" * 5001))
    assert bodies == []


def test_dry_run_runs_the_same_gate(sent):
    up, video, bodies = sent
    with pytest.raises(ValueError, match="Title exceeds 100"):
        up.upload_video(video, _metadata("x" * 101), dry_run=True)
    assert bodies == []


def test_title_at_the_api_limit_is_sent(sent):
    # The gate uses YouTube's hard limit (100), not the 70-char display
    # recommendation in YouTubeAdapter, so an API-legal title still uploads.
    up, video, bodies = sent
    assert up.upload_video(video, _metadata("y" * 100)) == "vid123"
    assert bodies[0]["snippet"]["title"] == "y" * 100
