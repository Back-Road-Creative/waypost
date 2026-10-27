"""youtube_sync adopts seo_manager.transport: retry reads, surface quota/auth."""

import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from googleapiclient.errors import HttpError

from seo_manager.analytics import youtube_sync as ys
from seo_manager.analytics.continuous_optimization_daemon import (
    ContinuousOptimizationDaemon,
    SyncOutcome,
)
from seo_manager.transport import AuthError, QuotaExhaustedError, RetryPolicy

LA = ZoneInfo("America/Los_Angeles")


class _Resp(dict):
    def __init__(self, status):
        super().__init__()
        self.status = status
        self.reason = "x"


def http_error(status, reason=None):
    body = {"error": {"code": status, "errors": []}}
    if reason:
        body["error"]["errors"].append({"reason": reason})
    return HttpError(_Resp(status), json.dumps(body).encode())


class _Resource:
    """Fake ``videos()`` / ``reports()``: each execute() pops the script."""

    def __init__(self, script, log, key):
        self.script, self.log, self.key = script, log, key

    def list(self, **kw):
        return self

    query = list

    def execute(self):
        self.log.append(self.key)
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, BaseException):
            raise item
        return item


STATS = {"items": [{"statistics": {"viewCount": "10", "likeCount": "1"}}]}
REPORT = {
    "columnHeaders": [{"name": "cardClickRate"}],
    "rows": [[0.05]],
}


@pytest.fixture
def make_sync(monkeypatch, tmp_path):
    token = tmp_path / "t.json"
    token.write_text(json.dumps({"scopes": ["a"]}))
    monkeypatch.setattr(
        ys.Credentials,
        "from_authorized_user_file",
        staticmethod(lambda p, s: SimpleNamespace()),
    )
    monkeypatch.setattr(ys, "build", lambda *a, **k: SimpleNamespace())

    def _make(stats_script, report_script=None):
        log, sleeps = [], []
        sync = ys.YouTubeAnalyticsSync(
            str(token),
            retry_policy=RetryPolicy(max_attempts=3, base_delay=1.0),
            sleep=sleeps.append,
            rand=lambda: 1.0,
        )
        sync.youtube_data = SimpleNamespace(
            videos=lambda: _Resource(stats_script, log, "stats")
        )
        sync.youtube_analytics = SimpleNamespace(
            reports=lambda: _Resource(report_script or [REPORT], log, "report")
        )
        return sync, log, sleeps

    return _make


class _Tracker:
    def __init__(self):
        self.calls = []

    def track_performance(self, **kw):
        self.calls.append(kw)
        return f"evt{len(self.calls)}"


def _manager():
    return SimpleNamespace(analytics=_Tracker())


def test_transient_then_success_retries_and_syncs(make_sync):
    sync, log, sleeps = make_sync([http_error(503), http_error(500), STATS])
    stats = sync.fetch_video_statistics("v1")
    assert stats == {"viewCount": "10", "likeCount": "1"}
    assert log == ["stats"] * 3
    assert sleeps == [1.0, 2.0]


def test_transient_beyond_bound_degrades_to_none(make_sync):
    sync, log, _ = make_sync([http_error(503)])
    assert sync.fetch_video_statistics("v1") is None
    assert log == ["stats"] * 3


def test_invalid_request_is_none_without_retry(make_sync):
    sync, log, sleeps = make_sync([http_error(404, "videoNotFound")])
    assert sync.fetch_video_statistics("v1") is None
    assert log == ["stats"]
    assert sleeps == []


def test_quota_exhaustion_raises_not_none(make_sync):
    sync, log, sleeps = make_sync([http_error(403, "quotaExceeded")])
    with pytest.raises(QuotaExhaustedError) as ei:
        sync.fetch_video_statistics("v1")
    assert ei.value.next_eligible.tzinfo is not None
    assert log == ["stats"]
    assert sleeps == []


def test_auth_failure_raises_distinctly(make_sync):
    sync, _, _ = make_sync([http_error(401)])
    with pytest.raises(AuthError):
        sync.fetch_video_statistics("v1")
    sync2, _, sleeps = make_sync([STATS], [http_error(403, "forbidden")])
    with pytest.raises(AuthError):
        sync2.fetch_video_analytics("v1")
    assert sleeps == []


