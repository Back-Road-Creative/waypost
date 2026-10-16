"""Review the text a viewer will see against the title that will be uploaded.

An operator approves on-screen text (the burned-in OST, the thumbnail, a
Short's burned text) and a title before assembly. ``select_title`` may pick a
different title later. ``review_final_text`` puts both in one record: the
exact visible text, the exact final title, the location and date evidence
behind each, and a verdict.

The verdict is ``refused`` when evidence is missing, ambiguous or does not
show in the text it backs; ``disagree`` when the final title is not the one
the operator approved; ``agree`` otherwise. Every reason found is recorded,
and ``refused`` outranks ``disagree``.

The review never approves anything. ``human_gate_required`` is always True:
a caller keeps its own human gate and uses ``verify_review`` just before
upload, which refuses any title or visible text that changed after the
review, and any review whose fields were edited. Pure: no clock, no I/O.

Record v2 also carries the route. Each ``route_places`` item says how long
the retained footage spends at a place; a place held for at least
``FinalTextPolicy.min_place_seconds`` is required. A whole-video surface,
and a long-form title, must name every required place, or name a region
whose ``region_claims`` item contains them all with full GPS coverage, no
gaps and no samples outside it. A segment surface (a Short's burned text),
and a Short's title, must name its own ``segment_places`` the same way.
``verify_review`` refuses a review whose source, plan, title policy or text
policy changed since it was made.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, fields
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

REVIEW_VERSION = "final-text-review-2"
RECORD_VERSION = "final-text-record-1"

AGREE = "agree"
DISAGREE = "disagree"
REFUSED = "refused"

LOCATION = "location"
REGION = "region"
DATE = "date"
_PLACE_KINDS = frozenset({LOCATION, REGION})

WHOLE = "whole"
SEGMENT = "segment"

TITLE_SURFACE = "title"
DATE_SURFACE = "recording_date"
_RESERVED = frozenset({TITLE_SURFACE, DATE_SURFACE})

TITLE_CHANGED = "title_changed_after_approval"


class FinalTextReviewRefused(ValueError):
    """``verify_review`` will not pass this upload.

    ``reasons`` holds every reason code found, ``code`` the first.
    """

    def __init__(self, reasons: Sequence[str], detail: str = "") -> None:
        self.reasons: Tuple[str, ...] = tuple(reasons)
        self.code = self.reasons[0]
        message = "; ".join(self.reasons)
        super().__init__(f"{message}: {detail}" if detail else message)


@dataclass(frozen=True)
class FinalTextPolicy:
    """The thresholds a review applies; recorded in, and checked against, it."""

    version: str = "final-text-policy-1"
    min_place_seconds: float = 60.0
    region_min_coverage: float = 1.0


DEFAULT_TEXT_POLICY = FinalTextPolicy()


@dataclass(frozen=True)
class EvidenceItem:
    evidence_id: str
    kind: str
    value: str
    source: str


@dataclass(frozen=True)
class SurfaceReview:
    """One piece of text a viewer sees, and the evidence behind it."""

    surface: str
    text: str
    evidence: Tuple[EvidenceItem, ...]
    scope: str = WHOLE
    segment_places: Tuple[str, ...] = ()


@dataclass(frozen=True)
class RoutePlace:
    evidence_id: str
    retained_seconds: float
    required: bool


@dataclass(frozen=True)
class RegionClaim:
    """A region named in place of the route, and its retained GPS coverage."""

    evidence_id: str
    contains: Tuple[str, ...]
    coverage: float
    gaps: Tuple[Tuple[float, float], ...]
    samples_outside: int


@dataclass(frozen=True)
class FinalTextReview:
    review_version: str
    verdict: str
    reasons: Tuple[str, ...]
    record_version: Optional[str]
    source_fingerprint: Optional[str]
    plan_fingerprint: Optional[str]
    text_policy: Dict[str, Any]
    route_places: Tuple[RoutePlace, ...]
    region_claims: Tuple[RegionClaim, ...]
    approval_hash: Optional[str]
    approved_title: Optional[str]
    final_title: Optional[str]
    recording_date: Optional[str]
    surfaces: Tuple[SurfaceReview, ...]
    decision_policy_version: Optional[str]
    decision_reason: Optional[str]
    decision_chosen_index: Optional[int]
    human_gate_required: bool
    fingerprint: str


def _plain(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


def review_to_dict(review: FinalTextReview) -> Dict[str, Any]:
    """JSON-safe form of ``review`` (tuples become lists)."""
    return _plain(asdict(review))


def _fingerprint_of(fields_: Mapping[str, Any]) -> str:
    body = dict(fields_)
    body.pop("fingerprint", None)
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _fingerprint(review: FinalTextReview) -> str:
    return _fingerprint_of(review_to_dict(review))


def _text(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value.strip() else None


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _ids(value: Any) -> Optional[Tuple[str, ...]]:
    if not isinstance(value, (list, tuple)) or not all(_text(v) for v in value):
        return None
    return tuple(value)


class _Reasons:
    def __init__(self) -> None:
        self.items: List[str] = []
        self.refused = False

    def refuse(self, reason: str) -> None:
        self.refused = True
        if reason not in self.items:
            self.items.append(reason)


def _index_evidence(
    evidence: Sequence[Mapping[str, Any]], reasons: _Reasons
) -> Tuple[Dict[str, EvidenceItem], set]:
    by_id: Dict[str, EvidenceItem] = {}
    ambiguous: set = set()
    for position, raw in enumerate(evidence):
        try:
            item = EvidenceItem(**{f.name: raw[f.name] for f in fields(EvidenceItem)})
        except (KeyError, TypeError):
            reasons.refuse(f"evidence_malformed:{position}")
            continue
        if not all(_text(getattr(item, f.name)) for f in fields(EvidenceItem)):
            reasons.refuse(f"evidence_malformed:{position}")
            continue
        seen = by_id.get(item.evidence_id)
        if seen is not None and seen != item:
            ambiguous.add(item.evidence_id)
        by_id.setdefault(item.evidence_id, item)
    return by_id, ambiguous


def _resolve(
    surface: str,
    text: Optional[str],
    ids: Sequence[str],
    kinds: frozenset,
    by_id: Mapping[str, EvidenceItem],
    ambiguous: set,
    reasons: _Reasons,
) -> Tuple[EvidenceItem, ...]:
    found: List[EvidenceItem] = []
    for evidence_id in ids:
        if evidence_id in ambiguous:
            reasons.refuse(f"evidence_ambiguous:{evidence_id}")
            continue
        item = by_id.get(evidence_id)
        if item is None:
            reasons.refuse(f"evidence_missing:{evidence_id}")
            continue
        found.append(item)
        if item.kind not in kinds:
            reasons.refuse(f"evidence_kind:{evidence_id}")
        elif text is not None and item.value.casefold() not in text.casefold():
            reasons.refuse(f"claim_not_visible:{surface}:{evidence_id}")
    return tuple(found)


def _chosen(
    decision: Mapping[str, Any], reasons: _Reasons
) -> Tuple[Optional[str], Tuple[str, ...]]:
    """The decision's final title and its claim evidence ids, or refusal."""
    title = _text(decision.get("chosen_title"))
    candidates = decision.get("candidates")
    index = decision.get("chosen_index")
    if title is None or not isinstance(candidates, (list, tuple)):
        reasons.refuse("decision_malformed")
        return title, ()
    if not candidates:
        reasons.refuse("title_without_evidence")
        return title, ()
    if (
        not isinstance(index, int)
        or isinstance(index, bool)
        or not 0 <= index < len(candidates)
        or not isinstance(candidates[index], Mapping)
        or candidates[index].get("title") != title
    ):
        reasons.refuse("decision_malformed")
        return title, ()
    claims = candidates[index].get("claim_evidence") or ()
    if not isinstance(claims, (list, tuple)) or not all(
        isinstance(c, str) for c in claims
    ):
        reasons.refuse("decision_malformed")
        return title, ()
    if not claims:
        reasons.refuse("title_without_evidence")
    return title, tuple(claims)


