"""Golden positive/negative cases for the search-demand opportunity rule."""

from __future__ import annotations

import hashlib
import json
import random
import re
from datetime import UTC, datetime, timedelta

import pytest

from seo_manager.collection.contracts import Observation
from seo_manager.collection.youtube_search_terms import SOURCE
from seo_manager.opportunities import (
    DEFAULT_POLICY,
    SearchDemandPolicy,
    VideoText,
    find_search_demand_opportunities,
)

T = "brand-a"
NOW = datetime(2026, 11, 1, 12, tzinfo=UTC)
W1 = (datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 10, 29, tzinfo=UTC))
W2 = (datetime(2026, 10, 8, tzinfo=UTC), datetime(2026, 11, 5, tzinfo=UTC))
V = "vid1"
TEXT = {V: VideoText("Sunrise hike", "A quiet trail walk.")}


def term_obs(video, term, views, window=W1, prov=None, tenant=T, metric="views"):
    return Observation(
        tenant, SOURCE, f"{video}|{term}", metric, window[0], window[1], views,
        window[1], prov,
    )  # fmt: skip


def rows_obs(video, n=25, at_cap=False, window=W1):
    return Observation(
        T, SOURCE, video, "search_term_rows", window[0], window[1], n, window[1],
        f"maxResults=25;at_cap={str(at_cap).lower()}",
    )  # fmt: skip


def run(obs, text=TEXT, policy=DEFAULT_POLICY):
    return find_search_demand_opportunities(obs, text, tenant=T, now=NOW, policy=policy)


def test_term_absent_from_metadata_is_open():
    (opp,) = run([term_obs(V, "waterfall camping", 40), rows_obs(V)])
    assert opp.status == "open"
    assert opp.asset == V
    assert opp.priority == 40.0
    assert opp.automation == "manual_review_only"
    assert opp.policy_version == "search-demand-1"
    assert opp.next_evaluation == NOW + timedelta(days=7)
    assert "waterfall camping" in opp.proposed_change
    assert opp.missing_evidence == ()
    assert opp.evidence[0].subject == f"{V}|waterfall camping"
    assert (opp.window_start, opp.window_end) == W1


@pytest.mark.parametrize(
    "term", ["sunrise HIKE", "Sunrise, hike!", "quiet trail", "SUNRISE   hike"]
)
def test_covered_term_variants_yield_nothing(term):
    assert run([term_obs(V, term, 50), rows_obs(V)]) == []


def test_accents_and_compat_forms_are_covered():
    text = {V: VideoText("Café sunrise", "")}
    assert run([term_obs(V, "café SUNRISE", 50), rows_obs(V)], text) == []
    assert (
        run(
            [
                term_obs(V, "\uff53\uff55\uff4e\uff52\uff49\uff53\uff45", 50),
                rows_obs(V),
            ],
            TEXT,
        )
        == []
    )


def test_partial_token_overlap_is_not_covered():
    assert len(run([term_obs(V, "sunrise lake", 50), rows_obs(V)])) == 1


def test_views_below_floor_and_unknown_views_yield_nothing():
    obs = [term_obs(V, "tiny term", 9), term_obs(V, "unknown term", None), rows_obs(V)]
    assert run(obs) == []
    assert len(run([term_obs(V, "edge term", 10)])) == 1


def test_unknown_views_make_share_unknown_not_zero():
    obs = [term_obs(V, "big term", 40), term_obs(V, "other", None)]
    (opp,) = run(obs)
    assert any("unknown" in r and "share" in r for r in opp.priority_reasons)


def test_share_of_known_search_views():
    obs = [term_obs(V, "big term", 40), term_obs(V, "small", 5)]
    (opp,) = run(obs)
    assert "40 of 45 reported search views" in " ".join(opp.priority_reasons)
    assert "2026-10-01 to 2026-10-29" in opp.priority_reasons[0]


def test_hashed_recoverable_term():
    term = "x" * 150 + " glacier"
    sub_term = term + "y" * 60
    digest = hashlib.sha256(sub_term.encode()).hexdigest()[:32]
    o = term_obs(V, f"sha256:{digest}", 30, prov=f"term={sub_term}")
    (opp,) = run([o, rows_obs(V)])
    assert opp.status == "open"
    assert sub_term in opp.proposed_change


def test_truncated_hashed_term_needs_evidence():
    big = "z" * 600
    digest = hashlib.sha256(big.encode()).hexdigest()[:32]
    o = term_obs(V, f"sha256:{digest}", 30, prov=f"term={big}"[:500])
    (opp,) = run([o, rows_obs(V)])
    assert opp.status == "needs_evidence"
    assert opp.missing_evidence == ("term_text_truncated",)