def test_no_analytics_row_still_yields_ctr_none(make_sync):
    empty = {"columnHeaders": [], "rows": []}
    sync, _, _ = make_sync([STATS], [empty])
    manager = _manager()
    assert sync.sync_video_analytics(manager, "v1", "c1") == "evt1"
    assert manager.analytics.calls[0]["ctr"] is None


def test_batch_quota_mid_run_stops_calls_and_defers_rest(make_sync):
    # v1 fine; v2's stats read hits daily quota; v3 must not be attempted.
    sync, log, _ = make_sync([STATS, http_error(403, "quotaExceeded")], [REPORT])
    manager = _manager()
    results = sync.batch_sync(manager, {"v1": "c1", "v2": "c2", "v3": "c3"})

    assert [r["success"] for r in results] == [True, False, False]
    assert [r["video_id"] for r in results] == ["v1", "v2", "v3"]
    for r in results[1:]:
        assert r["error_class"] == "quota_exhausted"
        assert r["deferred"] is True
        nxt = datetime.fromisoformat(r["next_eligible"])
        assert (nxt.hour, nxt.minute) == (0, 0)
        assert nxt.astimezone(LA).utcoffset() == nxt.utcoffset()
    # 2 stats calls for v1/v2 + 1 report call for v1; nothing for v3.
    assert log.count("stats") == 2
    assert len(manager.analytics.calls) == 1


def test_batch_auth_failure_stops_and_is_distinct(make_sync):
    sync, log, _ = make_sync([http_error(401)])
    results = sync.batch_sync(_manager(), {"v1": "c1", "v2": "c2"})
    assert [r["error_class"] for r in results] == ["auth", "auth"]
    assert not any(r.get("deferred") for r in results)
    assert log == ["stats"]


# ── the daemon maps these onto the existing SyncOutcome vocabulary ─────


class _Collector:
    def __init__(self, results):
        self._results = results

    def batch_sync(self, manager, mapping):
        return self._results


def _daemon(tmp_path, results):
    mapping = tmp_path / "m.json"
    mapping.write_text(json.dumps({"v1": "c1", "v2": "c2"}))
    return ContinuousOptimizationDaemon(
        manager=SimpleNamespace(),
        state_dir=tmp_path / "state",
        youtube_sync=_Collector(results),
        video_mapping_file=mapping,
    )


_NXT = "2026-07-11T00:00:00-07:00"
_QUOTA = {"error_class": "quota_exhausted", "deferred": True, "next_eligible": _NXT}
_OK = {"video_id": "v1", "content_id": "c1", "success": True}


def _failed(video, **extra):
    return {"video_id": video, "content_id": video, "success": False, **extra}


def test_daemon_quota_deferral_is_partial_with_next_eligible(tmp_path):
    out = _daemon(tmp_path, [_OK, _failed("v2", **_QUOTA)])._sync_youtube_analytics()
    assert out.sync_outcome is SyncOutcome.PARTIAL
    assert out.metrics["videos_deferred"] == 1
    assert out.metrics["sync_next_eligible"] == _NXT
    assert out.metrics["sync_blocked_by"] == ["quota_exhausted"]


def test_daemon_all_deferred_is_failed_not_empty(tmp_path):
    rows = [_failed("v1", **_QUOTA), _failed("v2", **_QUOTA)]
    out = _daemon(tmp_path, rows)._sync_youtube_analytics()
    assert out.sync_outcome is SyncOutcome.FAILED
    assert out.success is False
    assert out.metrics["sync_next_eligible"] == _NXT


def test_daemon_auth_stop_is_reported(tmp_path):
    rows = [_failed("v1", error_class="auth"), _failed("v2", error_class="auth")]
    out = _daemon(tmp_path, rows)._sync_youtube_analytics()
    assert out.sync_outcome is SyncOutcome.FAILED
    assert out.metrics["sync_blocked_by"] == ["auth"]
    assert "auth_error_reauthorize_token" in out.changes_made
