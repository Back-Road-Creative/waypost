"""Rule: the newest offline site audit found a technical defect on a page.

Pure and deterministic: observations in, opportunities out, no I/O. Only the
newest audit of the ``site_audit`` source is read, so a defect that is gone from
it is fixed and produces nothing. A tenant with no audit gets no opportunity.
``priority`` is an ordering key by severity, never an impact estimate. See
``docs/opportunities.md``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from seo_manager.collection.contracts import Observation
from seo_manager.collection.site_audit import SOURCE
from seo_manager.opportunities.contracts import EvidenceRef, Opportunity
from seo_manager.opportunities.youtube_search import _latest
from seo_manager.site_audit.audit import SEVERITY

RULE = "site_technical_defect"
_PAGE = "page:"
_HASH_PREFIX = "sha256:"
_RAW_ROOM = 200  # collection/site_audit.py keeps at most this much of the raw subject
_SCORE = {"error": 2.0, "warning": 1.0}

_FIX = (
    "Fix it in the site's source repository ({where}), then rebuild the site. "
    "Review the page before changing it."
)
_FAMILIES = (
    (
        ("broken_internal_link", "sitemap_url_missing_file"),
        "the page content or the link target",
    ),
    (("noindex",), "the page template or front matter that sets the robots meta tag"),
    (("canonical",), "the page template that writes the canonical link"),
    (("sitemap", "robots_sitemap"), "the sitemap and robots.txt generation"),
    (("schema",), "the template that writes the structured data"),
    (("robots_txt",), "the robots.txt source"),
)


@dataclass(frozen=True)
class SiteTechnicalPolicy:
    """Versioned product policy. Change a value, bump ``version``."""

    version: str = "site-technical-1"
    severities: tuple[str, ...] = ("error", "warning")
    max_per_check: int = 20
    reevaluate_after: timedelta = timedelta(days=7)


DEFAULT_POLICY = SiteTechnicalPolicy()


def _stable_id(tenant: str, check: str, subject: str) -> str:
    raw = "\x1f".join([tenant, RULE, check, subject])
    return "opp_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _proposed_change(check: str) -> str:
    for prefixes, where in _FAMILIES:
        if check.startswith(prefixes):
            return _FIX.format(where=where)
    return _FIX.format(where="the template or content that produces the page")


def _provenance(prov: str | None) -> dict[str, str]:
    """``key=value`` parts; ``subject=`` and ``detail=`` hold free text, so each
    runs to the next known key rather than to the next ``;``."""
    fields: dict[str, str] = {}
    if not prov:
        return fields
    head, _, rest = prov.partition(";subject=")
    detail_in_head = head.partition(";detail=")
    fields["severity"] = detail_in_head[0].removeprefix("severity=")
    if rest:
        raw, _, detail = rest.partition(";detail=")
        fields["subject"] = raw
        if detail:
            fields["detail"] = detail
    elif detail_in_head[1]:
        fields["detail"] = detail_in_head[2]
    return fields


def _page_target(
    obs: Observation, fields: dict[str, str]
) -> tuple[str, str | None, str]:
    """(page, target, note): the page recovered from the subject, never invented."""
    subject = obs.subject
    note = ""
    if subject.startswith(_PAGE + _HASH_PREFIX):
        raw = fields.get("subject")
        if raw is None:
            return subject, None, "page path unavailable (hashed subject)"
        if len(raw) >= _RAW_ROOM:
            note = "page path truncated at 200 characters"
        else:
            digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
            if subject != f"{_PAGE}{_HASH_PREFIX}{digest}":
                return subject, None, "page path unavailable (hashed subject)"
        subject = raw
    page, _, target = subject.removeprefix(_PAGE).partition("|")
    return page, (target or None), note


def find_site_technical_opportunities(
    observations: Iterable[Observation],
    *,
    tenant: str,
    now: datetime,
    policy: SiteTechnicalPolicy = DEFAULT_POLICY,
) -> list[Opportunity]:
    """Error and warning findings of the newest offline audit, one per page group.

    Only observations of ``tenant`` and the ``site_audit`` source are read; an
    observation of another tenant raises ``ValueError``. Info-level checks,
    unknown check names and rows without a readable severity never produce an
    opportunity. An audit that saw no pages produces none.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    obs_list = []
    for obs in observations:
        if obs.tenant != tenant:
            raise ValueError("observation belongs to a different tenant")
        if obs.source == SOURCE:
            obs_list.append(obs)
    rows = _latest(obs_list)
    if not rows:
        return []
    audited_at = max(o.window_start for o in rows)
    rows = [o for o in rows if o.window_start == audited_at]
    totals = {o.metric: o for o in rows if o.subject == "site"}
    pages = totals.get("pages")
    if pages is None or not pages.value:
        return []

    by_check: dict[str, list[Opportunity]] = {}
    for obs in rows:
        if not obs.subject.startswith(_PAGE) or obs.metric not in SEVERITY:
            continue
        fields = _provenance(obs.provenance)
        severity = fields.get("severity", "")
        if severity not in policy.severities or severity not in _SCORE:
            continue
        by_check.setdefault(obs.metric, []).append(
            _opportunity(obs, fields, severity, totals, tenant, now, policy)
        )

    out: list[Opportunity] = []
    for check, found in by_check.items():
        found.sort(key=lambda o: (-(o.priority or 0.0), o.id))
        kept = found[: policy.max_per_check]
        dropped = len(found) - len(kept)
        if dropped:
            total = totals.get(f"findings_{check}")
            shown = f" ({total.value:g})" if total and total.value is not None else ""
            note = (
                f"{dropped} more {check} findings not listed; "
                f"the site-level findings_{check} count{shown} covers all of them"
            )
            kept = [_with_reason(o, note) for o in kept]
        out.extend(kept)
    out.sort(key=lambda o: (-(o.priority or 0.0), o.id))
    return out


