"""Rule: a page ranks just off the first page for a query people already use.

Pure and deterministic: observations in, opportunities out, no I/O. Unknown
values (``None``) are never treated as zero. Search Console returns top rows
only, so an absent row is not zero demand. ``ctr`` is never read. See
``docs/opportunities.md``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from seo_manager.collection.contracts import Observation
from seo_manager.collection.search_console import SOURCE
from seo_manager.opportunities.contracts import EvidenceRef, Opportunity
from seo_manager.opportunities.youtube_search import _latest

RULE = "search_console_striking_distance"
_HASH_PREFIX = "sha256:"
_KEYS = ";keys="


@dataclass(frozen=True)
class SearchConsolePolicy:
    """Versioned product policy. Change a number, bump ``version``."""

    version: str = "sc-striking-1"
    search_type: str = "web"
    min_impressions: int = 100
    min_position: float = 8.0
    max_position: float = 20.0
    window_days: int = 28
    max_per_page: int = 5
    reevaluate_after: timedelta = timedelta(days=28)


DEFAULT_POLICY = SearchConsolePolicy()


def _stable_id(tenant: str, search_type: str, query: str, page: str) -> str:
    raw = "\x1f".join([tenant, RULE, search_type, query, page])
    return "opp_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _parse_provenance(prov: str | None) -> tuple[dict[str, str], set[str], str | None]:
    """``key=value`` parts, bare flags, and the raw ``keys=`` tail (if any)."""
    if not prov:
        return {}, set(), None
    head, sep, tail = prov.partition(_KEYS)
    fields: dict[str, str] = {}
    flags: set[str] = set()
    for part in head.split(";"):
        key, eq, val = part.partition("=")
        if eq:
            fields[key] = val
        if part:
            flags.add(part)
    return fields, flags, (tail if sep else None)


def _query_page(obs: Observation, tail: str | None, tag: str) -> tuple[str, str] | None:
    """The (query, page) of a row, or ``None`` when it cannot be read safely."""
    parts = obs.subject.split("|")
    if len(parts) == 2 and parts[1].startswith(_HASH_PREFIX):
        if tail is None:
            return None
        digest = hashlib.sha256(tail.encode("utf-8")).hexdigest()[:32]
        if parts[1] != f"{_HASH_PREFIX}{digest}":
            return None
        parts = tail.split("|")
    if len(parts) != 4 or parts[1] != tag:
        return None
    return (parts[2], parts[3]) if tag == "qp" else (parts[3], parts[2])


@dataclass(frozen=True)
class _Row:
    query: str
    page: str
    day: datetime  # window_end of the one-day row
    metrics: dict[str, Observation]
    incomplete: bool
    capped: bool


def _rows(observations: list[Observation], policy: SearchConsolePolicy) -> list[_Row]:
    cells: dict[tuple, dict[str, Observation]] = {}
    meta: dict[tuple, tuple[bool, bool]] = {}
    for obs in observations:
        fields, flags, tail = _parse_provenance(obs.provenance)
        if fields.get("type") != policy.search_type:
            continue
        dims = fields.get("dims", "").split(",")
        if "date" not in dims or len(dims) != 3:
            continue
        rest = {d for d in dims if d != "date"}
        if rest != {"query", "page"}:
            continue
        tag = "qp" if dims.index("query") < dims.index("page") else "pq"
        qp = _query_page(obs, tail, tag)
        if qp is None:
            continue
        key = (qp[0], qp[1], obs.window_end, tag)
        cells.setdefault(key, {})[obs.metric] = obs
        meta[key] = ("incomplete=true" in flags, "page_cap_hit=true" in flags)
    # One row per (query, page, day): if two tags describe it, the first wins.
    chosen: dict[tuple, tuple] = {}
    for key in sorted(cells):
        chosen.setdefault(key[:3], key)
    return [
        _Row(k[0], k[1], k[2], cells[full], *meta[full]) for k, full in chosen.items()
    ]


def find_search_console_opportunities(
    observations: Iterable[Observation],
    *,
    tenant: str,
    now: datetime,
    policy: SearchConsolePolicy = DEFAULT_POLICY,
) -> list[Opportunity]:
    """Queries a page already ranks for, a little off the first page.

    Only observations of ``tenant`` and the Search Console source are read; an
    observation of another tenant raises ``ValueError``. Only settled
    per-day ``query`` x ``page`` rows are used, never mixed with other
    dimension sets. Unknown impressions or positions are never zero-filled.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    obs_list = []
    for obs in observations:
        if obs.tenant != tenant:
            raise ValueError("observation belongs to a different tenant")
        if obs.source == SOURCE:
            obs_list.append(obs)
    rows = _rows(_latest(obs_list), policy)
    settled = [r for r in rows if not r.incomplete]
    if not settled:
        return []
    cutoff = max(r.day for r in settled) - timedelta(days=policy.window_days)

    pairs: dict[tuple[str, str], list[_Row]] = {}
    for r in rows:
        if r.day > cutoff:
            pairs.setdefault((r.query, r.page), []).append(r)

    found: list[Opportunity] = []
    for (query, page), group in pairs.items():
        opp = _pair(query, page, group, tenant, now, policy)
        if opp is not None:
            found.append(opp)

    by_page: dict[str, list[Opportunity]] = {}
    for opp in sorted(found, key=lambda o: (-(o.priority or 0.0), o.id)):
        by_page.setdefault(opp.asset, []).append(opp)
    out = [o for group in by_page.values() for o in group[: policy.max_per_page]]
    out.sort(key=lambda o: (-(o.priority or 0.0), o.id))
    return out


