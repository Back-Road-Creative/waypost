"""Title selection: one deterministic decision record per upload.

``select_title(facts, evidence_snapshot, policy)`` builds a bounded set of
candidate titles from verified facts and picks one from analytics evidence. It
returns a ``TitleDecision`` that names every candidate, its score, the evidence
each of its claims rests on, why it was or was not eligible, and the policy
version, so a published title can be replayed and audited later.

Facts come in three shapes. A route names ``origin``, ``destination`` and
``region``; a long-form whose GPS proves a single county or state names just
``region``; a single-place Short names one ``place``. Each shape has its own
baseline id and templates, so neither borrows the other's evidence.

Candidate 0 is always the caller's own title, the baseline. Another
candidate replaces it only when both it and the baseline have enough fresh
evidence, measured as a thumbnail-impression click rate, for the same format
and the traffic source the policy assigns that format, and it beats the
baseline by the policy's margin. Anything less keeps the baseline. A card click
rate or an unlabelled rate is never title evidence (``metric_source``).

Pure module: no clock (``as_of`` comes from the snapshot), no randomness, no
I/O, no LLM. Same inputs, in any observation order, give the same record.
"""

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from typing import Any

from . import metric_source
from .evidence import parse_ts

FORMATS = ("short", "longform")
TRAFFIC_SOURCES = ("search", "browse")

BASELINE = "route"
PLACE_BASELINE = "place"
REGION_BASELINE = "region_only_lf"

# Templates after the baseline, in candidate order. Each names the facts it
# claims; a template whose fact is absent is recorded, not generated.
TEMPLATES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "highlight_route",
        "{highlight}: {origin} to {destination}, {region}{suffix}",
        ("highlight", "origin", "destination", "region"),
    ),
    (
        "region_route",
        "{region} Drive: {origin} to {destination}{suffix}",
        ("region", "origin", "destination"),
    ),
    ("highlight_region", "{highlight}, {region}{suffix}", ("highlight", "region")),
)
_BASELINE_CLAIMS = ("origin", "destination", "region")

# Templates for a single-place Short (``TitleFacts.place``), same shape as above.
PLACE_TEMPLATES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("highlight_place", "{highlight}, {place}{suffix}", ("highlight", "place")),
)
_PLACE_BASELINE_CLAIMS = ("place",)
REGION_TEMPLATES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("region_drive_lf", "{region} Drive{suffix}", ("region",)),
)
_REGION_BASELINE_CLAIMS = ("region",)
_ROUTE_KEYS = ("origin", "destination", "region")

# Exclusion vocabulary, checked in this order.
MISSING_FACT = "missing_fact"
TOO_LONG = "too_long"
NO_EVIDENCE = "no_evidence"
METRIC_NOT_TITLE_EVIDENCE = "metric_not_title_evidence"
STALE = "stale"
N_BELOW_MIN = "n_below_min"

# Decision reasons.
INSUFFICIENT_EVIDENCE = "insufficient_evidence"
BASELINE_NOT_BEATEN = "baseline_not_beaten"
LIFT_OVER_BASELINE = "lift_over_baseline"


class TitleSelectionRefused(ValueError):
    """The inputs are not ones this function can decide on; ``str(exc)`` says why."""


@dataclass(frozen=True)
class Place:
    """A place name and the id of the evidence that puts the footage there."""

    name: str
    evidence_id: str


@dataclass(frozen=True)
class TitleFacts:
    """A route, a long-form region, or a Short's single place.

    Exactly one shape is given; a mix, or neither, is refused.
    """

    format: str
    baseline_title: str
    origin: Place | None = None
    destination: Place | None = None
    region: Place | None = None
    highlight: Place | None = None
    suffix: str = ""
    place: Place | None = None


@dataclass(frozen=True)
class TemplateObservation:
    """How one template's titles did for one format and traffic source.

    ``rate`` is a click rate in [0, 1]; ``n`` is its denominator (impressions).
    """

    template_id: str
    format: str
    traffic_source: str
    metric_source: str
    rate: float
    n: int
    window_start: str
    window_end: str


