"""How old the data behind a recommendation is.

A recommendation is only as fresh as the samples it was computed from.
`generate_report` stamping `datetime.now()` under the word "Generated:"
measures when a report was *printed*, not when anything was *learned*: an
account that stopped reporting in June still produced a "best" style under
today's date, and a reader had no way to tell. That is this codebase's
recurring defect -- a fresh timestamp read as fresh data.

The rule these types encode:

- A recommendation travels with its evidence age and sample count, in one
  frozen value, so no caller can print a "best" without having the age in
  hand. A warning bolted onto the report is a late gate a future caller
  skips; a type that cannot hold a winner without its age is not.
- Stale evidence is **labelled, never dropped and never replaced**. Past the
  threshold the last VALID recommendation still comes back, marked. Refusing
  to answer and inventing a fresh answer are both wrong.
- "Nothing measured" is its own outcome, distinct from a measured zero and
  from an arbitrary first entry -- the same rule `metric_source` applies to
  an unmeasured rate.
- An age that was never recorded is `UNDATED`, never `FRESH`. An unknown
  dressed as a measurement is the defect, one field over.

One home for the threshold and the arithmetic: two modules each carrying
their own copy are two code paths that disagree the moment one is edited.
"""

from collections.abc import Iterable
from datetime import datetime
from enum import Enum

STALE_AFTER_DAYS = 30.0
"""Evidence newer than this is FRESH, older is STALE -- and still reported.

A policy constant, not a finding: it matches the monthly cadence of the
analytics syncs that feed these trackers. Nothing in this service measured
what the right number is, so it is stated here rather than computed and
presented as if something had.
"""


class EvidenceState(Enum):
    """What is known about the age of the samples behind a value."""

    NOTHING_MEASURED = "nothing_measured"  # no admissible sample at all
    INSUFFICIENT = "insufficient"  # samples exist, too few to name a winner
    FRESH = "fresh"  # newest sample within STALE_AFTER_DAYS
    STALE = "stale"  # older -- still the last valid answer, labelled
    UNDATED = "undated"  # samples carry no timestamp; age unknown, not 0


def age_days(timestamp: str | None, now: datetime | None = None) -> float | None:
    """Days between an ISO-8601 timestamp and now, or None if unknowable."""
    if not timestamp:
        return None
    try:
        then = datetime.fromisoformat(timestamp)
    except (TypeError, ValueError):
        return None
    return ((now or datetime.now()) - then).total_seconds() / 86400.0


def newest(timestamps: Iterable[str | None]) -> str | None:
    """The latest parseable ISO-8601 stamp in the batch, or None."""
    dated = []
    for stamp in timestamps:
        if not stamp:
            continue
        try:
            dated.append((datetime.fromisoformat(stamp), stamp))
        except (TypeError, ValueError):
            continue
    return max(dated)[1] if dated else None


def classify(age: float | None) -> EvidenceState:
    """FRESH / STALE / UNDATED for evidence that exists. Never invents an age."""
    if age is None:
        return EvidenceState.UNDATED
    return EvidenceState.STALE if age > STALE_AFTER_DAYS else EvidenceState.FRESH


def describe(timestamp: str | None, age: float | None) -> str:
    """One human-readable phrase for an evidence age, for reports."""
    if timestamp is None or age is None:
        return "last updated: not recorded (age unknown)"
    return f"last updated: {timestamp} ({age:.1f} days old)"


def stale_reason(age: float | None, sample_size: int) -> str | None:
    """Why a recommendation is labelled, or None when it is fresh."""
    state = classify(age)
    if state is EvidenceState.STALE:
        return (
            f"stale evidence: the newest of {sample_size} samples is "
            f"{age:.1f} days old (threshold {STALE_AFTER_DAYS:.0f}); this is "
            "the last valid recommendation, not a new measurement"
        )
    if state is EvidenceState.UNDATED:
        return (
            f"evidence age unknown: none of the {sample_size} samples carries "
            "a last_updated timestamp"
        )
    return None
