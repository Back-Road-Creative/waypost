"""YouTubeSearchTermsAdapter: request shape, window, completeness, errors."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from seo_manager.analytics.continuous_optimization_daemon import SyncOutcome
from seo_manager.collection.contracts import Checkpoint
from seo_manager.collection.store import CollectionStore
from seo_manager.collection.youtube_search_terms import (
    SOURCE,
    YouTubeSearchTermsAdapter,
    collection_window,
)
from seo_manager.transport import (
    AuthError,
    InvalidRequestError,
    QuotaExhaustedError,
    TransientError,
)

NOW = datetime(2026, 10, 27, 18, 0, tzinfo=UTC)


class FakeAnalytics:
    """Fakes ``reports().query(**kw).execute()``; per-video response or error."""

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls: list[dict] = []

    def reports(self):
        return self

    def query(self, **kw):
        self.calls.append(kw)
        video = kw["filters"].split(";")[0].removeprefix("video==")
        result = self.responses.get(video, {"rows": []})
        mock = MagicMock()
        if isinstance(result, Exception):
            mock.execute.side_effect = result
        else:
            mock.execute.return_value = result
        return mock


def _adapter(fake, videos=("v1",), **kw):
    return YouTubeSearchTermsAdapter(fake, videos, read=lambda fn: fn(), **kw)


def test_exact_request_params():
    fake = FakeAnalytics()
    _adapter(fake, max_results=10).collect("t", None, NOW)
    assert fake.calls == [
        {
            "ids": "channel==MINE",
            "startDate": "2026-09-27",
            "endDate": "2026-10-24",
            "metrics": "views,estimatedMinutesWatched",
            "dimensions": "insightTrafficSourceDetail",
            "filters": "video==v1;insightTrafficSourceType==YT_SEARCH",
            "sort": "-views",
            "maxResults": 10,
        }
    ]


def test_window_across_dst_change():
    # 2026-11-01 is the fall-back day in Pacific time.
    now = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
    start, end = collection_window(now, 28, 3)
    assert (start.isoformat(), end.isoformat()) == ("2026-10-05", "2026-11-01")
    fake = FakeAnalytics({"v1": {"rows": [["a", 1, 2]]}})
    obs, _, _ = _adapter(fake).collect("t", None, now)
    # Pacific midnight 10-05 is PDT (UTC-7); end is midnight 11-02 PST (UTC-8).
    assert obs[0].window_start == datetime(2026, 10, 5, 7, tzinfo=UTC)
    assert obs[0].window_end == datetime(2026, 11, 2, 8, tzinfo=UTC)


def test_rows_to_observations_none_for_missing():
    fake = FakeAnalytics({"v1": {"rows": [["bass tabs", 10, 4.5], ["x", 3]]}})
    obs, cp, outcome = _adapter(fake).collect("t", None, NOW)
    got = {(o.subject, o.metric): o.value for o in obs}
    assert got[("v1|bass tabs", "views")] == 10
    assert got[("v1|bass tabs", "estimated_minutes_watched")] == 4.5
    assert got[("v1|x", "views")] == 3
    assert got[("v1|x", "estimated_minutes_watched")] is None
    assert all(o.source == SOURCE and o.tenant == "t" for o in obs)
    assert outcome is SyncOutcome.FRESH
    assert (cp.stream, cp.cursor, cp.tenant) == ("search_terms", "2026-10-24", "t")


def test_long_term_uses_digest_subject():
    term = "q" * 300
    fake = FakeAnalytics({"v1": {"rows": [[term, 1, 1]]}})
    obs, _, _ = _adapter(fake).collect("t", None, NOW)
    first = obs[0]
    assert first.subject.startswith("v1|sha256:") and len(first.subject) <= 200
    assert first.provenance.startswith("term=qqq") and len(first.provenance) <= 500


@pytest.mark.parametrize(("n_rows", "cap"), [(2, "true"), (1, "false")])
def test_completeness_row_records_cap(n_rows, cap):
    rows = [[f"t{i}", 1, 1] for i in range(n_rows)]
    fake = FakeAnalytics({"v1": {"rows": rows}})
    obs, _, _ = _adapter(fake, max_results=2).collect("t", None, NOW)
    last = obs[-1]
    assert (last.subject, last.metric, last.value) == ("v1", "search_term_rows", n_rows)
    assert last.provenance == f"maxResults=2;at_cap={cap}"


def test_no_rows_still_emits_count_only():
    obs, _, outcome = _adapter(FakeAnalytics()).collect("t", None, NOW)
    assert [(o.metric, o.value) for o in obs] == [("search_term_rows", 0)]
    assert outcome is SyncOutcome.FRESH


def test_checkpoint_skips_calls():
    fake = FakeAnalytics()
    cp = Checkpoint("t", SOURCE, "search_terms", "2026-10-24", NOW)
    obs, out_cp, outcome = _adapter(fake).collect("t", cp, NOW)
    assert (obs, out_cp, outcome) == ((), cp, SyncOutcome.EMPTY)
    assert fake.calls == []


def test_older_checkpoint_collects():
    fake = FakeAnalytics()
    cp = Checkpoint("t", SOURCE, "search_terms", "2026-10-23", NOW)
    _adapter(fake).collect("t", cp, NOW)
    assert len(fake.calls) == 1


def test_transient_video_gives_partial():
    fake = FakeAnalytics(
        {
            "v1": TransientError("boom"),
            "v2": {"rows": [["a", 1, 1]]},
            "v3": InvalidRequestError("bad"),
        }
    )
    obs, cp, outcome = _adapter(fake, videos=("v1", "v2", "v3")).collect("t", None, NOW)
    assert outcome is SyncOutcome.PARTIAL
    assert {o.subject.split("|")[0] for o in obs} == {"v2"}
    # No advance: the next run retries the window for the videos that failed.
    assert cp is None


@pytest.mark.parametrize(
    "err",
    [
        QuotaExhaustedError("q", next_eligible=NOW),
        AuthError("a"),
    ],
)
def test_quota_and_auth_propagate_immediately(err):
    fake = FakeAnalytics({"v1": err})
    with pytest.raises(type(err)):
        _adapter(fake, videos=("v1", "v2")).collect("t", None, NOW)
    assert len(fake.calls) == 1


def test_all_transient_raises_last_error():
    fake = FakeAnalytics({"v1": TransientError("one"), "v2": TransientError("two")})
    with pytest.raises(TransientError, match="two"):
        _adapter(fake, videos=("v1", "v2")).collect("t", None, NOW)


def test_empty_video_list_makes_no_calls():
    fake = FakeAnalytics()
    obs, cp, outcome = _adapter(fake, videos=()).collect("t", None, NOW)
    assert (obs, cp, outcome) == ((), None, SyncOutcome.EMPTY)
    assert fake.calls == []


def test_two_tenants_never_mix():
    fake = FakeAnalytics({"v1": {"rows": [["a", 1, 1]]}})
    a_obs, a_cp, _ = _adapter(fake).collect("brand-a", None, NOW)
    b_obs, b_cp, _ = _adapter(fake).collect("brand-b", None, NOW)
    assert {o.tenant for o in a_obs} == {"brand-a"} and a_cp.tenant == "brand-a"
    assert {o.tenant for o in b_obs} == {"brand-b"} and b_cp.tenant == "brand-b"


def test_end_to_end_store_rerun_adds_nothing(tmp_path):
    store = CollectionStore(tmp_path / "c.db")
    store.init()
    fake = FakeAnalytics({"v1": {"rows": [["a", 5, 2], ["b", 1, None]]}})
    adapter = _adapter(fake)
    obs, cp, _ = adapter.collect("t", None, NOW)
    assert store.write_batch("t", obs, cp) == len(obs) > 0
    stored = store.checkpoint("t", SOURCE, "search_terms")
    assert stored.cursor == "2026-10-24"
    obs2, cp2, _ = adapter.collect("t", None, NOW)
    assert store.write_batch("t", obs2, cp2) == 0
    assert any(o.value is None for o in store.latest("t", SOURCE))