def _pair(
    query: str,
    page: str,
    group: list[_Row],
    tenant: str,
    now: datetime,
    policy: SearchConsolePolicy,
) -> Opportunity | None:
    incomplete_days = sum(1 for r in group if r.incomplete)
    days = sorted((r for r in group if not r.incomplete), key=lambda r: r.day)
    used = [
        r
        for r in days
        if (i := r.metrics.get("impressions")) is not None and i.value is not None
    ]
    if not used:
        return None
    impressions = sum(r.metrics["impressions"].value for r in used)
    if impressions < policy.min_impressions:
        return None
    weighted = [
        r
        for r in used
        if r.metrics["impressions"].value > 0
        and (p := r.metrics.get("position")) is not None
        and p.value is not None
    ]
    weight = sum(r.metrics["impressions"].value for r in weighted)
    if weight <= 0:
        return None
    position = (
        sum(
            r.metrics["impressions"].value * r.metrics["position"].value
            for r in weighted
        )
        / weight
    )
    if not policy.min_position <= position <= policy.max_position:
        return None

    clicks_known = [
        r.metrics["clicks"].value
        for r in used
        if r.metrics.get("clicks") is not None and r.metrics["clicks"].value is not None
    ]
    clicks_unknown = len(used) - len(clicks_known)
    start = min(r.metrics["impressions"].window_start for r in used)
    end = max(r.day for r in used)
    first, last = (
        start.date().isoformat(),
        max(r.metrics["impressions"].window_start for r in used).date().isoformat(),
    )
    clicks_text = f"{sum(clicks_known):g} clicks"
    if clicks_unknown:
        clicks_text += f" ({clicks_unknown} of {len(used)} days unknown)"

    evidence = [EvidenceRef.from_observation(r.metrics["impressions"]) for r in used]
    evidence += [EvidenceRef.from_observation(r.metrics["position"]) for r in weighted]
    reasons = [
        f"{impressions:g} impressions from {first} to {last}",
        f"{len(used)} of {policy.window_days} days reported",
    ]
    if any(
        "page_cap_hit=true" in _parse_provenance(r.metrics["impressions"].provenance)[1]
        for r in used
    ):
        reasons.append("only top rows were collected; totals are lower bounds")
    if incomplete_days:
        reasons.append(f"{incomplete_days} incomplete days excluded")

    return Opportunity(
        id=_stable_id(tenant, policy.search_type, query, page),
        tenant=tenant,
        rule=RULE,
        status="open",
        asset=page,
        observed=(
            f'query "{query}" on {page}: {impressions:g} impressions over '
            f"{len(used)} of {policy.window_days} days ({first} to {last}), "
            f"average position {position:.1f}, {clicks_text}"
        ),
        evidence=tuple(evidence),
        window_start=start,
        window_end=end,
        proposed_change=(
            "Review whether the page answers this query directly in its title, "
            "heading and opening text. Change wording only where the page "
            "content already supports it; do not insert the query verbatim."
        ),
        priority=float(impressions),
        priority_reasons=tuple(reasons),
        effort="low: one title, heading or intro edit after review",
        risk="low: wording can drift from the page content if not checked",
        automation="manual_review_only",
        validation=(
            "Re-read the next settled window and compare average position "
            "and impressions for this query and page. Positions are observed "
            "averages, not a predicted lift."
        ),
        next_evaluation=now + policy.reevaluate_after,
        missing_evidence=(),
        policy_version=policy.version,
    )
