"""Rule: viewers search for a term the video's title and description never use.

Pure and deterministic: observations in, opportunities out, no I/O. Views that
are unknown (``None``) are never treated as zero and never yield an
opportunity. See ``docs/opportunities.md``.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from seo_manager.collection.contracts import MAX_PROVENANCE_LEN, Observation
from seo_manager.collection.youtube_search_terms import SOURCE
from seo_manager.opportunities.contracts import EvidenceRef, Opportunity, VideoText

RULE = "search_demand_not_in_metadata"
_HASH_PREFIX = "sha256:"
_TOKEN = re.compile(r"\w+")


@dataclass(frozen=True)
class SearchDemandPolicy:
    """Versioned product policy. Change a number, bump ``version``."""

    version: str = "search-demand-1"
    min_views: int = 10
    max_terms_per_video: int = 5
    reevaluate_after: timedelta = timedelta(days=7)


DEFAULT_POLICY = SearchDemandPolicy()


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(unicodedata.normalize("NFKC", text).casefold())


def _norm(text: str) -> str:
    return " ".join(_tokens(text))


def _stable_id(tenant: str, video_id: str, term: str) -> str:
    raw = "\x1f".join([tenant, RULE, video_id, term])
    return "opp_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _day(moment: datetime) -> str:
    return moment.date().isoformat()


@dataclass(frozen=True)
class _Term:
    key: str  # normalized term, or the hashed subject part when unrecoverable
    text: str | None  # None when the raw term cannot be recovered
    views: float
    ref: EvidenceRef


def _recover_term(subject_term: str, provenance: str | None) -> str | None:
    """Plain subject terms as-is; hashed ones only from an intact provenance."""
    if not subject_term.startswith(_HASH_PREFIX):
        return subject_term
    if not provenance or not provenance.startswith("term="):
        return None
    if len(provenance) >= MAX_PROVENANCE_LEN:
        return None  # truncated on write
    term = provenance[len("term=") :]
    digest = hashlib.sha256(term.encode("utf-8")).hexdigest()[:32]
    if subject_term != f"{_HASH_PREFIX}{digest}":
        return None
    return term


def _rank(obs: Observation) -> tuple:
    """The store's total order over revisions: (reported_at, value, provenance)."""
    value = "" if obs.value is None else repr(float(obs.value))
    return (obs.reported_at, value, obs.provenance or "")


def _latest(observations: Iterable[Observation]) -> list[Observation]:
    """Drop superseded revisions of the same key, as the store's projection does."""
    best: dict[tuple, Observation] = {}
    for obs in observations:
        cur = best.get(obs.key)
        if cur is None or _rank(obs) > _rank(cur):
            best[obs.key] = obs
    return list(best.values())


