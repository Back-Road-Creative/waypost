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
"""

import hashlib
import json
from dataclasses import asdict, dataclass, fields
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

REVIEW_VERSION = "final-text-review-1"

AGREE = "agree"
DISAGREE = "disagree"
REFUSED = "refused"

LOCATION = "location"
DATE = "date"

TITLE_SURFACE = "title"
DATE_SURFACE = "recording_date"
_RESERVED = frozenset({TITLE_SURFACE, DATE_SURFACE})

TITLE_CHANGED = "title_changed_after_approval"


class FinalTextReviewRefused(ValueError):
    """``verify_review`` will not pass this upload; ``str(exc)`` says why."""


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


@dataclass(frozen=True)
class FinalTextReview:
    review_version: str
    verdict: str
    reasons: Tuple[str, ...]
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


def _fingerprint(review: FinalTextReview) -> str:
    body = review_to_dict(review)
    body.pop("fingerprint")
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _text(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


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
    text: str,
    ids: Sequence[str],
    kind: str,
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
        if item.kind != kind:
            reasons.refuse(f"evidence_kind:{evidence_id}")
        elif kind == LOCATION and item.value.casefold() not in text.casefold():
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


def review_final_text(
    approved: Mapping[str, Any],
    decision: Mapping[str, Any],
    evidence: Sequence[Mapping[str, Any]],
) -> FinalTextReview:
    """Review ``approved`` text against the final title in ``decision``.

    ``approved``: ``approval_hash``, ``title`` (the title the operator
    approved), ``recording_date``, ``date_evidence_id`` and ``visible``, a
    list of ``{surface, text, evidence_ids}``. ``decision``: the dict form of
    a ``TitleDecision``. ``evidence``: ``{evidence_id, kind, value, source}``
    items, ``kind`` being ``location`` or ``date``.
    """
    reasons = _Reasons()
    by_id, ambiguous = _index_evidence(evidence, reasons)

    approval_hash = _text(approved.get("approval_hash"))
    approved_title = _text(approved.get("title"))
    recording_date = _text(approved.get("recording_date"))
    for name, value in (
        ("approval_hash", approval_hash),
        ("title", approved_title),
        ("recording_date", recording_date),
    ):
        if value is None:
            reasons.refuse(f"missing_field:{name}")

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
        if surface is None or text is None or not isinstance(ids, (list, tuple)):
            reasons.refuse(f"missing_field:visible[{position}]")
            continue
        if surface in _RESERVED:
            reasons.refuse(f"reserved_surface:{surface}")
            continue
        if surface in names:
            reasons.refuse(f"duplicate_surface:{surface}")
            continue
        names.add(surface)
        if not ids:
            reasons.refuse(f"surface_without_evidence:{surface}")
        items = _resolve(surface, text, ids, LOCATION, by_id, ambiguous, reasons)
        surfaces.append(SurfaceReview(surface, text, items))

    final_title, claims = _chosen(decision, reasons)
    title_items = _resolve(
        TITLE_SURFACE, final_title or "", claims, LOCATION, by_id, ambiguous, reasons
    )
    surfaces.append(SurfaceReview(TITLE_SURFACE, final_title or "", title_items))

    date_id = _text(approved.get("date_evidence_id"))
    date_items: Tuple[EvidenceItem, ...] = ()
    if date_id is None:
        reasons.refuse("date_without_evidence")
    else:
        date_items = _resolve(
            DATE_SURFACE, "", (date_id,), DATE, by_id, ambiguous, reasons
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
        approval_hash=approval_hash,
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


def verify_review(
    review: FinalTextReview, *, final_title: str, visible: Mapping[str, str]
) -> None:
    """Refuse an upload whose title or visible text is not what was reviewed.

    ``visible`` maps each surface (``ost``, ``thumbnail``, ``shorts_ost``, …)
    to the text the upload will show. Passing this is not an approval: the
    caller's human gate still applies.
    """
    if review.fingerprint != _fingerprint(review):
        raise FinalTextReviewRefused("review fingerprint does not match its fields")
    if review.verdict != AGREE:
        raise FinalTextReviewRefused(
            f"review verdict is {review.verdict}: {', '.join(review.reasons)}"
        )
    if final_title != review.final_title:
        raise FinalTextReviewRefused(
            f"final title {final_title!r} is not the reviewed title "
            f"{review.final_title!r}"
        )
    reviewed = {
        s.surface: s.text for s in review.surfaces if s.surface not in _RESERVED
    }
    if set(visible) != set(reviewed):
        raise FinalTextReviewRefused(
            f"visible surfaces {sorted(visible)} are not the reviewed surfaces "
            f"{sorted(reviewed)}"
        )
    for surface, text in sorted(reviewed.items()):
        if visible[surface] != text:
            raise FinalTextReviewRefused(
                f"{surface} text {visible[surface]!r} is not the reviewed text {text!r}"
            )
