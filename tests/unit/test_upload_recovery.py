"""An interrupted upload resumes or reconciles; it is never inserted blindly twice.

Fake clients only: no credentials, no network. A ``FakeRequest`` scripts what
each ``next_chunk`` call does; ``FakeYouTube`` answers the reconcile reads
(``channels.list`` -> uploads playlist -> ``playlistItems.list``, and
``videos.list`` for a journalled ID) and counts every ``videos.insert``.
"""

import json
import ssl
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError

from seo_manager.core.metadata import ContentType, Platform, SEOMetadata
from seo_manager.quota.manager import QuotaExceededError, QuotaManager
from seo_manager.transport import AuthError, QuotaExhaustedError, RetryPolicy
from seo_manager.youtube import uploader as uploader_mod
from seo_manager.youtube.upload_recovery import (
    RECONCILE_GRACE,
    UploadJournal,
    UploadOutcomeUnknown,
    UploadState,
)
from seo_manager.youtube.uploader import YouTubeUploader

from .final_text_fixture import sent_fields, upload_kwargs

TITLE = "Quillhaven Harbour at dawn"
NOW = datetime(2026, 10, 27, 17, 0, tzinfo=UTC)
DATA = "youtube_data"


class _Resp(dict):
    def __init__(self, status, headers=None):
        super().__init__(headers or {})
        self.status = status
        self.reason = "x"


def http_error(status, reason=None, headers=None):
    body = {"error": {"code": status, "message": "m", "errors": []}}
    if reason is not None:
        body["error"]["errors"].append({"reason": reason, "domain": "youtube"})
    return HttpError(_Resp(status, headers), json.dumps(body).encode())


class FakeRequest:
    """``steps``: an exception to raise, or a response dict to finish with.

    ``session`` is when the resumable session URI exists: before the first
    step unless ``session_from`` says a later step creates it.
    """

    def __init__(self, steps, session_from=0):
        self.steps = list(steps)
        self.calls = 0
        self.session_from = session_from
        self.resumable_uri = None

    def next_chunk(self):
        step = self.steps.pop(0)
        if self.calls >= self.session_from:
            self.resumable_uri = "https://upload.example/session"
        self.calls += 1
        if isinstance(step, BaseException):
            raise step
        return None, step


class FakeYouTube:
    def __init__(self, requests=(), uploads=(), existing=(), read_errors=()):
        self.requests = list(requests)
        self.inserts = 0
        self.uploads = list(uploads)  # playlistItems in the uploads playlist
        self.existing = set(existing)  # IDs videos.list still finds
        self.read_errors = list(read_errors)

    def _read(self, result):
        call = MagicMock()

        def execute():
            if self.read_errors:
                raise self.read_errors.pop(0)
            return result()

        call.execute.side_effect = execute
        return call

    def videos(self):
        api = MagicMock()

        def insert(**_):
            self.inserts += 1
            return self.requests.pop(0)

        api.insert.side_effect = insert
        api.list.side_effect = lambda id, **_: self._read(
            lambda: {"items": [{"id": id}] if id in self.existing else []}
        )
        return api

    def channels(self):
        api = MagicMock()
        api.list.side_effect = lambda **_: self._read(
            lambda: {
                "items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UUx"}}}]
            }
        )
        return api

    def playlistItems(self):
        api = MagicMock()
        api.list.side_effect = lambda **_: self._read(lambda: {"items": self.uploads})
        return api


def uploaded_item(video_id, title=TITLE, description=None, at=NOW):
    description = (
        sent_fields(TITLE)["description"] if description is None else description
    )
    return {
        "snippet": {
            "title": title,
            "description": description,
            "publishedAt": at.isoformat().replace("+00:00", "Z"),
            "resourceId": {"videoId": video_id},
        }
    }


def _metadata():
    return SEOMetadata(
        site_id="channel",
        content_id="video-1",
        platform=Platform.YOUTUBE,
        content_type=ContentType.VIDEO,
        title=TITLE,
        **sent_fields(TITLE),
    )