def _route(
    approved: Mapping[str, Any],
    policy: FinalTextPolicy,
    by_id: Mapping[str, EvidenceItem],
    ambiguous: set,
    reasons: _Reasons,
) -> Tuple[RoutePlace, ...]:
    raw = approved.get("route_places")
    if not isinstance(raw, (list, tuple)) or not raw:
        reasons.refuse("missing_field:route_places")
        return ()
    route: List[RoutePlace] = []
    for position, item in enumerate(raw):
        evidence_id = (
            _text(item.get("evidence_id")) if isinstance(item, Mapping) else None
        )
        seconds = item.get("retained_seconds") if evidence_id else None
        required = item.get("required") if evidence_id else None
        if not (_number(seconds) and seconds >= 0 and isinstance(required, bool)):
            reasons.refuse(f"route_place_malformed:{position}")
            continue
        assert evidence_id is not None
        if any(p.evidence_id == evidence_id for p in route):
            reasons.refuse(f"route_place_duplicate:{evidence_id}")
            continue
        _resolve(
            "route",
            None,
            (evidence_id,),
            frozenset({LOCATION}),
            by_id,
            ambiguous,
            reasons,
        )
        if required != (seconds >= policy.min_place_seconds):
            reasons.refuse(f"required_mismatch:{evidence_id}")
        route.append(RoutePlace(evidence_id, float(seconds), required))
    return tuple(route)


