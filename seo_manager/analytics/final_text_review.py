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
gaps and no samples outside it, or has a matching pinned bounded-route proof
for coverage and gaps. Observed samples outside always refuse. A segment
surface (a Short's burned text),
and a Short's title, must name its own ``segment_places`` the same way.
The reverse holds too: every name a surface shows needs a claim. Once its
cited names are taken out, a word outside ``FinalTextPolicy.filler_words``
is refused as a name nothing backs, and a cited location must be on the
retained route (a Short: in its own segment).
``verify_review`` refuses a review whose source, plan, title policy or text
policy changed since it was made.

Record v2 (review v4) types every visible surface (``final_text_surfaces``):
a ``place_line`` is checked as above, a ``template`` passes only on a
registered id whose hash matches its exact bytes, a ``chapter`` is a segment
surface over its own window, and a ``tag`` is backed by evidence or taken
from a registered tag template. A surface of any other kind is refused, so
no free prose is ever reviewed.
"""

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, fields
from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .drive_templates import (
    DRIVE_TEMPLATES_VERSION,
    DRIVE_TEXT,
    LAYOUT_GAPS,
    LAYOUTS,
    PARAMS,
    PLACE_TAG,
    REPEATABLE_TAGS,
    TAG_ORDER,
    drive_hashes,
)
from .final_text_params import param_hash, render, render_value, valid_value
from .final_text_surfaces import (
    CHAPTER,
    PARAM_TEMPLATE,
    PLACE_LINE,
    TAG,
    TEMPLATE,
    VISIBLE_KINDS,
    compose_description,
    compose_tags,
    is_description_surface,
    is_tag_surface,
    place_text,
    template_hash,
    template_hashes,
)

REVIEW_VERSION = "final-text-review-6"
RECORD_VERSION = "final-text-record-2"
BOUNDARY_PROOF_VERSION = "tiger2026-bounded-v1"
_BOUNDARY_SOURCES = {
    "county": (
        "TIGER2026/COUNTY/tl_2026_us_county.zip",
        "b527c9ba7aeb8f17ef118f026eae5e5a938aa1d00b873bc32342d0dce59321c0",  # pragma: allowlist secret
    ),
    "state": (
        "TIGER2026/STATE/tl_2026_us_state.zip",
        "2e17ea40dff3e00163eec87a26efa0b0ff7e8fe364a191bf1a7754dcb49ea326",  # pragma: allowlist secret
    ),
}
# Canonical TIGER state name -> (postal abbreviation, STATEFP). The state
# source's full name may appear as its postal abbreviation in GMS titles.
_US_STATES = {
    "Alabama": ("AL", "01"),
    "Alaska": ("AK", "02"),
    "Arizona": ("AZ", "04"),
    "Arkansas": ("AR", "05"),
    "California": ("CA", "06"),
    "Colorado": ("CO", "08"),
    "Connecticut": ("CT", "09"),
    "Delaware": ("DE", "10"),
    "District of Columbia": ("DC", "11"),
    "Florida": ("FL", "12"),
    "Georgia": ("GA", "13"),
    "Hawaii": ("HI", "15"),
    "Idaho": ("ID", "16"),
    "Illinois": ("IL", "17"),
    "Indiana": ("IN", "18"),
    "Iowa": ("IA", "19"),
    "Kansas": ("KS", "20"),
    "Kentucky": ("KY", "21"),
    "Louisiana": ("LA", "22"),
    "Maine": ("ME", "23"),
    "Maryland": ("MD", "24"),
    "Massachusetts": ("MA", "25"),
    "Michigan": ("MI", "26"),
    "Minnesota": ("MN", "27"),
    "Mississippi": ("MS", "28"),
    "Missouri": ("MO", "29"),
    "Montana": ("MT", "30"),
    "Nebraska": ("NE", "31"),
    "Nevada": ("NV", "32"),
    "New Hampshire": ("NH", "33"),
    "New Jersey": ("NJ", "34"),
    "New Mexico": ("NM", "35"),
    "New York": ("NY", "36"),
    "North Carolina": ("NC", "37"),
    "North Dakota": ("ND", "38"),
    "Ohio": ("OH", "39"),
    "Oklahoma": ("OK", "40"),
    "Oregon": ("OR", "41"),
    "Pennsylvania": ("PA", "42"),
    "Rhode Island": ("RI", "44"),
    "South Carolina": ("SC", "45"),
    "South Dakota": ("SD", "46"),
    "Tennessee": ("TN", "47"),
    "Texas": ("TX", "48"),
    "Utah": ("UT", "49"),
    "Vermont": ("VT", "50"),
    "Virginia": ("VA", "51"),
    "Washington": ("WA", "53"),
    "West Virginia": ("WV", "54"),
    "Wisconsin": ("WI", "55"),
    "Wyoming": ("WY", "56"),
}

AGREE = "agree"
DISAGREE = "disagree"
REFUSED = "refused"

LOCATION = "location"
REGION = "region"
DATE = "date"
ROAD = "road"
_PLACE_KINDS = frozenset({LOCATION, REGION, ROAD})

# Words a surface may carry besides its cited names: the connectors in
# Waypost's title templates and the usual title suffixes. Anything else
# that is left once the cited names are taken out counts as a name with
# no claim behind it.
DEFAULT_FILLER_WORDS: Tuple[str, ...] = (
    "to",
    "via",
    "and",
    "from",
    "loop",
    "drive",
    "in",
    "4k",
    "shorts",
    "the",
    "of",
    "at",
    "near",
    "through",
)

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

    version: str = "final-text-policy-4"
    min_place_seconds: float = 60.0
    region_min_coverage: float = 1.0
    filler_words: Tuple[str, ...] = DEFAULT_FILLER_WORDS
    # ``(template_id, sha256)`` of each template a surface may carry.
    templates: Tuple[Tuple[str, str], ...] = template_hashes()
    # The drive set (fixed lines, tags, parameterized skeletons), versioned:
    # a record written under one version is refused under another.
    drive_templates: Tuple[Tuple[str, str], ...] = drive_hashes()
    drive_version: str = DRIVE_TEMPLATES_VERSION
    # Playlist list ids a ``playlist_url`` slot may name; empty refuses all.
    playlist_allowlist: Tuple[str, ...] = ()


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
    kind: str = PLACE_LINE
    template_id: Optional[str] = None


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
    boundary_proof: Optional[Dict[str, Any]] = None


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
    description_layout: Optional[str]
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


def _bounded_proof(
    raw: Any,
    *,
    region: str,
    recording_date: Optional[str],
    gaps: Tuple[Tuple[float, float], ...],
) -> bool:
    """Validate the GMS producer's pinned proof envelope, not its geometry.

    Geometry is checked by GMS before rendering. The review binds that result
    to its named region, date and conservative uncertainty policy.
    """
    if not isinstance(raw, Mapping) or not isinstance(recording_date, str):
        return False
    source = raw.get("source")
    match = next(
        (item for item in _BOUNDARY_SOURCES.values() if item[0] == source), None
    )
    if match is None or raw.get("sha256") != match[1]:
        return False
    if raw.get("recording_date") != recording_date:
        return False
    try:
        recorded_on = date.fromisoformat(recording_date)
    except ValueError:
        return False
    if recorded_on.year != 2026 or raw.get("verified") is not True:
        return False
    if raw.get("basis") != "pinned_tiger_polygon_bounded_speed_and_error":
        return False
    statefp = raw.get("statefp")
    if not isinstance(statefp, str) or not re.fullmatch(r"\d{2}", statefp):
        return False
    proof_region = raw.get("region")
    if not isinstance(proof_region, str):
        return False
    if source == _BOUNDARY_SOURCES["state"][0]:
        state = _US_STATES.get(proof_region)
        if (
            state is None
            or statefp != state[1]
            or region not in (proof_region, state[0])
        ):
            return False
    elif proof_region != region:
        return False
    try:
        speed = float(raw.get("max_speed_m_s"))
        error = float(raw.get("gps_error_m"))
        max_gap = float(raw.get("max_gap_s"))
    except (OverflowError, TypeError, ValueError):
        return False
    return (
        math.isfinite(speed)
        and speed >= 70.0
        and math.isfinite(error)
        and error >= 100.0
        and math.isfinite(max_gap)
        # Both sides carry times to the millisecond; comparing at that
        # precision keeps float noise in end - start from refusing a gap
        # exactly as long as the proof's bound.
        and round(max_gap, 3)
        >= round(max((end - start for start, end in gaps), default=0.0), 3)
    )


def _regions(
    approved: Mapping[str, Any],
    recording_date: Optional[str],
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
        raw_proof = item.get("boundary_proof")
        proof = None
        if raw_proof is not None:
            region = by_id.get(evidence_id)
            if not _bounded_proof(
                raw_proof,
                region=region.value if region else "",
                recording_date=recording_date,
                gaps=gaps,
            ):
                reasons.refuse(f"boundary_proof_invalid:{evidence_id}")
            else:
                proof = dict(raw_proof)
        claims.append(
            RegionClaim(evidence_id, contains, float(coverage), gaps, outside, proof)
        )
    return tuple(claims)


def _region_faults(
    claim: RegionClaim, needed: Sequence[str], policy: FinalTextPolicy
) -> List[str]:
    faults = []
    if not set(needed) <= set(claim.contains):
        faults.append("region_partial")
    if claim.coverage < policy.region_min_coverage and claim.boundary_proof is None:
        faults.append("region_coverage")
    if claim.gaps and claim.boundary_proof is None:
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


def _check_backing(
    surface: SurfaceReview,
    places: Sequence[str],
    off_places: str,
    policy: FinalTextPolicy,
    reasons: _Reasons,
) -> None:
    """Refuse a name on ``surface`` that no claim in ``places`` stands behind.

    Every cited location must be one of ``places`` (the retained route, or a
    segment's own places). Every word left once the cited names are taken out
    must be filler: otherwise it is a name nothing backs.
    """
    residual = surface.text.casefold()
    for item in sorted(surface.evidence, key=lambda i: -len(i.value)):
        if item.kind == LOCATION and item.evidence_id not in places:
            reasons.refuse(f"{off_places}:{surface.surface}:{item.evidence_id}")
        pattern = r"(?<!\w)" + re.escape(item.value.casefold()) + r"(?!\w)"
        residual = re.sub(pattern, " ", residual)
    filler = {w.casefold() for w in policy.filler_words}
    for word in re.findall(r"[^\W_]+", residual):
        if word not in filler and any(c.isalpha() for c in word):
            reasons.refuse(f"name_unbacked:{surface.surface}:{word}")


def _template(
    surface: str,
    text: str,
    template_id: Any,
    tag: bool,
    policy: FinalTextPolicy,
    reasons: _Reasons,
) -> None:
    """Refuse a template surface unless its id is registered and its bytes match."""
    registered = {**dict(policy.templates), **dict(policy.drive_templates)}
    if (
        not isinstance(template_id, str)
        or template_id not in registered
        or template_id in PARAMS
        or template_id.startswith("tag.") != tag
    ):
        reasons.refuse(f"template_unknown:{surface}:{template_id}")
    elif template_hash(text) != registered[template_id]:
        reasons.refuse(f"template_changed:{surface}:{template_id}")


def _param(
    entry: Mapping[str, Any],
    surface: str,
    policy: FinalTextPolicy,
    by_id: Mapping[str, EvidenceItem],
    ambiguous: set,
    route_ids: Sequence[str],
    reasons: _Reasons,
) -> Optional[Tuple[str, Tuple[EvidenceItem, ...]]]:
    """``(text, place evidence)`` of a parameterized template, or None if refused.

    The skeleton must hash to the registered hash, every slot value must be
    legal for its slot type, the text must be the skeleton filled, and the
    places cited must pass ``_check_backing`` like a place line's.
    """
    template_id, text = entry.get("template_id"), _text(entry.get("text"))
    template = PARAMS.get(template_id) if isinstance(template_id, str) else None
    registered = dict(policy.drive_templates)
    slots, ids = entry.get("slots"), entry.get("evidence_ids")
    if template is None or template_id not in registered:
        reasons.refuse(f"template_unknown:{surface}:{template_id}")
        return None
    skeleton = entry.get("skeleton")
    if param_hash(skeleton, template) != registered[template_id]:
        reasons.refuse(f"template_changed:{surface}:{template_id}")
        return None
    if text is None or not isinstance(slots, Mapping) or _ids(ids) is None:
        reasons.refuse(f"param_malformed:{surface}")
        return None
    if set(slots) != {s.name for s in template.slots}:
        reasons.refuse(f"param_slots:{surface}")
        return None
    rendered: Dict[str, str] = {}
    items: List[EvidenceItem] = []
    for slot in template.slots:
        value = slots[slot.name]
        if slot.type != "place":
            if valid_value(slot, value, policy.playlist_allowlist):
                rendered[slot.name] = render_value(slot, value)
            else:
                reasons.refuse(f"param_slot:{surface}:{slot.name}")
            continue
        found = (
            _resolve(surface, None, [value], _PLACE_KINDS, by_id, ambiguous, reasons)
            if isinstance(value, str) and value in ids
            else ()
        )
        if not found:
            reasons.refuse(f"param_place:{surface}:{slot.name}")
            continue
        items += found
        rendered[slot.name] = place_text(slot, found[0].value)
    if len(rendered) != len(template.slots):
        return None
    if render(skeleton, rendered) != text:
        reasons.refuse(f"param_text:{surface}")
    if set(ids) != {i.evidence_id for i in items} or len(ids) != len(items):
        reasons.refuse(f"param_evidence:{surface}")
    cited = SurfaceReview(
        surface, " ".join(i.value for i in items), tuple(items), kind=PARAM_TEMPLATE
    )
    _check_backing(cited, route_ids, "place_not_on_route", policy, reasons)
    return text, tuple(items)


def _year_backed(entry: Mapping[str, Any], recording_date: Optional[str]) -> bool:
    """True when a year tag names the recording date's year."""
    slots = entry.get("slots")
    year = slots.get("year") if isinstance(slots, Mapping) else None
    return bool(recording_date) and str(year) == str(recording_date)[:4]


def _is_drive_surface(surface: "SurfaceReview") -> bool:
    """A parameterized block or a fixed drive ``description.*`` block."""
    if surface.kind == PARAM_TEMPLATE:
        return True
    tid = surface.template_id or ""
    return tid in DRIVE_TEXT and tid.startswith("description.")


def _layout(layout: Any, surfaces: Sequence[SurfaceReview], reasons: _Reasons) -> None:
    """Refuse a description that is not exactly ``layout``'s blocks in order."""
    blocks = LAYOUTS.get(layout) if isinstance(layout, str) else None
    if blocks is None:
        reasons.refuse(f"layout_unknown:{layout}")
        return
    names = [name for name, _ in blocks]
    last, seen = -1, set()
    for s in surfaces:
        if not is_description_surface(s):
            continue
        name = (
            "chapter" if s.kind == CHAPTER else s.surface.partition("description.")[2]
        )
        ok = s.kind == CHAPTER or (
            s.kind in (TEMPLATE, PARAM_TEMPLATE)
            and str(s.template_id).split(".")[:2] == ["description", name]
        )
        if name not in names or not ok:
            reasons.refuse(f"layout_free_line:{s.surface}")
            continue
        position = names.index(name)
        if position < last or (position == last and name != "chapter"):
            reasons.refuse(f"layout_order:{s.surface}")
        last = max(last, position)
        seen.add(name)
    for name, required in blocks:
        if required and name not in seen:
            reasons.refuse(f"layout_missing:{name}")
    gap, after = LAYOUT_GAPS.get(layout, ("", frozenset()))
    if gap and (gap in seen) != bool(seen & after):
        reasons.refuse(f"layout_gap:{gap}")
    # Tags rank by TAG_ORDER (a cited place tag as PLACE_TAG); only place
    # ranks repeat.
    ids = [s.template_id or PLACE_TAG for s in surfaces if s.kind == TAG]
    order = [TAG_ORDER.index(t) if t in TAG_ORDER else -1 for t in ids]
    once = [t for t in ids if t not in REPEATABLE_TAGS]
    if -1 in order or order != sorted(order) or len(set(once)) != len(once):
        reasons.refuse("tag_order")


def _surface(
    entry: Mapping[str, Any],
    surface: str,
    position: int,
    policy: FinalTextPolicy,
    reasons: _Reasons,
) -> Optional[Tuple[str, str, Sequence[str], str, Tuple[str, ...], Optional[str]]]:
    """``(kind, text, evidence_ids, scope, segment_places, template_id)`` or None.

    A template block, or a tag naming a ``template_id``, carries no evidence:
    its id and bytes are checked here. Anything else cites its evidence.
    """
    kind = entry.get("kind")
    if kind not in VISIBLE_KINDS:
        reasons.refuse(f"surface_kind:{surface}")
        return None
    text = _text(entry.get("text"))
    if kind == TEMPLATE and entry.get("text") == "":
        text = ""  # a blank line (``description.gap``); its hash still binds it
    ids = entry.get("evidence_ids", () if kind == TEMPLATE else None)
    if text is None or not isinstance(ids, (list, tuple)):
        reasons.refuse(f"missing_field:visible[{position}]")
        return None
    template_id = entry.get("template_id")
    if kind == TEMPLATE or (kind == TAG and template_id is not None):
        if ids:
            reasons.refuse(f"template_with_evidence:{surface}")
        _template(surface, text, template_id, kind == TAG, policy, reasons)
        return kind, text, (), WHOLE, (), str(template_id)
    if kind == TAG:
        return kind, text, ids, WHOLE, (), None
    if kind == CHAPTER:
        segment = _ids(entry.get("segment_places"))
        if not segment or entry.get("scope", SEGMENT) != SEGMENT:
            reasons.refuse(f"chapter_without_window:{surface}")
            return None
        return kind, text, ids, SEGMENT, segment, None
    scope = entry.get("scope")
    segment = _ids(entry.get("segment_places")) if scope == SEGMENT else ()
    if scope not in (WHOLE, SEGMENT) or not (segment or scope == WHOLE):
        reasons.refuse(f"missing_field:visible[{position}]")
        return None
    return kind, text, ids, scope, segment or (), None


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
    contains, coverage, gaps, samples_outside, boundary_proof?}``) and ``visible``, a list of
    ``{surface, kind, text, evidence_ids, scope}``. ``kind`` is one of
    ``final_text_surfaces.VISIBLE_KINDS``; for a ``place_line`` ``scope`` is
    ``whole`` or ``segment`` and a segment also lists its ``segment_places``;
    a ``chapter`` lists its window as ``segment_places``; a ``template``, or
    a ``tag`` from a tag template, names its ``template_id`` and no evidence;
    a ``param_template`` names its ``template_id``, ``skeleton``, ``slots``
    (``name -> value``) and the evidence ids its place slots cite. An optional
    ``description_layout`` names a registered layout to enforce.
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
    regions = _regions(approved, recording_date, route_ids, by_id, ambiguous, reasons)

    surfaces: List[SurfaceReview] = []
    visible = approved.get("visible")
    if not isinstance(visible, (list, tuple)) or not visible:
        reasons.refuse("missing_field:visible")
        visible = ()
    names: set = set()
    for position, entry in enumerate(visible):
        surface = _text(entry.get("surface")) if isinstance(entry, Mapping) else None
        if surface is None:
            reasons.refuse(f"missing_field:visible[{position}]")
            continue
        if surface in _RESERVED:
            reasons.refuse(f"reserved_surface:{surface}")
            continue
        if surface in names:
            reasons.refuse(f"duplicate_surface:{surface}")
            continue
        names.add(surface)
        kind, template_id = entry.get("kind"), entry.get("template_id")
        if kind == PARAM_TEMPLATE or (kind == TAG and template_id in PARAMS):
            # A parameterized tag is sent as a tag; any other block is not.
            if str(template_id).startswith("tag.") != (kind == TAG):
                reasons.refuse(f"template_unknown:{surface}:{template_id}")
                continue
            done = _param(entry, surface, policy, by_id, ambiguous, route_ids, reasons)
            if done is None:
                continue
            if template_id == "tag.year" and not _year_backed(entry, recording_date):
                reasons.refuse(f"tag_year_unbacked:{surface}")
            surfaces.append(
                SurfaceReview(
                    surface, done[0], done[1], kind=kind, template_id=str(template_id)
                )
            )
            continue
        parsed = _surface(entry, surface, position, policy, reasons)
        if parsed is None:
            continue
        kind, text, ids, scope, segment, template_id = parsed
        items: Tuple[EvidenceItem, ...] = ()
        if template_id is None:
            if not ids:
                reasons.refuse(f"surface_without_evidence:{surface}")
            items = _resolve(
                surface, text, ids, _PLACE_KINDS, by_id, ambiguous, reasons
            )
        for place in segment:
            if place not in route_ids:
                reasons.refuse(f"route_place_unknown:{place}")
        surfaces.append(
            SurfaceReview(surface, text, items, scope, segment, kind, template_id)
        )

    segment_needed: List[str] = []
    for review_surface in surfaces:
        if review_surface.template_id is not None:
            continue
        if review_surface.kind == TAG:
            # A place tag names one place; it need not carry the whole route.
            _check_backing(
                review_surface, route_ids, "place_not_on_route", policy, reasons
            )
            continue
        if review_surface.scope == WHOLE:
            _check_backing(
                review_surface, route_ids, "place_not_on_route", policy, reasons
            )
            _check_names(
                review_surface, required, "place_dropped", regions, policy, reasons
            )
            continue
        _check_backing(
            review_surface,
            review_surface.segment_places,
            "place_not_in_segment",
            policy,
            reasons,
        )
        needed = [p for p in review_surface.segment_places if p in route_ids]
        if review_surface.kind == PLACE_LINE:
            segment_needed += [p for p in needed if p not in segment_needed]
        _check_names(
            review_surface, needed, "segment_place_dropped", regions, policy, reasons
        )

    layout = _text(approved.get("description_layout"))
    if layout is not None:
        _layout(layout, surfaces, reasons)
    elif any(_is_drive_surface(s) for s in surfaces):
        # A drive description is checked only against a named layout; without
        # one a free line, a dropped block or a reorder would pass unseen.
        reasons.refuse("layout_required")

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
    title_surface = SurfaceReview(
        TITLE_SURFACE, final_title or "", title_items, kind=TITLE_SURFACE
    )
    if decision.get("format") == "short" and segment_needed:
        _check_backing(
            title_surface, segment_needed, "place_not_in_segment", policy, reasons
        )
        _check_names(
            title_surface,
            segment_needed,
            "segment_place_dropped",
            regions,
            policy,
            reasons,
        )
    else:
        _check_backing(title_surface, route_ids, "place_not_on_route", policy, reasons)
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
    surfaces.append(
        SurfaceReview(DATE_SURFACE, recording_date or "", date_items, kind=DATE_SURFACE)
    )

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
        description_layout=layout,
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
    description: Optional[str] = None,
    tags: Optional[Sequence[str]] = None,
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
        ("stale:text_policy", body["text_policy"], _plain(asdict(text_policy))),
        ("stale:title", body["final_title"], final_title),
    ):
        if (not _text(actual) and not isinstance(actual, dict)) or expected != actual:
            found.append(code)
    reviewed = {
        s["surface"]: s["text"]
        for s in body["surfaces"]
        if s["surface"] not in _RESERVED
        # A sent description or tag is bound by its own comparison below.
        and not (description is not None and is_description_surface(s))
        and not (tags is not None and is_tag_surface(s))
    }
    if set(visible) != set(reviewed):
        found.append("stale:surfaces")
    for surface, text in sorted(reviewed.items()):
        if surface in visible and visible[surface] != text:
            found.append(f"stale:text:{surface}")
    if description is not None and description != compose_description(body["surfaces"]):
        found.append("stale:description")
    if tags is not None and list(tags) != compose_tags(body["surfaces"]):
        found.append("stale:tags")
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
    description: Optional[str] = None,
    tags: Optional[Sequence[str]] = None,
) -> None:
    """Refuse an upload unless ``review`` covers exactly what will be sent.

    ``review`` is a ``FinalTextReview`` or its persisted dict form.
    ``visible`` maps each surface (``ost``, ``thumbnail``, ``shorts_ost``, …)
    to the text the upload will show; ``source_fingerprint`` and
    ``plan_fingerprint`` are recomputed from current state, and
    ``policy_version`` is the title policy in force. When ``description`` or
    ``tags`` is given it is the text about to be sent: it must equal
    ``compose_description`` / ``compose_tags`` of the reviewed surfaces
    (``stale:description``, ``stale:tags``), and those surfaces then drop out
    of the ``visible`` comparison. Raises
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
            description,
            tags,
        )
    except (KeyError, TypeError, AttributeError) as exc:
        raise FinalTextReviewRefused([REVIEW_MALFORMED], repr(exc)) from exc
    if found:
        raise FinalTextReviewRefused(found, ", ".join(body["reasons"]))