def find_search_demand_opportunities(
    observations: Iterable[Observation],
    video_text: Mapping[str, VideoText | None],
    *,
    tenant: str,
    now: datetime,
    policy: SearchDemandPolicy = DEFAULT_POLICY,
) -> list[Opportunity]:
    """Terms viewers searched that the video's own text does not use.

    Only observations of ``tenant`` and the search-terms source are read; an
    observation of another tenant raises ``ValueError``. Per video only the
    newest window is used. A term needs a known ``views`` value of at least
    ``policy.min_views``; unknown views never produce an opportunity.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    obs_list = []
    for obs in observations:
        if obs.tenant != tenant:
            raise ValueError("observation belongs to a different tenant")
        if obs.source == SOURCE:
            obs_list.append(obs)
    obs_list = _latest(obs_list)

    by_video: dict[str, list[Observation]] = {}
    for obs in obs_list:
        by_video.setdefault(obs.subject.split("|", 1)[0], []).append(obs)

    out: list[Opportunity] = []
    for video_id, rows in by_video.items():
        newest = max(o.window_end for o in rows)
        rows = [o for o in rows if o.window_end == newest]
        out.extend(
            _video(video_id, rows, video_text.get(video_id), tenant, now, policy)
        )
    out.sort(
        key=lambda o: (o.priority is None, -(o.priority or 0.0), o.id),
    )
    return out


def _video(
    video_id: str,
    rows: list[Observation],
    text: VideoText | None,
    tenant: str,
    now: datetime,
    policy: SearchDemandPolicy,
) -> list[Opportunity]:
    completeness = next((o for o in rows if o.metric == "search_term_rows"), None)
    views_obs = sorted(
        (o for o in rows if o.metric == "views" and "|" in o.subject),
        key=lambda o: o.subject,
    )
    if not views_obs:
        return []
    window_start, window_end = views_obs[0].window_start, views_obs[0].window_end
    all_known = all(o.value is not None for o in views_obs)
    total = sum(o.value for o in views_obs if o.value is not None)

    terms: list[_Term] = []
    for o in views_obs:
        if o.value is None or o.value < policy.min_views:
            continue
        sub = o.subject.split("|", 1)[1]
        raw = _recover_term(sub, o.provenance)
        key = _norm(raw) if raw is not None else sub
        terms.append(_Term(key, raw, float(o.value), EvidenceRef.from_observation(o)))
    if not terms:
        return []

    def reasons(t: _Term) -> tuple[str, ...]:
        span = f"{_day(window_start)} to {_day(window_end)}"
        r = [f"{t.views:g} views from search in {span}"]
        if all_known and total > 0:
            r.append(f"{t.views:g} of {total:g} reported search views for this video")
        else:
            r.append("share of this video's search views is unknown")
        if completeness is not None and "at_cap=true" in (
            completeness.provenance or ""
        ):
            n = completeness.value if completeness.value is not None else "n"
            r.append(f"top-{n} referrers only; other terms are unknown")
        return tuple(r)

    comp_ref = (EvidenceRef.from_observation(completeness),) if completeness else ()
    base_missing = () if completeness else ("completeness_unknown",)
    common = {
        "tenant": tenant,
        "rule": RULE,
        "asset": video_id,
        "window_start": window_start,
        "window_end": window_end,
        "effort": "low: one title or description edit after review",
        "risk": "low: wording can drift from the footage if not checked",
        "automation": "manual_review_only",
        "next_evaluation": now + policy.reevaluate_after,
        "policy_version": policy.version,
    }
    validation = (
        "Re-read the next settled window and compare views for this term. "
        "The views are observed referrals from YouTube search, not total "
        "search volume and not a predicted lift."
    )

    def change(term: str) -> str:
        return (
            f"Check whether the title or description should describe what "
            f'viewers searched for ("{term}"), only where the footage '
            f"shows it. Do not add the phrase otherwise."
        )

    ranked = sorted(terms, key=lambda t: (-t.views, t.key))

    if text is None:
        views = ", ".join(f"{t.views:g}" for t in ranked)
        return [
            Opportunity(
                id=_stable_id(tenant, video_id, ""),
                status="needs_evidence",
                observed=(
                    f"{len(ranked)} search terms at or above the floor "
                    f"(views: {views}); the video's current text is unavailable"
                ),
                evidence=tuple(t.ref for t in ranked) + comp_ref,
                proposed_change=(
                    "Fetch the title and description, then check whether they "
                    "describe what viewers searched for, only where the "
                    "footage shows it."
                ),
                priority=ranked[0].views,
                priority_reasons=reasons(ranked[0]),
                validation=validation,
                missing_evidence=("video_text_unavailable", *base_missing),
                **common,
            )
        ]

    have = set(_tokens(f"{text.title} {text.description}"))
    result: list[Opportunity] = []
    for t in ranked:
        if t.text is None:
            result.append(
                Opportunity(
                    id=_stable_id(tenant, video_id, t.key),
                    status="needs_evidence",
                    observed=(
                        "a search term over the length limit was stored "
                        "without its full text"
                    ),
                    evidence=(t.ref, *comp_ref),
                    proposed_change=(
                        "Recover the full search term before deciding whether "
                        "the text should describe it."
                    ),
                    priority=t.views,
                    priority_reasons=reasons(t),
                    validation=validation,
                    missing_evidence=("term_text_truncated", *base_missing),
                    **common,
                )
            )
            continue
        toks = _tokens(t.text)
        if not toks or all(tok in have for tok in toks):
            continue
        result.append(
            Opportunity(
                id=_stable_id(tenant, video_id, t.key),
                status="open",
                observed=(
                    f'viewers reached this video by searching "{t.text}"; '
                    "that wording is not in its title or description"
                ),
                evidence=(t.ref, *comp_ref),
                proposed_change=change(t.text),
                priority=t.views,
                priority_reasons=reasons(t),
                validation=validation,
                missing_evidence=base_missing,
                **common,
            )
        )
    return result[: policy.max_terms_per_video]
