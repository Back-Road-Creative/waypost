"""Typed transport errors and bounded read retry (seo_manager.transport)."""

import json
from datetime import UTC, datetime
from email.utils import format_datetime
from zoneinfo import ZoneInfo

import pytest
from googleapiclient.errors import HttpError

from seo_manager import transport
from seo_manager.transport import (
    AuthError,
    InvalidRequestError,
    QuotaExhaustedError,
    RetryPolicy,
    TransientError,
    UnknownTransportError,
    classify,
    next_quota_reset,
    retry_read,
)

LA = ZoneInfo("America/Los_Angeles")


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


class Clock:
    """Deterministic sleep: records delays, advances nothing real."""

    def __init__(self):
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)


# ── classification: status x reason ────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "reason", "expected"),
    [
        (500, None, TransientError),
        (503, "backendError", TransientError),
        (429, None, TransientError),
        (429, "rateLimitExceeded", TransientError),
        (403, "rateLimitExceeded", TransientError),
        (403, "userRateLimitExceeded", TransientError),
        (500, "backendError", TransientError),
        (403, "quotaExceeded", QuotaExhaustedError),
        (403, "dailyLimitExceeded", QuotaExhaustedError),
        (429, "quotaExceeded", QuotaExhaustedError),
        (503, "quotaExceeded", QuotaExhaustedError),
        (401, None, AuthError),
        (401, "authError", AuthError),
        (403, "forbidden", AuthError),
        (403, "insufficientPermissions", AuthError),
        (400, "invalidCredentials", AuthError),
        (400, "badRequest", InvalidRequestError),
        (404, "videoNotFound", InvalidRequestError),
        (404, None, InvalidRequestError),
        (403, "somethingNew", UnknownTransportError),
        (418, None, UnknownTransportError),
    ],
)
def test_status_and_reason_classification(status, reason, expected):
    err = classify(http_error(status, reason))
    assert type(err) is expected
    assert err.status == status
    assert err.reason == reason


def test_unparseable_body_still_classifies_by_status():
    exc = HttpError(_Resp(503), b"<html>nope</html>")
    err = classify(exc)
    assert isinstance(err, TransientError)
    assert err.reason is None


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (TimeoutError("t"), TransientError),
        (ConnectionResetError("r"), TransientError),
        (BrokenPipeError("b"), TransientError),
        (KeyError("bug"), type(None)),
        (ValueError("bug"), type(None)),
    ],
)
def test_non_http_faults(exc, expected):
    assert type(classify(exc)) is expected


def test_classified_error_keeps_cause():
    original = http_error(503)
    assert classify(original).__cause__ is original


# ── quota reset: next midnight Pacific ─────────────────────────────────


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        # summer, 08:30 PDT -> next midnight PDT (UTC-7)
        (
            datetime(2026, 7, 10, 15, 30, tzinfo=UTC),
            datetime(2026, 7, 11, 7, tzinfo=UTC),
        ),
        # 00:00:01 PDT -> a full day out
        (
            datetime(2026, 7, 11, 7, 0, 1, tzinfo=UTC),
            datetime(2026, 7, 12, 7, tzinfo=UTC),
        ),
        # spring forward 2026-03-08 (23-hour day): midnight before is PST ...
        (datetime(2026, 3, 8, 3, tzinfo=UTC), datetime(2026, 3, 8, 8, tzinfo=UTC)),
        # ... and the midnight after it is PDT
        (datetime(2026, 3, 8, 18, tzinfo=UTC), datetime(2026, 3, 9, 7, tzinfo=UTC)),
        # fall back 2026-11-01 (25-hour day): 22:00 PDT the night before ...
        (datetime(2026, 11, 1, 5, tzinfo=UTC), datetime(2026, 11, 1, 7, tzinfo=UTC)),
        # ... and noon PST that day -> midnight PST
        (datetime(2026, 11, 1, 20, tzinfo=UTC), datetime(2026, 11, 2, 8, tzinfo=UTC)),
    ],
)
def test_quota_reset_is_next_pacific_midnight(now, expected):
    reset = next_quota_reset(now)
    assert reset.astimezone(UTC) == expected


def test_quota_reset_rejects_naive_datetime():
    with pytest.raises(ValueError, match="aware"):
        next_quota_reset(datetime(2026, 7, 10, 1, 0))


def test_quota_error_carries_next_eligible():
    now = datetime(2026, 7, 10, 15, 30, tzinfo=UTC)
    err = classify(http_error(403, "quotaExceeded"), now=now)
    assert err.next_eligible == datetime(2026, 7, 11, 0, 0, tzinfo=LA)


# ── retry_read ─────────────────────────────────────────────────────────


def _flaky(failures, result="ok"):
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] <= len(failures):
            raise failures[calls["n"] - 1]
        return result

    return fn, calls


def test_transient_then_success():
    clock = Clock()
    fn, calls = _flaky([http_error(503), http_error(500)])
    out = retry_read(fn, sleep=clock.sleep, rand=lambda: 1.0)
    assert out == "ok"
    assert calls["n"] == 3
    assert len(clock.sleeps) == 2