def _gaps(value: Any) -> Optional[Tuple[Tuple[float, float], ...]]:
    if not isinstance(value, (list, tuple)):
        return None
    gaps = []
    for gap in value:
        if not isinstance(gap, Mapping):
            return None
        start, end = gap.get("start_s"), gap.get("end_s")
        if not (_number(start) and _number(end) and 0 <= start <= end):
            return None
        gaps.append((float(start), float(end)))
    return tuple(gaps)


def _regions(
    approved: Mapping[str, Any],
    route_ids: Sequence[str],
    by_id: Mapping[str, EvidenceItem],
    ambiguous: set,
    reasons: _Reasons,
) -> Tuple[RegionClaim, ...]:
    raw = approved.get("region_claims")
    if not isinstance(raw, (list, tuple)):
        reasons.refuse("missing_field:region_claims")
        return ()
    claims: List[RegionClaim] = []
    for position, item in enumerate(raw):
        evidence_id = (
            _text(item.get("evidence_id")) if isinstance(item, Mapping) else None
        )
        if evidence_id is None:
            reasons.refuse(f"region_claim_malformed:{position}")
            continue
        contains = _ids(item.get("contains"))
        coverage = item.get("coverage")
        gaps = _gaps(item.get("gaps"))
        outside = item.get("samples_outside")
        if (
            not contains
            or not (_number(coverage) and 0 <= coverage <= 1)
            or gaps is None
            or not (_number(outside) and isinstance(outside, int) and outside >= 0)
        ):
            reasons.refuse(f"region_claim_malformed:{evidence_id}")
            continue
        _resolve(
            "region",
            None,
            (evidence_id,),
            frozenset({REGION}),
            by_id,
            ambiguous,
            reasons,
        )
        for place in contains:
            if place not in route_ids:
                reasons.refuse(f"route_place_unknown:{place}")
        claims.append(
            RegionClaim(evidence_id, contains, float(coverage), gaps, outside)
        )
    return tuple(claims)


