"""Opportunity contracts. See ``docs/opportunities.md``."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from seo_manager.collection.contracts import Observation

STATUSES = ("open", "needs_evidence")


@dataclass(frozen=True)
class VideoText:
    """The current public text of one video."""

    title: str
    description: str


@dataclass(frozen=True)
class EvidenceRef:
    """A pointer to one stored observation, enough to re-read it."""

    source: str
    subject: str
    metric: str
    window_start: datetime
    window_end: datetime
    reported_at: datetime

    @classmethod
    def from_observation(cls, obs: Observation) -> EvidenceRef:
        return cls(
            obs.source,
            obs.subject,
            obs.metric,
            obs.window_start,
            obs.window_end,
            obs.reported_at,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "subject": self.subject,
            "metric": self.metric,
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "reported_at": self.reported_at.isoformat(),
        }


@dataclass(frozen=True)
class Opportunity:
    """One reviewable finding about one asset.

    ``priority`` is a versioned ordering key set by product policy
    (``policy_version``). It is not a confidence, a probability or a predicted
    gain, and must never be shown as one. ``None`` means unranked.
    ``status`` is ``needs_evidence`` when ``missing_evidence`` blocks a review.
    """

    id: str
    tenant: str
    rule: str
    status: str
    asset: str
    observed: str
    evidence: tuple[EvidenceRef, ...]
    window_start: datetime
    window_end: datetime
    proposed_change: str
    priority: float | None
    priority_reasons: tuple[str, ...]
    effort: str
    risk: str
    automation: str
    validation: str
    next_evaluation: datetime
    missing_evidence: tuple[str, ...]
    policy_version: str

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        if self.next_evaluation.tzinfo is None:
            raise ValueError("next_evaluation must be timezone-aware")

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe form: datetimes as ISO strings, tuples as lists."""
        return {
            "id": self.id,
            "tenant": self.tenant,
            "rule": self.rule,
            "status": self.status,
            "asset": self.asset,
            "observed": self.observed,
            "evidence": [e.as_dict() for e in self.evidence],
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "proposed_change": self.proposed_change,
            "priority": self.priority,
            "priority_reasons": list(self.priority_reasons),
            "effort": self.effort,
            "risk": self.risk,
            "automation": self.automation,
            "validation": self.validation,
            "next_evaluation": self.next_evaluation.isoformat(),
            "missing_evidence": list(self.missing_evidence),
            "policy_version": self.policy_version,
        }