@pytest.fixture
def make(monkeypatch, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\0" * 64)
    monkeypatch.setattr(uploader_mod, "MediaFileUpload", MagicMock())
    sleeps = []

    def build(youtube, quota=True, journal=True):
        up = YouTubeUploader.__new__(YouTubeUploader)
        up.youtube = youtube
        up.quota_used = 0
        up.quota = (
            QuotaManager("proj", storage_path=tmp_path / "quota", clock=lambda: NOW)
            if quota
            else None
        )
        up.journal = UploadJournal(tmp_path / "journal") if journal else None
        up.resume_policy = RetryPolicy(max_attempts=3, base_delay=1, max_delay=2)
        up.sleep = sleeps.append
        up.clock = lambda: NOW
        return up

    build.video = str(video)
    build.sleeps = sleeps
    return build


def _upload(up, video):
    return up.upload_video(video, _metadata(), **upload_kwargs(TITLE))


def _usage(up, bucket):
    return up.quota.get_bucket_usage(DATA, bucket)


def _record(up, video):
    return up.journal.get(up.last_upload_key)


class TestResume:
    def test_a_clean_upload_settles_one_insert_call_and_journals_it(self, make):
        up = make(FakeYouTube([FakeRequest([{"id": "vid1"}])]))
        assert _upload(up, make.video) == "vid1"
        assert _usage(up, "videos.insert")["used"] == 1
        assert _usage(up, "videos.insert")["reserved"] == 0
        rec = _record(up, make.video)
        assert rec.state == UploadState.UPLOADED and rec.video_id == "vid1"

    @pytest.mark.parametrize(
        "fault",
        [
            TimeoutError("read timed out"),
            ConnectionResetError("reset"),
            ssl.SSLError("EOF occurred in violation of protocol"),
            http_error(503, "backendError"),
        ],
    )
    def test_a_transient_fault_resumes_the_same_session(self, make, fault):
        request = FakeRequest([fault, {"id": "vid1"}])
        youtube = FakeYouTube([request])
        up = make(youtube)
        assert _upload(up, make.video) == "vid1"
        assert youtube.inserts == 1  # resumed, not re-inserted
        assert request.calls == 2

    def test_retry_after_paces_the_resume(self, make):
        fault = http_error(429, "rateLimitExceeded", {"retry-after": "2"})
        up = make(FakeYouTube([FakeRequest([fault, {"id": "vid1"}])]))
        assert _upload(up, make.video) == "vid1"
        assert make.sleeps == [2.0]


class TestAmbiguousOutcome:
    def test_exhausted_resumes_reconcile_and_find_the_video(self, make):
        faults = [TimeoutError("t")] * 3
        youtube = FakeYouTube([FakeRequest(faults)], uploads=[uploaded_item("vid9")])
        up = make(youtube)
        assert _upload(up, make.video) == "vid9"
        assert youtube.inserts == 1
        assert _record(up, make.video).state == UploadState.UPLOADED

    def test_not_found_right_after_an_interruption_is_unknown_not_absent(self, make):
        youtube = FakeYouTube([FakeRequest([TimeoutError("t")] * 3)])
        up = make(youtube)
        with pytest.raises(UploadOutcomeUnknown):
            _upload(up, make.video)
        assert youtube.inserts == 1
        assert _record(up, make.video).state == UploadState.UNKNOWN
        # The reservation is charged, not refunded: the request was sent.
        assert _usage(up, "videos.insert")["used"] == 1
        assert _usage(up, "videos.insert")["reserved"] == 0

    def test_two_matching_uploads_are_unknown_never_a_guess(self, make):
        youtube = FakeYouTube(
            [FakeRequest([TimeoutError("t")] * 3)],
            uploads=[uploaded_item("a"), uploaded_item("b")],
        )
        with pytest.raises(UploadOutcomeUnknown, match="2 uploads match") as exc:
            _upload(make(youtube), make.video)
        assert exc.value.error_class == "outcome_unknown"  # batch results show it

    def test_an_older_or_different_upload_does_not_match(self, make):
        youtube = FakeYouTube(
            [FakeRequest([TimeoutError("t")] * 3)],
            uploads=[
                uploaded_item("old", at=NOW - timedelta(days=2)),
                uploaded_item("other", title="Another harbour"),
            ],
        )
        with pytest.raises(UploadOutcomeUnknown):
            _upload(make(youtube), make.video)

    def test_a_failed_reconcile_read_is_unknown(self, make):
        youtube = FakeYouTube(
            [FakeRequest([TimeoutError("t")] * 3)],
            read_errors=[http_error(403, "forbidden")],
        )
        with pytest.raises(UploadOutcomeUnknown, match="reconcile"):
            _upload(make(youtube), make.video)

    def test_reconcile_reads_are_budgeted(self, make):
        youtube = FakeYouTube(
            [FakeRequest([TimeoutError("t")] * 3)], uploads=[uploaded_item("v")]
        )
        up = make(youtube)
        _upload(up, make.video)
        assert _usage(up, "default")["used"] == 2  # channels.list + playlistItems.list


class TestNextRun:
    def test_unknown_then_found_returns_it_without_a_second_insert(self, make):
        first = FakeYouTube([FakeRequest([TimeoutError("t")] * 3)])
        up = make(first)
        with pytest.raises(UploadOutcomeUnknown):
            _upload(up, make.video)
        second = FakeYouTube([], uploads=[uploaded_item("late")])
        up.youtube = second
        assert _upload(up, make.video) == "late"
        assert second.inserts == 0

    def test_unknown_then_absent_after_the_grace_period_uploads(self, make):
        up = make(FakeYouTube([FakeRequest([TimeoutError("t")] * 3)]))
        with pytest.raises(UploadOutcomeUnknown):
            _upload(up, make.video)
        again = FakeYouTube([FakeRequest([{"id": "fresh"}])])
        up.youtube = again
        up.clock = lambda: NOW + RECONCILE_GRACE
        assert _upload(up, make.video) == "fresh"
        assert again.inserts == 1

    def test_unknown_then_absent_too_soon_stays_unknown(self, make):
        up = make(FakeYouTube([FakeRequest([TimeoutError("t")] * 3)]))
        with pytest.raises(UploadOutcomeUnknown):
            _upload(up, make.video)
        again = FakeYouTube([FakeRequest([{"id": "dup"}])])
        up.youtube = again
        up.clock = lambda: NOW + RECONCILE_GRACE - timedelta(seconds=1)
        with pytest.raises(UploadOutcomeUnknown, match="not visible yet"):
            _upload(up, make.video)
        assert again.inserts == 0

    def test_an_uploaded_record_that_still_exists_is_not_reuploaded(self, make):
        up = make(FakeYouTube([FakeRequest([{"id": "vid1"}])]))
        _upload(up, make.video)
        again = FakeYouTube([], existing={"vid1"})
        up.youtube = again
        assert _upload(up, make.video) == "vid1"
        assert again.inserts == 0

    def test_an_uploaded_record_whose_video_is_gone_uploads_again(self, make):
        up = make(FakeYouTube([FakeRequest([{"id": "vid1"}])]))
        _upload(up, make.video)
        again = FakeYouTube([FakeRequest([{"id": "vid2"}])], existing=set())
        up.youtube = again
        assert _upload(up, make.video) == "vid2"

    def test_a_crash_mid_upload_leaves_an_in_flight_record_that_reconciles(self, make):
        class Crash(BaseException):
            pass

        up = make(FakeYouTube([FakeRequest([Crash()])]))
        with pytest.raises(Crash):
            _upload(up, make.video)
        assert _record(up, make.video).state == UploadState.IN_FLIGHT
        again = FakeYouTube([], uploads=[uploaded_item("vid3")])
        up.youtube = again
        assert _upload(up, make.video) == "vid3"
        assert again.inserts == 0

    def test_a_corrupt_record_reconciles_instead_of_trusting_it(self, make):
        up = make(FakeYouTube([FakeRequest([TimeoutError("t")] * 3)]))
        with pytest.raises(UploadOutcomeUnknown):
            _upload(up, make.video)
        up.journal._path(up.last_upload_key).write_text("{not json")
        up.youtube = FakeYouTube([], uploads=[uploaded_item("vid4")])
        assert _upload(up, make.video) == "vid4"


class TestRefusals:
    @pytest.mark.parametrize("status,reason", [(400, "invalidTitle"), (404, None)])
    def test_an_invalid_request_is_not_uploaded_and_returns_none(
        self, make, status, reason
    ):
        up = make(FakeYouTube([FakeRequest([http_error(status, reason)])]))
        assert _upload(up, make.video) is None
        assert _record(up, make.video).state == UploadState.NOT_UPLOADED
        assert _usage(up, "videos.insert")["charged_failures"] == 1

    def test_daily_quota_exhaustion_propagates_with_next_eligible(self, make):
        up = make(FakeYouTube([FakeRequest([http_error(403, "quotaExceeded")])]))
        with pytest.raises(QuotaExhaustedError) as caught:
            _upload(up, make.video)
        assert caught.value.next_eligible > NOW

    def test_auth_failure_propagates(self, make):
        up = make(FakeYouTube([FakeRequest([http_error(401)])]))
        with pytest.raises(AuthError):
            _upload(up, make.video)

    def test_a_session_start_fault_is_retried(self, make):
        request = FakeRequest([TimeoutError("t"), {"id": "v"}], session_from=1)
        youtube = FakeYouTube([request])
        assert _upload(make(youtube), make.video) == "v"
        assert youtube.inserts == 1

    def test_a_session_that_never_started_never_created_a_video(self, make):
        request = FakeRequest([TimeoutError("t")] * 3, session_from=99)
        up = make(FakeYouTube([request]))
        assert _upload(up, make.video) is None
        assert _record(up, make.video).state == UploadState.NOT_UPLOADED

    def test_local_budget_full_sends_nothing(self, make, tmp_path):
        youtube = FakeYouTube([FakeRequest([{"id": "never"}])])
        up = make(youtube)
        up.quota = QuotaManager(
            "full",
            storage_path=tmp_path / "q2",
            limits={f"{DATA}/videos.insert": 0},
            clock=lambda: NOW,
        )
        with pytest.raises(QuotaExceededError):
            _upload(up, make.video)
        assert youtube.inserts == 0

    def test_a_request_that_could_not_be_built_is_refunded(self, make, monkeypatch):
        monkeypatch.setattr(
            uploader_mod, "MediaFileUpload", MagicMock(side_effect=OSError("gone"))
        )
        up = make(FakeYouTube([]))
        with pytest.raises(OSError):
            _upload(up, make.video)
        assert _usage(up, "videos.insert")["used"] == 0
        assert _record(up, make.video).state == UploadState.NOT_UPLOADED

    def test_works_without_quota_or_journal(self, make):
        up = make(FakeYouTube([FakeRequest([{"id": "v"}])]), quota=False, journal=False)
        assert _upload(up, make.video) == "v"