def _region_faults(
    claim: RegionClaim, needed: Sequence[str], policy: FinalTextPolicy
) -> List[str]:
    faults = []
    if not set(needed) <= set(claim.contains):
        faults.append("region_partial")
    if claim.coverage < policy.region_min_coverage:
        faults.append("region_coverage")
    if claim.gaps:
        faults.append("region_gaps")
    if claim.samples_outside:
        faults.append("region_outside")
    if set(faults) - {"region_partial"}:
        # One code for "the retained route leaves the region", for callers
        # that do not care which measurement showed it.
        faults.append("region_not_contained")
    return faults


def _check_names(
    surface: SurfaceReview,
    needed: Sequence[str],
    dropped: str,
    regions: Sequence[RegionClaim],
    policy: FinalTextPolicy,
    reasons: _Reasons,
) -> None:
    """Refuse ``surface`` unless it names every ``needed`` place or a sound region."""
    text = surface.text.casefold()
    named = {i.evidence_id for i in surface.evidence if i.value.casefold() in text}
    missing = [place for place in needed if place not in named]
    if not missing:
        return
    bases = [c for c in regions if c.evidence_id in named]
    faults = [(c, _region_faults(c, needed, policy)) for c in bases]
    if any(not found for _, found in faults):
        return
    for claim, found in faults:
        for fault in found:
            reasons.refuse(f"{fault}:{claim.evidence_id}")
    for place in missing:
        reasons.refuse(f"{dropped}:{surface.surface}:{place}")
    if (
        dropped == "place_dropped"
        and len(needed) >= 2
        and len(missing) == len(needed) - 1
    ):
        reasons.refuse(f"route_reduced:{surface.surface}")


