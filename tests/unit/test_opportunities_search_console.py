"""Search Console striking-distance rule: eligibility, aggregation, honesty."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from seo_manager.collection.contracts import MAX_PROVENANCE_LEN, Observation
from seo_manager.opportunities.search_console import (
    DEFAULT_POLICY,
    RULE,
    find_search_console_opportunities,
)
from seo_manager.opportunities.search_console import SOURCE as SC

NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)
D0 = datetime(2026, 10, 1, 7, tzinfo=UTC)
PAGE = "https://x.test/a"
DIMS = "date,query,page"
UNSET = object()


def day(i):
    return D0 + timedelta(days=i)


def rows(
    i,
    *,
    imp=60,
    pos=10.0,
    clicks=3,
    query="lava tubes",
    page=PAGE,
    tag="qp",
    dims=DIMS,
    flags=(),
    stype="web",
    tenant="t",
    source=SC,
    subject=UNSET,
    keys=None,
):
    prov = ";".join([f"type={stype}", "dataState=final", f"dims={dims}", *flags])
    if keys is not None:
        prov += ";keys=" + keys
    if subject is UNSET:
        vals = [query, page] if tag.startswith("qp") else [page, query]
        subject = "|".join([stype, tag, *vals])
    out = []
    for metric, v in (
        ("clicks", clicks),
        ("impressions", imp),
        ("ctr", 0.5),
        ("position", pos),
    ):
        out.append(
            Observation(
                tenant,
                source,
                subject,
                metric,
                day(i),
                day(i) + timedelta(days=1),
                v,
                NOW,
                prov,
            )
        )
    return out


def find(obs, **kw):
    kw.setdefault("tenant", "t")
    kw.setdefault("now", NOW)
    return find_search_console_opportunities(obs, **kw)


def two_days(**kw):
    return rows(0, **kw) + rows(1, **kw)


def test_basic_open_finding_shape():
    (o,) = find(two_days())
    assert o.rule == RULE and o.status == "open" and o.asset == PAGE
    assert o.priority == 120.0 and o.policy_version == "sc-striking-1"
    assert o.automation == "manual_review_only" and o.missing_evidence == ()
    assert "lava tubes" in o.observed and "10.0" in o.observed
    assert "2 of 28 days" in o.observed and "6 clicks" in o.observed
    assert "2026-10-01" in o.observed and "2026-10-02" in o.observed
    assert o.next_evaluation == NOW + DEFAULT_POLICY.reevaluate_after
    assert "verbatim" in o.proposed_change
    assert {e.metric for e in o.evidence} == {"impressions", "position"}
    assert len(o.evidence) == 4


def test_below_min_impressions_yields_nothing():
    assert find(rows(0, imp=99)) == []
    assert len(find(rows(0, imp=100))) == 1


@pytest.mark.parametrize(
    ("pos", "n"), [(7.9, 0), (8.0, 1), (20.0, 1), (20.1, 0), (3.0, 0)]
)
def test_position_band_edges_inclusive(pos, n):
    assert len(find(rows(0, imp=200, pos=pos))) == n


def test_unknown_impressions_never_zero_filled():
    obs = rows(0, imp=60) + rows(1, imp=None)
    assert find(obs) == []  # 60 known < 100, the None day adds nothing
    obs = rows(0, imp=100) + rows(1, imp=None)
    (o,) = find(obs)
    assert o.priority == 100.0 and "1 of 28 days" in o.observed


def test_all_unknown_position_yields_nothing():
    assert find(rows(0, imp=500, pos=None)) == []


def test_unknown_position_day_skipped_in_weighting_unknown_clicks_noted():
    obs = rows(0, imp=100, pos=10.0, clicks=None) + rows(1, imp=100, pos=None)
    (o,) = find(obs)
    assert o.priority == 200.0 and "10.0" in o.observed
    assert "3 clicks (1 of 2 days unknown)" in o.observed


def test_position_is_impression_weighted():
    obs = rows(0, imp=300, pos=9.0) + rows(1, imp=100, pos=17.0)
    (o,) = find(obs)
    assert "average position 11.0" in o.observed


def test_incomplete_rows_excluded_and_counted():
    obs = two_days() + rows(2, imp=900, flags=("incomplete=true",))
    (o,) = find(obs)
    assert o.priority == 120.0
    assert "1 incomplete days excluded" in o.priority_reasons


def test_only_incomplete_rows_yield_nothing():
    assert find(rows(0, imp=500, flags=("incomplete=true",))) == []


def test_other_dimension_sets_ignored():
    assert find(rows(0, imp=500, dims="date,query,page,country", tag="qpc")) == []
    assert find(rows(0, imp=500, dims="query,page")) == []
    assert find(rows(0, imp=500, dims="date,query")) == []


def test_page_then_query_order_accepted():
    (o,) = find(rows(0, imp=500, dims="date,page,query", tag="pq"))
    assert o.asset == PAGE and "lava tubes" in o.observed


def test_other_search_type_ignored():
    assert find(rows(0, imp=500, stype="image")) == []


def test_pipe_in_query_skipped():
    assert find(rows(0, imp=500, query="a|b")) == []


def test_hashed_subject_recovered_when_digest_matches():
    raw = "web|qp|lava tubes|" + PAGE
    digest = hashlib.sha256(raw.encode()).hexdigest()[:32]
    subject = f"web|sha256:{digest}"
    (o,) = find(rows(0, imp=500, subject=subject, keys=raw))
    assert o.asset == PAGE and "lava tubes" in o.observed


def test_hashed_subject_skipped_when_keys_truncated_or_mismatched():
    raw = "web|qp|lava tubes|" + PAGE
    digest = hashlib.sha256(raw.encode()).hexdigest()[:32]
    subject = f"web|sha256:{digest}"
    assert find(rows(0, imp=500, subject=subject, keys=raw[:-3])) == []
    assert find(rows(0, imp=500, subject=subject, keys="web|qp|other|" + PAGE)) == []
    assert find(rows(0, imp=500, subject=subject)) == []
    assert MAX_PROVENANCE_LEN > 0


def test_keys_with_semicolon_recovered():
    q = "a;b"
    raw = f"web|qp|{q}|{PAGE}"
    digest = hashlib.sha256(raw.encode()).hexdigest()[:32]
    (o,) = find(rows(0, imp=500, subject=f"web|sha256:{digest}", keys=raw))
    assert q in o.observed


def test_window_days_uses_only_newest_span():
    old = rows(0, imp=500)
    new = rows(40, imp=100)
    (o,) = find(old + new)
    assert o.priority == 100.0


def test_page_cap_hit_reason():
    (o,) = find(two_days(flags=("page_cap_hit=true",)))
    assert "only top rows were collected; totals are lower bounds" in (
        o.priority_reasons
    )
    (o,) = find(two_days())
    assert not any("lower bounds" in r for r in o.priority_reasons)


def test_max_per_page_keeps_highest_impressions():
    obs = []
    for n in range(7):
        obs += rows(0, imp=100 + n, query=f"q{n}")
    out = find(obs)
    assert len(out) == 5
    assert [o.priority for o in out] == [106.0, 105.0, 104.0, 103.0, 102.0]
    other = find(obs, policy=replace(DEFAULT_POLICY, max_per_page=2))
    assert len(other) == 2


def test_max_per_page_is_per_page():
    obs = []
    for p in ("https://x.test/a", "https://x.test/b"):
        for n in range(6):
            obs += rows(0, imp=100 + n, query=f"q{n}", page=p)
    assert len(find(obs)) == 10


def test_id_stable_across_reruns_and_input_order():
    obs = two_days() + two_days(query="other", imp=70)
    a = find(obs)
    b = find(list(reversed(obs)))
    assert [o.id for o in a] == [o.id for o in b]
    assert a[0].id.startswith("opp_") and len(a[0].id) == 20
    assert [o.priority for o in a] == sorted((o.priority for o in a), reverse=True)


def test_other_tenant_raises():
    with pytest.raises(ValueError, match="different tenant"):
        find(two_days(tenant="other"))


def test_naive_now_raises():
    with pytest.raises(ValueError, match="timezone"):
        find(two_days(), now=datetime(2026, 10, 27))


def test_other_sources_ignored():
    assert find(rows(0, imp=900, source="youtube_reach")) == []
    assert find([]) == []


def test_ctr_never_read():
    obs = [o if o.metric != "ctr" else replace(o, value=None) for o in two_days()]
    assert [x.priority for x in find(obs)] == [x.priority for x in find(two_days())]