def test_backoff_is_exponential_with_full_jitter():
    clock = Clock()
    fn, _ = _flaky([http_error(503)] * 3)
    policy = RetryPolicy(max_attempts=4, base_delay=1.0, max_delay=100.0)
    retry_read(fn, policy=policy, sleep=clock.sleep, rand=lambda: 1.0)
    assert clock.sleeps == [1.0, 2.0, 4.0]
    for jitter, scale in ((0.5, 0.5), (0.0, 0.0)):
        clock2 = Clock()
        fn2, _ = _flaky([http_error(503)] * 3)
        retry_read(fn2, policy=policy, sleep=clock2.sleep, rand=lambda j=jitter: j)
        assert clock2.sleeps == [scale * d for d in (1.0, 2.0, 4.0)]


def test_max_attempts_bound_raises_last_transient():
    clock = Clock()
    fn, calls = _flaky([http_error(503)] * 10)
    with pytest.raises(TransientError):
        retry_read(
            fn,
            policy=RetryPolicy(max_attempts=3, base_delay=0.1),
            sleep=clock.sleep,
            rand=lambda: 1.0,
        )
    assert calls["n"] == 3
    assert len(clock.sleeps) == 2


def test_total_delay_bound_stops_retrying():
    clock = Clock()
    fn, calls = _flaky([http_error(503)] * 10)
    policy = RetryPolicy(
        max_attempts=10, base_delay=4.0, max_delay=100.0, max_total_delay=10.0
    )
    with pytest.raises(TransientError):
        retry_read(fn, policy=policy, sleep=clock.sleep, rand=lambda: 1.0)
    # 4 + 8 would be 12 > 10: only the first sleep happens.
    assert clock.sleeps == [4.0]
    assert calls["n"] == 2
    assert sum(clock.sleeps) <= policy.max_total_delay


def test_retry_after_seconds_is_honoured():
    clock = Clock()
    fn, _ = _flaky([http_error(429, headers={"retry-after": "7"})])
    retry_read(fn, sleep=clock.sleep, rand=lambda: 0.0)
    assert clock.sleeps == [7.0]


def test_retry_after_is_capped_by_max_delay():
    clock = Clock()
    fn, _ = _flaky([http_error(429, headers={"retry-after": "3600"})])
    policy = RetryPolicy(max_delay=30.0, max_total_delay=60.0)
    retry_read(fn, policy=policy, sleep=clock.sleep, rand=lambda: 0.0)
    assert clock.sleeps == [30.0]


def test_retry_after_http_date_is_honoured():
    now = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)
    when = datetime(2026, 7, 10, 12, 0, 12, tzinfo=UTC)
    clock = Clock()
    fn, _ = _flaky(
        [http_error(503, headers={"retry-after": format_datetime(when, usegmt=True)})]
    )
    retry_read(fn, sleep=clock.sleep, rand=lambda: 0.0, now=lambda: now)
    assert clock.sleeps == [12.0]


@pytest.mark.parametrize("value", ["Thu, 01 Jan 1970 00:00:00 GMT", "soon", "-5"])
def test_retry_after_past_or_garbage_falls_back_to_backoff(value):
    clock = Clock()
    fn, _ = _flaky([http_error(503, headers={"retry-after": value})])
    now = datetime(2026, 7, 10, 12, tzinfo=UTC)
    policy = RetryPolicy(base_delay=2.0)
    retry_read(fn, policy=policy, sleep=clock.sleep, rand=lambda: 1.0, now=lambda: now)
    assert clock.sleeps == [2.0]


@pytest.mark.parametrize(
    ("exc", "cls"),
    [
        (http_error(403, "quotaExceeded"), QuotaExhaustedError),
        (http_error(401), AuthError),
        (http_error(403, "forbidden"), AuthError),
        (http_error(404), InvalidRequestError),
        (http_error(400, "badRequest"), InvalidRequestError),
        (http_error(418), UnknownTransportError),
    ],
)
def test_terminal_errors_are_never_retried(exc, cls):
    clock = Clock()
    fn, calls = _flaky([exc] * 5)
    with pytest.raises(cls):
        retry_read(fn, sleep=clock.sleep)
    assert calls["n"] == 1
    assert clock.sleeps == []


def test_unclassified_exception_propagates_unchanged_and_unretried():
    clock = Clock()
    fn, calls = _flaky([KeyError("bug")])
    with pytest.raises(KeyError):
        retry_read(fn, sleep=clock.sleep)
    assert calls["n"] == 1


def test_network_fault_is_retried_then_succeeds():
    clock = Clock()
    fn, calls = _flaky([TimeoutError("t")])
    assert retry_read(fn, sleep=clock.sleep, rand=lambda: 1.0) == "ok"
    assert calls["n"] == 2


def test_policy_defaults_bounded_and_rejects_nonsense():
    p = RetryPolicy()
    assert p.max_attempts <= 6 and p.max_total_delay <= 120
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=0)
    with pytest.raises(ValueError):
        RetryPolicy(max_total_delay=-1)