def _with_reason(opp: Opportunity, note: str) -> Opportunity:
    return replace(opp, priority_reasons=(*opp.priority_reasons, note))


def _opportunity(
    obs: Observation,
    fields: dict[str, str],
    severity: str,
    totals: dict[str, Observation],
    tenant: str,
    now: datetime,
    policy: SiteTechnicalPolicy,
) -> Opportunity:
    page, target, note = _page_target(obs, fields)
    check = obs.metric
    count = "count unknown" if obs.value is None else f"{obs.value:g} found"
    observed = f"{check} ({severity}) on {page}"
    if target:
        observed += f" targeting {target}"
    observed += f": {count}"
    if detail := fields.get("detail"):
        observed += f"; {detail}"
    if note:
        observed += f" [{note}]"
    observed += f"; audited {obs.window_start.isoformat()}"
    evidence = [EvidenceRef.from_observation(obs)]
    if (site := totals.get(f"findings_{check}")) is not None:
        evidence.append(EvidenceRef.from_observation(site))
    return Opportunity(
        id=_stable_id(tenant, check, obs.subject),
        tenant=tenant,
        rule=RULE,
        status="open",
        asset=page,
        observed=observed,
        evidence=tuple(evidence),
        window_start=obs.window_start,
        window_end=obs.window_end,
        proposed_change=_proposed_change(check),
        priority=_SCORE[severity],
        priority_reasons=(
            f"severity {severity}; ordering key, not an impact estimate",
        ),
        effort="low to medium: one template or content fix, then a rebuild",
        risk="low: a template change can affect every page that uses it",
        automation="manual_review_only",
        validation=(
            "Rebuild the site, run `collect run --site-build-dir` again and "
            "confirm this finding is absent from the next audit."
        ),
        next_evaluation=now + policy.reevaluate_after,
        missing_evidence=(),
        policy_version=policy.version,
    )
