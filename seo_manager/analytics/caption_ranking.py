"""Caption ranking: Meta performance re-orders a caption opener rotation.

``rank_captions(platform, rotation, evidence, policy)`` takes the order a caption
generator would walk its opener templates in and returns that order re-ranked
from Meta evidence, with a record naming each opener's score, sample size, tier
and why it was or was not counted, plus the policy version.

It re-orders and never replaces. A Meta caption must still open differently from
every recent post (the generator's anti-spam gate), so "always use the winner" is
not available: openers whose posts reach clearly more people than the pool's
middle move to the front, openers that reach clearly fewer move to the back, and
every other opener (including all unmeasured ones, which still need data) keeps
its place. Inside each tier the generator's own order holds. The result is always
a permutation of ``rotation``.

Evidence is the median day-7 value of an opener's settled posts on one platform
(``CaptionObservation``): Instagram reach, Facebook initial plays. Each value is
tagged with the metric it measures, and only the metric the policy assigns the
platform counts.

Pure module: no clock (``as_of`` comes from the evidence), no randomness, no I/O,
no LLM. Same inputs, in any observation order, give the same record.
"""

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from statistics import median
from typing import Any

from .evidence import parse_ts

PLATFORMS = ("facebook", "instagram")

# Metric provenance. A value with any other tag is never caption evidence.
# Facebook is judged on day-7 initial plays (play starts): Graph answers no
# per-Reel reach for the live Page, so reach cannot be the metric yet.
FACEBOOK_REEL_INITIAL_PLAYS_DAY7 = "facebook_reel_initial_plays_day7"
FACEBOOK_REEL_REACH_DAY7 = "facebook_reel_reach_day7"
INSTAGRAM_REEL_REACH_DAY7 = "instagram_reel_reach_day7"

# Exclusion vocabulary, checked in this order.
NO_EVIDENCE = "no_evidence"
METRIC_NOT_CAPTION_EVIDENCE = "metric_not_caption_evidence"
STALE = "stale"
N_BELOW_MIN = "n_below_min"

# Tiers and their notes.
LEAD = "lead"
MIDDLE = "middle"
LAG = "lag"
ABOVE_REFERENCE = "above_reference"
BELOW_REFERENCE = "below_reference"

# Ranking reasons.
INSUFFICIENT_EVIDENCE = "insufficient_evidence"
NO_SPREAD = "no_spread"
RANKED = "ranked"


class CaptionRankingRefused(ValueError):
    """The inputs are not ones this function can rank; ``str(exc)`` says why."""


@dataclass(frozen=True)
class CaptionObservation:
    """How one opener's posts did on one platform.

    ``median`` is the median of a per-post metric (day-7 reach) over ``n`` settled
    posts whose day-7 windows fall in ``[window_start, window_end]``.
    """

    platform: str
    template_id: str
    metric_source: str
    median: float
    n: int
    window_start: str
    window_end: str


@dataclass(frozen=True)
class CaptionEvidence:
    as_of: str
    observations: tuple[CaptionObservation, ...] = ()


@dataclass(frozen=True)
class CaptionPolicy:
    """Policy revisions live in code: a changed number is a new ``version``.

    The defaults are proposed values, not measured ones. ``min_n`` is low next to
    a title's because a Meta post is one observation, not one impression; the
    ``min_relative_lift`` margin around the pool median is what keeps noise out.
    """

    version: str = "caption-policy-1"
    metric_by_platform: Mapping[str, str] = field(
        default_factory=lambda: {
            "facebook": FACEBOOK_REEL_INITIAL_PLAYS_DAY7,
            "instagram": INSTAGRAM_REEL_REACH_DAY7,
        }
    )
    min_n: int = 10
    max_evidence_age_days: int = 120
    min_relative_lift: float = 0.10
    min_measured: int = 2


DEFAULT_POLICY = CaptionPolicy()


@dataclass(frozen=True)
class CaptionEntry:
    template_id: str
    rotation_index: int
    tier: str
    score: float | None = None
    n: int | None = None
    metric_source: str | None = None
    evidence_window: tuple[str, str] | None = None
    excluded_reason: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class CaptionRanking:
    policy_version: str
    platform: str
    metric_source: str
    evidence_as_of: str
    reason: str
    reference: float | None
    order: tuple[str, ...]
    entries: tuple[CaptionEntry, ...]


def record_to_dict(ranking: CaptionRanking) -> dict[str, Any]:
    return asdict(ranking)


