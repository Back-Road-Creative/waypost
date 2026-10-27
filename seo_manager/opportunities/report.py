"""Daily read-only report: top opportunities, source health, missing evidence.

Pure: health rows and opportunities in, a JSON-safe dict (and a text rendering)
out. Each expected source gets exactly one entry. No health row means no run is
recorded, which is "never run or not configured" -- the store cannot tell the
two apart. Health keeps only the latest state, so recovery history is not
recorded, and the report says so. ``priority`` is an ordering key, never a
confidence. See ``docs/opportunities.md``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from seo_manager.collection.contracts import SourceHealth
from seo_manager.opportunities.contracts import Opportunity

NEVER_RAN_NOTE = "no run recorded: never run or not configured"
DEFAULT_LIMIT = 10


def _source_entry(
    source: str, health: SourceHealth | None, now: datetime, expected: bool
) -> dict[str, Any]:
    if health is None:
        state, note = "never_ran", NEVER_RAN_NOTE
    else:
        state = health.last_outcome.value if health.last_outcome else "unknown"
        note = None
    age = health.age_since_success(now) if health else None
    return {
        "source": source,
        "expected": expected,
        "state": state,
        "note": note,
        "last_attempt_at": health and _iso(health.last_attempt_at),
        "last_success_at": health and _iso(health.last_success_at),
        "last_success_age_seconds": None if age is None else int(age.total_seconds()),
        "error_class": health.error_class if health else None,
        "next_eligible": health and _iso(health.next_eligible),
    }


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def build_report(
    health: Iterable[SourceHealth],
    expected_sources: Sequence[str],
    opportunities: Iterable[Opportunity],
    *,
    tenant: str,
    now: datetime,
    limit: int = DEFAULT_LIMIT,
    video_text: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the report. ``opportunities`` keep the order the rule gave them."""
    if limit < 1:
        raise ValueError("limit must be >= 1")
    rows = {h.source: h for h in health}
    sources = [_source_entry(s, rows.get(s), now, True) for s in expected_sources]
    sources += [
        _source_entry(s, rows[s], now, False)
        for s in sorted(rows)
        if s not in expected_sources
    ]
    opps = list(opportunities)
    open_ = [o for o in opps if o.status == "open"]
    needs = [o for o in opps if o.status == "needs_evidence"]
    evaluations = [o.next_evaluation for o in opps]
    return {
        "tenant": tenant,
        "generated_at": now.isoformat(),
        "sources": sources,
        # Health keeps only the latest state: an incident that recovered leaves
        # no trace, so this report cannot list recoveries.
        "recovery_history": "not_recorded",
        "video_text": video_text or {"state": "unknown", "error_class": None},
        "open": [o.as_dict() for o in open_[:limit]],
        "open_total": len(open_),
        "needs_evidence": [o.as_dict() for o in needs],
        "next_evaluation": _iso(min(evaluations)) if evaluations else None,
    }


def _age(seconds: int) -> str:
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def render_text(report: dict[str, Any]) -> str:
    """Human form of ``build_report``'s dict."""
    out = [f"Daily report for {report['tenant']} at {report['generated_at']}", ""]
    out.append("Source health")
    for s in report["sources"]:
        age = s["last_success_age_seconds"]
        line = (
            f"  {s['source']}  {s['state']}  "
            f"success={'never' if age is None else _age(age) + ' ago'}  "
            f"error={s['error_class'] or 'none'}  "
            f"next_eligible={s['next_eligible'] or 'none'}"
        )
        if s["note"]:
            line += f"  ({s['note']})"
        if not s["expected"]:
            line += "  (not an expected source)"
        out.append(line)
    out.append(
        "  Recovery history is not recorded: health keeps only the latest state."
    )
    vt = report["video_text"]
    out.append(f"  video text: {vt['state']}" + _err(vt.get("error_class")))
    out += [
        "",
        f"Open opportunities (top {len(report['open'])} of {report['open_total']})",
    ]
    if not report["open"]:
        out.append("  none")
    for o in report["open"]:
        out.append(f"  {o['id']}  asset={o['asset']}  priority={o['priority']}")
        out.append(f"    {o['observed']}")
        out.append(f"    next: {o['proposed_change']}")
    out += ["", f"Needs evidence ({len(report['needs_evidence'])})"]
    if not report["needs_evidence"]:
        out.append("  none")
    for o in report["needs_evidence"]:
        out.append(
            f"  {o['id']}  asset={o['asset']}  missing: "
            + ", ".join(o["missing_evidence"])
        )
    out += ["", f"Next evaluation: {report['next_evaluation'] or 'none'}"]
    return "\n".join(out) + "\n"


def _err(error_class: str | None) -> str:
    return f"  error={error_class}" if error_class else ""