def review_final_text(
    approved: Mapping[str, Any],
    decision: Mapping[str, Any],
    evidence: Sequence[Mapping[str, Any]],
    policy: FinalTextPolicy = DEFAULT_TEXT_POLICY,
) -> FinalTextReview:
    """Review ``approved`` text against the final title in ``decision``.

    ``approved`` (the GMS final-text record): ``record_version``,
    ``approval_hash``, ``title`` (the title the operator approved),
    ``recording_date``, ``date_evidence_id``, ``source_fingerprint``,
    ``plan_fingerprint``, ``route_places`` (``{evidence_id,
    retained_seconds, required}``), ``region_claims`` (``{evidence_id,
    contains, coverage, gaps, samples_outside}``) and ``visible``, a list of
    ``{surface, text, evidence_ids, scope}`` where ``scope`` is ``whole`` or
    ``segment`` and a segment also lists its ``segment_places``.
    ``decision``: the dict form of a ``TitleDecision``. ``evidence``:
    ``{evidence_id, kind, value, source}`` items, ``kind`` being
    ``location``, ``region`` or ``date``.
    """
    reasons = _Reasons()
    by_id, ambiguous = _index_evidence(evidence, reasons)

    record_version = _text(approved.get("record_version"))
    if record_version is None:
        reasons.refuse("missing_field:record_version")
    elif record_version != RECORD_VERSION:
        reasons.refuse(f"record_version:{record_version}")

    named_fields = {
        name: _text(approved.get(name))
        for name in (
            "approval_hash",
            "title",
            "recording_date",
            "source_fingerprint",
            "plan_fingerprint",
        )
    }
    for name, value in named_fields.items():
        if value is None:
            reasons.refuse(f"missing_field:{name}")
    approved_title = named_fields["title"]
    recording_date = named_fields["recording_date"]

    route = _route(approved, policy, by_id, ambiguous, reasons)
    route_ids = [p.evidence_id for p in route]
    required = [p.evidence_id for p in route if p.required]
    regions = _regions(approved, route_ids, by_id, ambiguous, reasons)

    surfaces: List[SurfaceReview] = []
    visible = approved.get("visible")
    if not isinstance(visible, (list, tuple)) or not visible:
        reasons.refuse("missing_field:visible")
        visible = ()
    names: set = set()
    for position, entry in enumerate(visible):
        surface = _text(entry.get("surface")) if isinstance(entry, Mapping) else None
        text = _text(entry.get("text")) if surface else None
        ids = entry.get("evidence_ids") if surface else None
        scope = entry.get("scope") if surface else None
        segment = _ids(entry.get("segment_places")) if scope == SEGMENT else ()
        if (
            surface is None
            or text is None
            or not isinstance(ids, (list, tuple))
            or scope not in (WHOLE, SEGMENT)
            or not (segment or scope == WHOLE)
        ):
            reasons.refuse(f"missing_field:visible[{position}]")
            continue
        assert segment is not None
        if surface in _RESERVED:
            reasons.refuse(f"reserved_surface:{surface}")
            continue
        if surface in names:
            reasons.refuse(f"duplicate_surface:{surface}")
            continue
        names.add(surface)
        if not ids:
            reasons.refuse(f"surface_without_evidence:{surface}")
        items = _resolve(surface, text, ids, _PLACE_KINDS, by_id, ambiguous, reasons)
        for place in segment:
            if place not in route_ids:
                reasons.refuse(f"route_place_unknown:{place}")
        surfaces.append(SurfaceReview(surface, text, items, scope, segment))

    segment_needed: List[str] = []
    for review_surface in surfaces:
        if review_surface.scope == WHOLE:
            _check_names(
                review_surface, required, "place_dropped", regions, policy, reasons
            )
            continue
        needed = [p for p in review_surface.segment_places if p in route_ids]
        segment_needed += [p for p in needed if p not in segment_needed]
        _check_names(
            review_surface, needed, "segment_place_dropped", regions, policy, reasons
        )

    final_title, claims = _chosen(decision, reasons)
    title_items = _resolve(
        TITLE_SURFACE,
        final_title or "",
        claims,
        _PLACE_KINDS,
        by_id,
        ambiguous,
        reasons,
    )
    title_surface = SurfaceReview(TITLE_SURFACE, final_title or "", title_items)
    if decision.get("format") == "short" and segment_needed:
        _check_names(
            title_surface,
            segment_needed,
            "segment_place_dropped",
            regions,
            policy,
            reasons,
        )
    else:
        _check_names(title_surface, required, "place_dropped", regions, policy, reasons)
    surfaces.append(title_surface)

    date_id = _text(approved.get("date_evidence_id"))
    date_items: Tuple[EvidenceItem, ...] = ()
    if date_id is None:
        reasons.refuse("date_without_evidence")
    else:
        date_items = _resolve(
            DATE_SURFACE, None, (date_id,), frozenset({DATE}), by_id, ambiguous, reasons
        )
        for item in date_items:
            if item.kind == DATE and recording_date and item.value != recording_date:
                reasons.refuse(f"date_mismatch:{date_id}")
    surfaces.append(SurfaceReview(DATE_SURFACE, recording_date or "", date_items))

    disagree = bool(approved_title and final_title and approved_title != final_title)
    if disagree:
        reasons.items.append(TITLE_CHANGED)
    verdict = REFUSED if reasons.refused else DISAGREE if disagree else AGREE

    index = decision.get("chosen_index")
    review = FinalTextReview(
        review_version=REVIEW_VERSION,
        verdict=verdict,
        reasons=tuple(reasons.items),
        record_version=record_version,
        source_fingerprint=named_fields["source_fingerprint"],
        plan_fingerprint=named_fields["plan_fingerprint"],
        text_policy=asdict(policy),
        route_places=route,
        region_claims=regions,
        approval_hash=named_fields["approval_hash"],
        approved_title=approved_title,
        final_title=final_title,
        recording_date=recording_date,
        surfaces=tuple(surfaces),
        decision_policy_version=_text(decision.get("policy_version")),
        decision_reason=_text(decision.get("reason")),
        decision_chosen_index=index if isinstance(index, int) else None,
        human_gate_required=True,
        fingerprint="",
    )
    return FinalTextReview(
        **{
            **{f.name: getattr(review, f.name) for f in fields(review)},
            "fingerprint": _fingerprint(review),
        }
    )