def _validate(
    platform: str,
    rotation: Sequence[str],
    evidence: CaptionEvidence,
    policy: CaptionPolicy,
) -> str:
    if platform not in PLATFORMS:
        raise CaptionRankingRefused(f"platform {platform!r} not in {PLATFORMS}")
    if not rotation or len(set(rotation)) != len(rotation) or not all(rotation):
        raise CaptionRankingRefused("rotation: give distinct, non-empty template ids")
    metric = policy.metric_by_platform.get(platform)
    if not metric:
        raise CaptionRankingRefused(f"policy: no metric for platform {platform!r}")
    try:
        as_of = parse_ts(evidence.as_of)
    except (TypeError, ValueError) as exc:
        raise CaptionRankingRefused("evidence: as_of is not an ISO timestamp") from exc
    seen = set()
    for o in evidence.observations:
        key = (o.platform, o.template_id, o.metric_source)
        if key in seen:
            raise CaptionRankingRefused(f"evidence: duplicate observation {key}")
        seen.add(key)
        if o.median < 0 or o.n < 0:
            raise CaptionRankingRefused(f"evidence: {key} median or n out of range")
        try:
            start, end = parse_ts(o.window_start), parse_ts(o.window_end)
        except (TypeError, ValueError) as exc:
            raise CaptionRankingRefused(f"evidence: {key} window is not ISO") from exc
        if start > end or end > as_of:
            raise CaptionRankingRefused(f"evidence: {key} window is not before as_of")
    return metric


def _entry(
    index: int,
    template_id: str,
    mine: list[CaptionObservation],
    metric: str,
    as_of: str,
    policy: CaptionPolicy,
) -> CaptionEntry:
    def make(obs: CaptionObservation | None, excluded: str | None) -> CaptionEntry:
        return CaptionEntry(
            template_id=template_id,
            rotation_index=index,
            tier=MIDDLE,
            score=obs.median if obs else None,
            n=obs.n if obs else None,
            metric_source=obs.metric_source if obs else None,
            evidence_window=(obs.window_start, obs.window_end) if obs else None,
            excluded_reason=excluded,
        )

    if not mine:
        return make(None, NO_EVIDENCE)
    usable = [o for o in mine if o.metric_source == metric]
    if not usable:
        return make(
            min(mine, key=lambda o: o.metric_source), METRIC_NOT_CAPTION_EVIDENCE
        )
    obs = usable[0]
    cutoff = parse_ts(as_of) - timedelta(days=policy.max_evidence_age_days)
    if parse_ts(obs.window_end) < cutoff:
        return make(obs, STALE)
    if obs.n < policy.min_n:
        return make(obs, N_BELOW_MIN)
    return make(obs, None)


def rank_captions(
    platform: str,
    rotation: Sequence[str],
    evidence: CaptionEvidence,
    policy: CaptionPolicy,
) -> CaptionRanking:
    """Re-rank ``rotation`` (opener template ids, in the generator's order) for
    ``platform`` from ``evidence`` under ``policy``."""
    metric = _validate(platform, rotation, evidence, policy)
    by_template: dict[str, list[CaptionObservation]] = {}
    for o in evidence.observations:
        if o.platform == platform:
            by_template.setdefault(o.template_id, []).append(o)
    entries = [
        _entry(i, tid, by_template.get(tid, []), metric, evidence.as_of, policy)
        for i, tid in enumerate(rotation)
    ]
    measured = [e for e in entries if e.excluded_reason is None]
    reference: float | None = None
    reason = INSUFFICIENT_EVIDENCE
    if len(measured) >= policy.min_measured:
        reference = float(median(e.score or 0.0 for e in measured))
        high = reference * (1 + policy.min_relative_lift)
        low = reference * (1 - policy.min_relative_lift)
        ranked = []
        for e in entries:
            if e.excluded_reason is None and (e.score or 0.0) > high:
                e = CaptionEntry(**{**asdict(e), "tier": LEAD, "note": ABOVE_REFERENCE})
            elif e.excluded_reason is None and (e.score or 0.0) < low:
                e = CaptionEntry(**{**asdict(e), "tier": LAG, "note": BELOW_REFERENCE})
            ranked.append(e)
        entries = ranked
        reason = RANKED if any(e.tier != MIDDLE for e in entries) else NO_SPREAD

    order = tuple(
        e.template_id for tier in (LEAD, MIDDLE, LAG) for e in entries if e.tier == tier
    )
    return CaptionRanking(
        policy_version=policy.version,
        platform=platform,
        metric_source=metric,
        evidence_as_of=evidence.as_of,
        reason=reason,
        reference=reference,
        order=order,
        entries=tuple(entries),
    )