@dataclass(frozen=True)
class EvidenceSnapshot:
    as_of: str
    observations: tuple[TemplateObservation, ...] = ()


@dataclass(frozen=True)
class TitlePolicy:
    """Policy revisions live in code: a changed number is a new ``version``.

    The defaults are proposed values, not measured ones.
    """

    version: str = "title-policy-2"
    traffic_by_format: Mapping[str, str] = field(
        default_factory=lambda: {"short": "browse", "longform": "search"}
    )
    min_n: int = 400
    max_evidence_age_days: int = 28
    min_relative_lift: float = 0.10
    max_title_chars: int = 100


DEFAULT_POLICY = TitlePolicy()


@dataclass(frozen=True)
class Candidate:
    index: int
    template_id: str
    title: str | None
    claim_evidence: tuple[str, ...]
    score: float | None = None
    n: int | None = None
    metric_source: str | None = None
    evidence_window: tuple[str, str] | None = None
    excluded_reason: str | None = None


@dataclass(frozen=True)
class TitleDecision:
    policy_version: str
    format: str
    traffic_source: str
    evidence_as_of: str
    chosen_index: int
    chosen_title: str
    reason: str
    candidates: tuple[Candidate, ...]


def record_to_dict(decision: TitleDecision) -> dict[str, Any]:
    return asdict(decision)


def _places(facts: TitleFacts) -> dict[str, Place]:
    route = {k: getattr(facts, k) for k in _ROUTE_KEYS}
    given = [k for k, p in route.items() if p is not None]
    places: dict[str, Place]
    if facts.place is not None:
        if given:
            raise TitleSelectionRefused(
                f"facts: place cannot be mixed with route facts {given}"
            )
        if facts.format != "short":
            raise TitleSelectionRefused("facts: a single place is for a short only")
        places = {"place": facts.place}
    elif len(given) == len(_ROUTE_KEYS):
        places = {k: p for k, p in route.items() if p is not None}
    elif given == ["region"] and facts.format == "longform":
        places = {"region": route["region"]}
    else:
        raise TitleSelectionRefused(
            "facts: give a Short's place, a long-form region, or origin, destination and region"
        )
    if facts.highlight is not None:
        places["highlight"] = facts.highlight
    for key, place in places.items():
        if not place.name or not place.evidence_id:
            raise TitleSelectionRefused(f"facts: {key} needs a name and an evidence id")
    return places


def _validate(
    facts: TitleFacts, snapshot: EvidenceSnapshot, policy: TitlePolicy
) -> str:
    if facts.format not in FORMATS:
        raise TitleSelectionRefused(f"facts: format {facts.format!r} not in {FORMATS}")
    if not facts.baseline_title:
        raise TitleSelectionRefused("facts: baseline_title is empty")
    traffic = policy.traffic_by_format.get(facts.format)
    if traffic not in TRAFFIC_SOURCES:
        raise TitleSelectionRefused(
            f"policy: traffic source for {facts.format!r} not in {TRAFFIC_SOURCES}"
        )
    try:
        as_of = parse_ts(snapshot.as_of)
    except (TypeError, ValueError) as exc:
        raise TitleSelectionRefused("snapshot: as_of is not an ISO timestamp") from exc
    seen = set()
    for o in snapshot.observations:
        key = (o.template_id, o.format, o.traffic_source, o.metric_source)
        if key in seen:
            raise TitleSelectionRefused(f"snapshot: duplicate observation {key}")
        seen.add(key)
        if not 0 <= o.rate <= 1 or o.n < 0:
            raise TitleSelectionRefused(f"snapshot: {key} rate or n out of range")
        try:
            start, end = parse_ts(o.window_start), parse_ts(o.window_end)
        except (TypeError, ValueError) as exc:
            raise TitleSelectionRefused(f"snapshot: {key} window is not ISO") from exc
        if start > end or end > as_of:
            raise TitleSelectionRefused(f"snapshot: {key} window is not before as_of")
    return str(traffic)


