"""A failed underlying upload must not become a successful batch result."""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from seo_manager.core.metadata import ContentType, Platform, SEOMetadata
from seo_manager.quota.manager import QuotaExceededError
from seo_manager.transport import AuthError, QuotaExhaustedError
from seo_manager.youtube.batch_uploader import (
    BatchUploadConfig,
    BatchUploader,
    VideoConfig,
)


@pytest.fixture
def batch(tmp_path, monkeypatch):
    monkeypatch.setenv("SEO_MANAGER_DATA_HOME", str(tmp_path / "data-home"))
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


def test_quota_exhaustion_defers_this_and_every_untried_video(batch):
    uploader, video = batch
    reset = datetime(2026, 10, 28, 7, 0, tzinfo=UTC)
    uploader._uploader.upload_video.side_effect = QuotaExhaustedError(
        "quota spent", next_eligible=reset
    )
    result = uploader.upload_batch(BatchUploadConfig([video, video, video]))
    assert uploader._uploader.upload_video.call_count == 1
    assert result.failed == 3 and result.successful == 0
    assert all(r.deferred for r in result.results)
    assert {r.error_class for r in result.results} == {"quota_exhausted"}
    assert {r.next_eligible for r in result.results} == {reset.isoformat()}
    assert result.results[0].error == "quota spent"
    assert result.results[2].error == "not tried: quota_exhausted"


def test_a_local_budget_refusal_also_defers(batch):
    uploader, video = batch
    uploader._uploader.upload_video.side_effect = QuotaExceededError("full")
    result = uploader.upload_batch(BatchUploadConfig([video, video]))
    assert uploader._uploader.upload_video.call_count == 1
    assert [r.error_class for r in result.results] == ["quota_budget"] * 2
    assert all(r.deferred and r.next_eligible is None for r in result.results)


def test_auth_stops_the_batch_without_deferring(batch):
    uploader, video = batch
    uploader._uploader.upload_video.side_effect = AuthError("denied", status=401)
    result = uploader.upload_batch(BatchUploadConfig([video, video]))
    assert uploader._uploader.upload_video.call_count == 1
    assert [r.error_class for r in result.results] == ["auth", "auth"]
    assert not any(r.deferred for r in result.results)


def test_a_stop_keeps_earlier_successes(batch):
    uploader, video = batch
    uploader._uploader.upload_video.side_effect = [
        "video1",
        QuotaExceededError("full"),
    ]
    result = uploader.upload_batch(BatchUploadConfig([video, video, video]))
    assert result.successful == 1 and result.failed == 2
    assert [r.deferred for r in result.results] == [False, True, True]


class _Classified(Exception):
    error_class = "outcome_unknown"


def test_a_classified_error_is_recorded_and_the_batch_continues(batch):
    uploader, video = batch
    uploader._uploader.upload_video.side_effect = [_Classified("?"), "video2"]
    result = uploader.upload_batch(BatchUploadConfig([video, video]))
    assert result.results[0].error_class == "outcome_unknown"
    assert not result.results[0].deferred
    assert result.results[1].video_id == "video2"
