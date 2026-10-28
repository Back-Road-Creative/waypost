"""Pure daily report: source health, opportunities, honest gaps."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from seo_manager.analytics.continuous_optimization_daemon import SyncOutcome
from seo_manager.collection.contracts import SourceHealth
from seo_manager.opportunities.contracts import Opportunity
from seo_manager.opportunities.report import build_report, render_text

NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)
SOURCES = ("src_a", "src_b", "src_c")


def opp(i, *, status="open", priority=10.0, missing=(), days=7):
    return Opportunity(
        id=f"opp_{i}",
        tenant="t",
        rule="r",
        status=status,
        asset=f"vid{i}",
        observed="o",
        evidence=(),
        window_start=NOW - timedelta(days=28),
        window_end=NOW,
        proposed_change="c",
        priority=priority,
        priority_reasons=("x",),
        effort="e",
        risk="r",
        automation="manual_review_only",
        validation="v",
        next_evaluation=NOW + timedelta(days=days),
        missing_evidence=tuple(missing),
        policy_version="p1",
    )


def report(health=(), opps=(), **kw):
    return build_report(health, SOURCES, opps, tenant="t", now=NOW, **kw)


def by_source(rep):
    return {s["source"]: s for s in rep["sources"]}


def test_never_ran_and_unconfigured_are_one_state_with_honest_wording():
    rep = report()
    assert [s["source"] for s in rep["sources"]] == list(SOURCES)
    for s in rep["sources"]:
        assert s["state"] == "never_ran"
        assert "never run or not configured" in s["note"]
        assert s["last_success_at"] is None and s["error_class"] is None


def test_partial_error_and_healthy_states():
    health = [
        SourceHealth("t", "src_a", NOW, NOW - timedelta(hours=3), SyncOutcome.FRESH),
        SourceHealth("t", "src_b", NOW, NOW, SyncOutcome.PARTIAL),
        SourceHealth(
            "t",
            "src_c",
            NOW,
            None,
            SyncOutcome.FAILED,
            "quota_exhausted",
            NOW + timedelta(hours=2),
        ),
    ]
    s = by_source(report(health))
    assert s["src_a"]["state"] == "fresh"
    assert s["src_a"]["last_success_age_seconds"] == 3 * 3600
    assert s["src_b"]["state"] == "partial"
    assert s["src_c"]["state"] == "failed"
    assert s["src_c"]["error_class"] == "quota_exhausted"
    assert s["src_c"]["last_success_at"] is None
    assert s["src_c"]["next_eligible"] == (NOW + timedelta(hours=2)).isoformat()


def test_one_entry_per_source_and_unknown_sources_listed():
    health = [
        SourceHealth("t", "src_a", NOW, NOW, SyncOutcome.FRESH),
        SourceHealth("t", "extra", NOW, NOW, SyncOutcome.FRESH),
    ]
    names = [s["source"] for s in report(health)["sources"]]
    assert names == ["src_a", "src_b", "src_c", "extra"]
    assert len(names) == len(set(names))
    assert by_source(report(health))["extra"]["expected"] is False


def test_health_row_without_outcome_is_unknown():
    health = [SourceHealth("t", "src_a")]
    assert by_source(report(health))["src_a"]["state"] == "unknown"


def test_recovery_history_is_declared_not_recorded():
    rep = report()
    assert rep["recovery_history"] == "not_recorded"
    assert "recovery history is not recorded" in render_text(rep).lower()


def test_open_limited_in_given_order_and_needs_evidence_all_listed():
    opps = [opp(i, priority=100 - i) for i in range(5)]
    opps += [
        opp(10, status="needs_evidence", missing=("video_text_unavailable",)),
        opp(11, status="needs_evidence", missing=("completeness_unknown",)),
    ]
    rep = report(opps=opps, limit=3)
    assert [o["id"] for o in rep["open"]] == ["opp_0", "opp_1", "opp_2"]
    assert rep["open_total"] == 5
    assert [o["id"] for o in rep["needs_evidence"]] == ["opp_10", "opp_11"]
    assert rep["needs_evidence"][0]["missing_evidence"] == ["video_text_unavailable"]


def test_next_evaluation_is_earliest_or_none():
    assert report()["next_evaluation"] is None
    rep = report(opps=[opp(1, days=7), opp(2, days=3)])
    assert rep["next_evaluation"] == (NOW + timedelta(days=3)).isoformat()


def test_video_text_state_is_reported():
    rep = report(video_text={"state": "not_configured", "error_class": None})
    assert rep["video_text"]["state"] == "not_configured"
    assert "video text: not_configured" in render_text(rep)


def test_json_safe_and_text_has_no_percent_or_confidence():
    rep = report(
        [SourceHealth("t", "src_a", NOW, NOW, SyncOutcome.FRESH)],
        [opp(1), opp(2, status="needs_evidence", missing=("x",))],
    )
    json.dumps(rep)
    text = render_text(rep)
    assert "%" not in text and "confidence" not in text.lower()
    assert "src_a" in text and "opp_1" in text and "opp_2" in text


def test_limit_applies_per_rule_and_totals_by_rule():
    from dataclasses import replace

    a = [replace(opp(f"a{i}", priority=1000 - i), rule="ra") for i in range(3)]
    b = [replace(opp(f"b{i}", priority=5 - i), rule="rb") for i in range(3)]
    rep = report(opps=a + b, limit=2)
    assert [o["id"] for o in rep["open"]] == ["opp_a0", "opp_a1", "opp_b0", "opp_b1"]
    assert rep["open_total"] == 6
    assert rep["open_total_by_rule"] == {"ra": 3, "rb": 3}
    assert "per rule: ra 3, rb 3" in render_text(rep)


def test_single_rule_output_is_plain_top_limit():
    rep = report(opps=[opp(i) for i in range(4)], limit=3)
    assert len(rep["open"]) == 3 and rep["open_total"] == 4
    assert rep["open_total_by_rule"] == {"r": 4}
    assert "per rule" not in render_text(rep)