def test_missing_text_gives_one_needs_evidence_per_video():
    obs = [
        term_obs(V, "a term", 40), term_obs(V, "b term", 30), rows_obs(V),
        term_obs("vid2", "c term", 20), rows_obs("vid2"),
    ]  # fmt: skip
    for text in ({}, {V: None, "vid2": None}):
        res = run(obs, text)
        assert [o.asset for o in res] == [V, "vid2"]
        assert all(o.status == "needs_evidence" for o in res)
        assert res[0].missing_evidence == ("video_text_unavailable",)
        assert len([e for e in res[0].evidence if e.metric == "views"]) == 2


def test_cross_tenant_observation_raises():
    with pytest.raises(ValueError):
        run([term_obs(V, "a term", 40, tenant="other")])


def test_other_sources_ignored():
    o = Observation(T, "other_src", f"{V}|a term", "views", *W1, 99, W1[1])
    assert run([o]) == []


def test_older_window_ignored():
    obs = [
        term_obs(V, "old term", 99, window=W1),
        term_obs(V, "new term", 20, window=W2),
        rows_obs(V, window=W2),
    ]
    (opp,) = run(obs)
    assert "new term" in opp.proposed_change
    assert opp.window_end == W2[1]


def test_at_cap_reason_and_missing_rows_observation():
    (capped,) = run([term_obs(V, "a term", 40), rows_obs(V, n=25, at_cap=True)])
    assert "top-25 referrers only; other terms are unknown" in capped.priority_reasons
    (nrows,) = run([term_obs(V, "a term", 40)])
    assert nrows.missing_evidence == ("completeness_unknown",)
    assert nrows.status == "open"
    (ok,) = run([term_obs(V, "a term", 40), rows_obs(V)])
    assert not any("top-" in r for r in ok.priority_reasons)


def test_max_terms_cap_keeps_highest_views():
    obs = [term_obs(V, f"term{i} words", 10 + i) for i in range(8)] + [rows_obs(V)]
    res = run(obs)
    assert [o.priority for o in res] == [17.0, 16.0, 15.0, 14.0, 13.0]
    res2 = run(obs, policy=SearchDemandPolicy(max_terms_per_video=2))
    assert len(res2) == 2


def test_deterministic_under_shuffle_and_sorted():
    obs = [term_obs(V, f"term{i} words", 10 + i) for i in range(6)]
    obs += [term_obs("vid2", "other thing", 12), rows_obs(V), rows_obs("vid2")]
    text = {**TEXT, "vid2": VideoText("t", "d")}
    base = run(obs, text)
    for seed in range(5):
        shuffled = obs[:]
        random.Random(seed).shuffle(shuffled)
        assert run(shuffled, text) == base
    keys = [(-o.priority, o.id) for o in base]
    assert keys == sorted(keys)


def test_same_reported_at_revisions_resolve_like_the_store():
    # Two revisions of one key reported at the same moment: the store keeps the
    # greater (reported_at, value, provenance); arrival order must not matter.
    low, high = (
        term_obs(V, "waterfall camping", 20),
        term_obs(V, "waterfall camping", 40),
    )
    for order in ([low, high], [high, low]):
        (opp,) = run([*order, rows_obs(V)])
        assert opp.priority == 40.0


def test_stable_id_across_windows_and_unique_per_term():
    a = run([term_obs(V, "Waterfall  Camping", 40, window=W1)])
    b = run([term_obs(V, "waterfall camping", 55, window=W2)])
    assert a[0].id == b[0].id
    assert re.fullmatch(r"opp_[0-9a-f]{16}", a[0].id)
    c = run([term_obs(V, "other place", 40)])
    assert c[0].id != a[0].id


def test_as_dict_is_json_safe():
    (opp,) = run([term_obs(V, "a term", 40), rows_obs(V)])
    d = json.loads(json.dumps(opp.as_dict()))
    assert d["next_evaluation"] == (NOW + timedelta(days=7)).isoformat()
    assert d["evidence"][0]["metric"] == "views"


BANNED = re.compile(
    r"%|will increase|will grow|boost|uplift|market volume|guarantee|rank higher",
    re.I,
)


def test_no_banned_wording_in_any_text_field():
    obs = [
        term_obs(V, "a term", 40),
        term_obs(V, "b term", None),
        rows_obs(V, at_cap=True),
    ]
    res = run(obs) + run(obs, {}) + run([term_obs(V, "q", 12)])
    assert res
    for opp in res:
        fields = [
            opp.observed, opp.proposed_change, opp.effort, opp.risk,
            opp.validation, *opp.priority_reasons, *opp.missing_evidence,
        ]  # fmt: skip
        for f in fields:
            assert not BANNED.search(f), f
        assert "not total search volume" in opp.validation
