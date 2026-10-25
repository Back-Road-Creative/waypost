"""A failed underlying upload must not become a successful batch result."""

from unittest.mock import Mock

import pytest

from seo_manager.core.metadata import ContentType, Platform, SEOMetadata
from seo_manager.youtube.batch_uploader import (
    BatchUploadConfig,
    BatchUploader,
    VideoConfig,
)


@pytest.fixture
def batch(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"synthetic fixture")
    metadata = SEOMetadata(
        site_id="example",
        content_id="fixture",
        platform=Platform.YOUTUBE,
        content_type=ContentType.VIDEO,
        title="Example",
        description="Example video",
        tags=["example"],
    )
    uploader = BatchUploader("unused.json", "example")
    uploader._uploader = Mock()
    return uploader, VideoConfig(str(video), metadata)


@pytest.mark.parametrize("invalid_id", [None, "", "   "])
def test_missing_id_records_failure_and_continues(batch, invalid_id):
    uploader, video = batch
    uploader._uploader.upload_video.side_effect = [invalid_id, "video123"]
    result = uploader.upload_batch(BatchUploadConfig([video, video]))
    assert result.failed == 1 and result.successful == 1
    assert not result.is_complete_success()
    assert not result.results[0].success
    assert result.results[0].video_id is None and result.results[0].video_url is None
    assert "video ID" in result.results[0].error
    assert result.results[1].video_id == "video123"
    assert uploader._uploader.upload_video.call_count == 2


def test_missing_id_obeys_stop_on_error(batch):
    uploader, video = batch
    uploader._uploader.upload_video.return_value = None
    result = uploader.upload_batch(
        BatchUploadConfig([video, video], continue_on_error=False)
    )
    assert result.failed == 1 and result.successful == 0
    assert uploader._uploader.upload_video.call_count == 1


def test_successful_dry_run_still_uses_the_uploader_validation(batch):
    uploader, video = batch
    uploader._uploader.upload_video.return_value = "DRY_RUN_SUCCESS"
    result = uploader.upload_batch(BatchUploadConfig([video], dry_run=True))
    assert result.successful == 1 and result.failed == 0
    assert uploader._uploader.upload_video.call_args.kwargs["dry_run"] is True
