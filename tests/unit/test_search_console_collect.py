"""Search Console adapter: request bodies, paging, windows, flags, store."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from seo_manager.analytics.continuous_optimization_daemon import SyncOutcome
from seo_manager.collection.contracts import Checkpoint
from seo_manager.collection.search_console import SearchConsoleAdapter
from seo_manager.collection.store import CollectionStore
from seo_manager.transport import AuthError, QuotaExhaustedError, TransientError

NOW = datetime(2026, 10, 27, 20, 0, tzinfo=UTC)  # Pacific 2026-10-27
PT = ZoneInfo("America/Los_Angeles")
SITE = "sc-domain:example.com"


def direct(fn):
    return fn()


class FakeSC:
    """Fake ``searchanalytics().query(siteUrl, body).execute()`` chain."""

    def __init__(self, responder):
        self.responder = responder
        self.calls: list[dict] = []

    def searchanalytics(self):
        return self

    def query(self, siteUrl, body):
        assert siteUrl == SITE
        self.calls.append(body)
        outer = self

        class _Req:
            def execute(self_inner):
                r = outer.responder(body)
                if isinstance(r, Exception):
                    raise r
                return r

        return _Req()


def rows(n, *, date_="2026-10-20", prefix="q"):
    return [
        {"keys": [date_, f"{prefix}{i}", f"/p{i}"], "clicks": i, "impressions": 10 * i,
         "ctr": 0.1, "position": 3.5}
        for i in range(n)
    ]  # fmt: skip


def adapter(sc, **kw):
    kw.setdefault("read", direct)
    return SearchConsoleAdapter(sc, SITE, **kw)


def test_request_body_and_window():
    sc = FakeSC(lambda b: {"rows": []})
    out, cp, outcome = adapter(sc).collect("t", None, NOW)
    assert sc.calls == [
        {
            "startDate": "2026-09-27", "endDate": "2026-10-24",
            "dimensions": ["date", "query", "page"], "type": "web",
            "dataState": "final", "rowLimit": 25000, "startRow": 0,
        }
    ]  # fmt: skip
    assert (out, outcome) == ([], SyncOutcome.EMPTY)
    assert cp.cursor == "2026-10-24" and cp.stream == "search_analytics"
    assert cp.source == "search_console"


def test_paging_with_past_the_end_empty_page():
    def responder(b):
        return {"rows": rows(2) if b["startRow"] < 4 else []}

    sc = FakeSC(responder)
    out, cp, outcome = adapter(sc, row_limit=2).collect("t", None, NOW)
    assert [c["startRow"] for c in sc.calls] == [0, 2, 4]
    assert len(out) == 2 * 2 * 4
    assert outcome is SyncOutcome.FRESH and cp.cursor == "2026-10-24"


def test_short_page_stops():
    sc = FakeSC(lambda b: {"rows": rows(1)})
    adapter(sc, row_limit=2).collect("t", None, NOW)
    assert len(sc.calls) == 1


def test_page_cap_is_partial_and_flagged():
    sc = FakeSC(lambda b: {"rows": rows(2)})
    prior = Checkpoint("t", "search_console", "search_analytics", "2026-10-01", NOW)
    out, cp, outcome = adapter(sc, row_limit=2, max_pages=2).collect("t", prior, NOW)
    assert len(sc.calls) == 2 and outcome is SyncOutcome.PARTIAL
    assert cp.cursor > prior.cursor
    assert all("page_cap_hit=true" in o.provenance for o in out)


def test_cap_only_partial_advances_so_rerun_does_not_requery():
    # A cap returns the same truncated rows every time: retrying only spends quota.
    sc = FakeSC(lambda b: {"rows": rows(2)})
    ad = adapter(sc, row_limit=2, max_pages=2)
    _, cp, outcome = ad.collect("t", None, NOW)
    assert outcome is SyncOutcome.PARTIAL and cp is not None
    assert cp.cursor == sc.calls[0]["endDate"]
    sc.calls.clear()
    assert ad.collect("t", cp, NOW) == ((), cp, SyncOutcome.EMPTY)
    assert sc.calls == []


def test_date_dimension_window_is_one_pacific_day():
    sc = FakeSC(lambda b: {"rows": rows(1)})
    out, _, _ = adapter(sc).collect("t", None, NOW)
    o = out[0]
    assert o.window_start == datetime(2026, 10, 20, tzinfo=PT).astimezone(UTC)
    assert o.window_end == datetime(2026, 10, 21, tzinfo=PT).astimezone(UTC)


def test_no_date_window_is_whole_request_window():
    resp = {"rows": [{"keys": ["q", "/p"], "clicks": 1, "impressions": 2,
                      "ctr": 0.5, "position": 1.0}]}  # fmt: skip
    sc = FakeSC(lambda b: resp)
    out, _, _ = adapter(sc, dimensions=("query", "page")).collect("t", None, NOW)
    assert out[0].window_start == datetime(2026, 9, 27, tzinfo=PT).astimezone(UTC)
    assert out[0].window_end == datetime(2026, 10, 25, tzinfo=PT).astimezone(UTC)
    assert out[0].subject == "web|qp|q|/p"
    assert "dims=query,page" in out[0].provenance


def test_dst_day_is_25_hours():
    resp = {"rows": [{"keys": ["2026-11-01", "q"], "clicks": 1}]}
    sc = FakeSC(lambda b: resp)
    now = datetime(2026, 11, 10, 20, tzinfo=UTC)
    out, _, _ = adapter(sc, dimensions=("date", "query")).collect("t", None, now)
    assert out[0].window_end - out[0].window_start == timedelta(hours=25)


def test_missing_metrics_are_none_not_zero():
    sc = FakeSC(lambda b: {"rows": [{"keys": ["2026-10-20", "q", "/p"], "clicks": 0}]})
    out, _, _ = adapter(sc).collect("t", None, NOW)
    got = {o.metric: o.value for o in out}
    assert got == {"clicks": 0, "impressions": None, "ctr": None, "position": None}


def test_subject_format_and_dims_tag_prevent_collision():
    resp = {"rows": [{"keys": ["2026-10-20", "x"], "clicks": 1}]}
    a = adapter(FakeSC(lambda b: resp), dimensions=("date", "query"))
    b = adapter(FakeSC(lambda b: resp), dimensions=("date", "page"))
    sa = a.collect("t", None, NOW)[0][0].subject
    sb = b.collect("t", None, NOW)[0][0].subject
    assert sa == "web|q|x" and sb == "web|p|x"


def test_long_key_becomes_digest_with_raw_in_provenance():
    long_page = "/" + "a" * 400
    resp = {"rows": [{"keys": ["2026-10-20", "q", long_page], "clicks": 1}]}
    out, _, _ = adapter(FakeSC(lambda b: resp)).collect("t", None, NOW)
    o = out[0]
    assert (
        o.subject.startswith("web|sha256:")
        and len(o.subject) == len("web|sha256:") + 32
    )
    assert len(o.provenance) <= 500
    assert "type=web;dataState=final;dims=date,query,page" in o.provenance
    assert ";keys=" in o.provenance and "/aaaa" in o.provenance


def test_all_state_flags_only_rows_from_first_incomplete_date():
    resp = {
        "rows": [
            {"keys": ["2026-10-22", "q", "/p"], "clicks": 1},
            {"keys": ["2026-10-23", "q", "/p"], "clicks": 1},
            {"keys": ["2026-10-24", "q", "/p"], "clicks": 1},
        ],
        "metadata": {"first_incomplete_date": "2026-10-23"},
    }
    sc = FakeSC(lambda b: resp)
    out, _, _ = adapter(sc, data_state="all").collect("t", None, NOW)
    flagged = {
        o.window_start.astimezone(PT).date(): "incomplete=true" in o.provenance
        for o in out
    }
    assert flagged == {
        date(2026, 10, 22): False,
        date(2026, 10, 23): True,
        date(2026, 10, 24): True,
    }
    assert sc.calls[0]["dataState"] == "all"


def test_all_state_uses_first_incomplete_hour_and_final_never_flags():
    resp = {"rows": [{"keys": ["2026-10-24", "q", "/p"], "clicks": 1}],
            "metadata": {"first_incomplete_hour": "2026-10-24T10:00:00-07:00"}}  # fmt: skip
    out, _, _ = adapter(FakeSC(lambda b: resp), data_state="all").collect(
        "t", None, NOW
    )
    assert "incomplete=true" in out[0].provenance
    out, _, _ = adapter(FakeSC(lambda b: resp)).collect("t", None, NOW)
    assert "incomplete" not in out[0].provenance


@pytest.mark.parametrize(
    "kw",
    [
        {"search_types": ("bogus",)},
        {"search_types": ()},
        {"dimensions": ("bogus",)},
        {"dimensions": ()},
        {"dimensions": ("date", "date")},
        {"dimensions": ("searchAppearance", "date")},
        {"search_types": ("discover",), "dimensions": ("query",)},
        {"search_types": ("web", "googleNews"), "dimensions": ("date", "query")},
        {"data_state": "hourly_all"},
        {"row_limit": 0},
        {"row_limit": 25001},
        {"max_pages": 0},
    ],
)
def test_invalid_combinations_refused_without_calls(kw):
    sc = FakeSC(lambda b: pytest.fail("API called"))
    with pytest.raises(ValueError):
        adapter(sc, **kw)
    assert sc.calls == []


def test_valid_special_combinations_accepted():
    adapter(FakeSC(lambda b: {}), dimensions=("searchAppearance",))
    adapter(
        FakeSC(lambda b: {}), search_types=("discover",), dimensions=("date", "page")
    )


def test_checkpoint_at_or_past_target_makes_no_calls():
    sc = FakeSC(lambda b: pytest.fail("API called"))
    cp = Checkpoint("t", "search_console", "search_analytics", "2026-10-24", NOW)
    out, got, outcome = adapter(sc).collect("t", cp, NOW)
    assert (out, got, outcome) == ((), cp, SyncOutcome.EMPTY)


@pytest.mark.parametrize(
    "err", [QuotaExhaustedError("q", next_eligible=NOW), AuthError("a")]
)
def test_quota_and_auth_propagate(err):
    with pytest.raises(type(err)):
        adapter(FakeSC(lambda b: err)).collect("t", None, NOW)


def test_one_type_failing_is_partial_keeps_other_type():
    def responder(b):
        return TransientError("x") if b["type"] == "image" else {"rows": rows(1)}

    sc = FakeSC(responder)
    out, cp, outcome = adapter(sc, search_types=("web", "image")).collect(
        "t", None, NOW
    )
    assert outcome is SyncOutcome.PARTIAL and cp is None
    assert out and all(o.subject.startswith("web|") for o in out)


def test_all_types_failing_raises():
    sc = FakeSC(lambda b: TransientError("x"))
    with pytest.raises(TransientError):
        adapter(sc, search_types=("web", "image")).collect("t", None, NOW)


def test_tenant_stamped_and_isolated(tmp_path):
    sc = FakeSC(lambda b: {"rows": rows(1)})
    a = adapter(sc)
    store = CollectionStore(tmp_path / "c.db")
    store.init()
    for tenant in ("gms", "other"):
        obs, cp, _ = a.collect(tenant, None, NOW)
        assert {o.tenant for o in obs} == {tenant}
        store.write_batch(tenant, obs, cp)
    assert {o.tenant for o in store.latest("gms")} == {"gms"}
    assert len(store.latest("other")) == 4
    assert (
        store.checkpoint("gms", "search_console", "search_analytics").cursor
        == "2026-10-24"
    )


def test_end_to_end_rerun_adds_no_revisions(tmp_path):
    sc = FakeSC(lambda b: {"rows": rows(3)})
    store = CollectionStore(tmp_path / "c.db")
    store.init()
    obs, _, _ = adapter(sc).collect("gms", None, NOW)
    assert store.write_batch("gms", obs) == 12
    obs2, _, _ = adapter(sc).collect("gms", None, NOW)
    assert store.write_batch("gms", obs2) == 0
    assert len(store.revisions("gms", "search_console")) == 12