REVIEW_MISSING = "review_missing"
REVIEW_MALFORMED = "review_malformed"
REVIEW_TAMPERED = "review_tampered"


def _stale(
    body: Mapping[str, Any],
    final_title: str,
    visible: Mapping[str, str],
    source_fingerprint: str,
    plan_fingerprint: str,
    policy_version: str,
    text_policy: FinalTextPolicy,
) -> List[str]:
    found: List[str] = []
    if body["review_version"] != REVIEW_VERSION:
        return ["stale:review_version"]
    if body["verdict"] != AGREE:
        found.append(f"verdict:{body['verdict']}")
    for code, expected, actual in (
        ("stale:source", body["source_fingerprint"], source_fingerprint),
        ("stale:plan", body["plan_fingerprint"], plan_fingerprint),
        ("stale:policy", body["decision_policy_version"], policy_version),
        ("stale:text_policy", body["text_policy"], asdict(text_policy)),
        ("stale:title", body["final_title"], final_title),
    ):
        if (not _text(actual) and not isinstance(actual, dict)) or expected != actual:
            found.append(code)
    reviewed = {
        s["surface"]: s["text"]
        for s in body["surfaces"]
        if s["surface"] not in _RESERVED
    }
    if set(visible) != set(reviewed):
        found.append("stale:surfaces")
    for surface, text in sorted(reviewed.items()):
        if surface in visible and visible[surface] != text:
            found.append(f"stale:text:{surface}")
    return found


def verify_review(
    review: Any,
    *,
    final_title: str,
    visible: Mapping[str, str],
    source_fingerprint: str,
    plan_fingerprint: str,
    policy_version: str,
    text_policy: FinalTextPolicy = DEFAULT_TEXT_POLICY,
) -> None:
    """Refuse an upload unless ``review`` covers exactly what will be sent.

    ``review`` is a ``FinalTextReview`` or its persisted dict form.
    ``visible`` maps each surface (``ost``, ``thumbnail``, ``shorts_ost``, …)
    to the text the upload will show; ``source_fingerprint`` and
    ``plan_fingerprint`` are recomputed from current state, and
    ``policy_version`` is the title policy in force. Raises
    ``FinalTextReviewRefused`` naming every reason. Passing this is not an
    approval: the caller's human gate still applies.
    """
    if isinstance(review, FinalTextReview):
        body: Dict[str, Any] = review_to_dict(review)
    elif isinstance(review, Mapping):
        body = dict(review)
    elif review is None or isinstance(review, str):
        raise FinalTextReviewRefused([REVIEW_MISSING], "no review was recorded")
    else:
        raise FinalTextReviewRefused([REVIEW_MALFORMED], type(review).__name__)
    if not _text(body.get("fingerprint")):
        raise FinalTextReviewRefused([REVIEW_MISSING], "review has no fingerprint")
    if set(body) != {f.name for f in fields(FinalTextReview)}:
        raise FinalTextReviewRefused([REVIEW_MALFORMED], "review fields differ")
    if body["fingerprint"] != _fingerprint_of(body):
        raise FinalTextReviewRefused(
            [REVIEW_TAMPERED], "review fingerprint does not match its fields"
        )
    if body["human_gate_required"] is not True:
        raise FinalTextReviewRefused([REVIEW_MALFORMED], "human gate not required")
    try:
        found = _stale(
            body,
            final_title,
            visible,
            source_fingerprint,
            plan_fingerprint,
            policy_version,
            text_policy,
        )
    except (KeyError, TypeError, AttributeError) as exc:
        raise FinalTextReviewRefused([REVIEW_MALFORMED], repr(exc)) from exc
    if found:
        raise FinalTextReviewRefused(found, ", ".join(body["reasons"]))