def _evidence_for(
    template_id: str,
    observations: Sequence[TemplateObservation],
    as_of: str,
    policy: TitlePolicy,
) -> tuple[TemplateObservation | None, str | None]:
    mine = [o for o in observations if o.template_id == template_id]
    if not mine:
        return None, NO_EVIDENCE
    usable = [
        o for o in mine if metric_source.is_thumbnail_click_through(o.metric_source)
    ]
    if not usable:
        return min(mine, key=lambda o: o.metric_source), METRIC_NOT_TITLE_EVIDENCE
    obs = usable[0]
    cutoff = parse_ts(as_of) - timedelta(days=policy.max_evidence_age_days)
    if parse_ts(obs.window_end) < cutoff:
        return obs, STALE
    if obs.n < policy.min_n:
        return obs, N_BELOW_MIN
    return obs, None


def _candidate(
    index: int,
    template_id: str,
    title: str | None,
    claims: tuple[str, ...],
    obs: TemplateObservation | None,
    excluded: str | None,
) -> Candidate:
    return Candidate(
        index=index,
        template_id=template_id,
        title=title,
        claim_evidence=claims,
        score=obs.rate if obs else None,
        n=obs.n if obs else None,
        metric_source=obs.metric_source if obs else None,
        evidence_window=(obs.window_start, obs.window_end) if obs else None,
        excluded_reason=excluded,
    )


def select_title(
    facts: TitleFacts, evidence_snapshot: EvidenceSnapshot, policy: TitlePolicy
) -> TitleDecision:
    """Pick a title for ``facts`` from ``evidence_snapshot`` under ``policy``."""
    traffic = _validate(facts, evidence_snapshot, policy)
    places = _places(facts)
    observations = [
        o
        for o in evidence_snapshot.observations
        if o.format == facts.format and o.traffic_source == traffic
    ]
    as_of = evidence_snapshot.as_of

    if "place" in places:
        baseline_id, baseline_claims, templates = (
            PLACE_BASELINE,
            _PLACE_BASELINE_CLAIMS,
            PLACE_TEMPLATES,
        )
    elif "origin" not in places:
        baseline_id, baseline_claims, templates = (
            REGION_BASELINE,
            _REGION_BASELINE_CLAIMS,
            REGION_TEMPLATES,
        )
    else:
        baseline_id, baseline_claims, templates = BASELINE, _BASELINE_CLAIMS, TEMPLATES

    obs, excluded = _evidence_for(baseline_id, observations, as_of, policy)
    claims = tuple(places[k].evidence_id for k in baseline_claims)
    candidates: list[Candidate] = [
        _candidate(0, baseline_id, facts.baseline_title, claims, obs, excluded)
    ]
    for index, (template_id, template, needs) in enumerate(templates, start=1):
        if any(k not in places for k in needs):
            candidates.append(
                _candidate(index, template_id, None, (), None, MISSING_FACT)
            )
            continue
        values = {k: p.name for k, p in places.items()}
        title = template.format(suffix=facts.suffix, **values)
        claims = tuple(places[k].evidence_id for k in needs)
        if len(title) > policy.max_title_chars:
            candidates.append(
                _candidate(index, template_id, title, claims, None, TOO_LONG)
            )
            continue
        obs, excluded = _evidence_for(template_id, observations, as_of, policy)
        candidates.append(_candidate(index, template_id, title, claims, obs, excluded))

    baseline = candidates[0]
    eligible = [c for c in candidates[1:] if c.excluded_reason is None]
    chosen, reason = baseline, INSUFFICIENT_EVIDENCE
    if baseline.excluded_reason is None and eligible:
        # Highest score wins; equal scores go to the lower index.
        best = min(eligible, key=lambda c: (-(c.score or 0.0), c.index))
        bar = (baseline.score or 0.0) * (1 + policy.min_relative_lift)
        if (best.score or 0.0) > bar:
            chosen, reason = best, LIFT_OVER_BASELINE
        else:
            reason = BASELINE_NOT_BEATEN

    return TitleDecision(
        policy_version=policy.version,
        format=facts.format,
        traffic_source=traffic,
        evidence_as_of=as_of,
        chosen_index=chosen.index,
        chosen_title=chosen.title or facts.baseline_title,
        reason=reason,
        candidates=tuple(candidates),
    )
