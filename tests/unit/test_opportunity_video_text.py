"""Read-only video text fetch: chunking, missing ids, error handling."""

from __future__ import annotations

from datetime import UTC, datetime

from seo_manager.opportunities.contracts import VideoText
from seo_manager.opportunities.video_text import fetch_video_text
from seo_manager.transport import AuthError, QuotaExhaustedError, TransientError


class FakeYouTube:
    def __init__(self, items=None, errors=None):
        self.items = items or {}
        self.errors = errors or {}  # call index -> exception
        self.calls: list[dict] = []

    def videos(self):
        return self

    def list(self, **kw):
        self.calls.append(kw)
        return self

    def execute(self):
        idx = len(self.calls) - 1
        if idx in self.errors:
            raise self.errors[idx]
        ids = self.calls[idx]["id"].split(",")
        return {
            "items": [
                {"id": i, "snippet": {"title": f"T{i}", "description": f"D{i}"}}
                for i in ids
                if i in self.items
            ]
        }


def direct(fn):
    return fn()


def ids(n):
    return [f"v{i}" for i in range(n)]


def test_chunks_of_fifty_and_request_shape():
    yt = FakeYouTube({i: 1 for i in ids(120)})
    res = fetch_video_text(yt, ids(120), read=direct)
    assert [len(c["id"].split(",")) for c in yt.calls] == [50, 50, 20]
    assert all(c["part"] == "snippet" and c["maxResults"] == 50 for c in yt.calls)
    assert res.texts["v7"] == VideoText("Tv7", "Dv7")
    assert res.error_class is None and len(res.texts) == 120


def test_missing_id_is_none():
    yt = FakeYouTube({"v0": 1})
    res = fetch_video_text(yt, ["v0", "v1"], read=direct)
    assert res.texts["v1"] is None and res.texts["v0"] is not None
    assert res.error_class is None


def test_duplicate_ids_requested_once():
    yt = FakeYouTube({"v0": 1})
    fetch_video_text(yt, ["v0", "v0"], read=direct)
    assert yt.calls[0]["id"] == "v0"


def test_transport_error_nones_that_chunk_and_continues():
    yt = FakeYouTube({i: 1 for i in ids(60)}, {0: TransientError("secret-token")})
    res = fetch_video_text(yt, ids(60), read=direct)
    assert all(res.texts[i] is None for i in ids(50))
    assert res.texts["v55"] is not None
    assert res.error_class == "TransientError"
    assert "secret-token" not in repr(res)


def test_terminal_error_stops_fetching():
    yt = FakeYouTube(
        {i: 1 for i in ids(120)},
        {
            1: QuotaExhaustedError(
                "tok", next_eligible=datetime(2026, 10, 28, tzinfo=UTC)
            )
        },
    )
    res = fetch_video_text(yt, ids(120), read=direct)
    assert len(yt.calls) == 2
    assert res.texts["v0"] is not None
    assert all(res.texts[i] is None for i in ids(120)[50:])
    assert res.error_class == "QuotaExhaustedError"
    assert "tok" not in repr(res)


def test_auth_error_recorded_by_type():
    yt = FakeYouTube({}, {0: AuthError("tok")})
    res = fetch_video_text(yt, ["a"], read=direct)
    assert res.texts == {"a": None} and res.error_class == "AuthError"


def test_no_ids_makes_no_call():
    yt = FakeYouTube()
    res = fetch_video_text(yt, [], read=direct)
    assert yt.calls == [] and res.texts == {}


def test_state_says_partial_when_some_chunks_failed():
    yt = FakeYouTube({i: 1 for i in ids(60)}, {0: TransientError("x")})
    assert fetch_video_text(yt, ids(60), read=direct).state == "partial"


def test_state_is_error_when_nothing_was_fetched():
    yt = FakeYouTube({}, {0: AuthError("x")})
    assert fetch_video_text(yt, ["a"], read=direct).state == "error"


def test_state_is_fetched_without_errors():
    yt = FakeYouTube({"a": 1})
    assert fetch_video_text(yt, ["a", "b"], read=direct).state == "fetched"
