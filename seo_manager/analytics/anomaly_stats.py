"""Refusal-first statistics for post-level dips and spikes.

A qualification kernel, not a detector: a robust deviation (median / MAD), an
empirical tail-rank p-value, and a policy-feasibility check asked before any
outcome is inspected. Each returns a typed ``Refusal`` rather than inventing a
number it cannot compute.

It holds **no operating thresholds** — what counts as a dip, how many
references are enough, what alpha an epoch may spend is all caller policy, and
this module only reports what is attainable. Every function is pure (no clock,
no randomness, no logging, no I/O, no global state), so historical data can
replay against it to qualify the method before anything depends on it.

``1.4826`` is the only constant here, and it is a property of the estimator
(it makes the MAD consistent for sigma on normal data), not a tunable.
"""

import math
import statistics
from dataclasses import dataclass
from fractions import Fraction
from typing import List, Optional, Sequence, Union

# Refusal vocabulary. A refusal is an answer — "this method cannot speak to
# that question" — and never a degraded number.
NO_BASELINE_VARIATION = "no_baseline_variation"
INSUFFICIENT_RESOLUTION = "insufficient_resolution"
POLICY_INFEASIBLE = "policy_infeasible"

# Transform vocabulary. Counts go on a log1p scale (an extra 10 views on a post
# with 10 is not the same event as an extra 10 on a post with 10,000); rates go
# as they are. The allowlist is the guard: an unnamed transform is refused, not
# silently read as identity.
IDENTITY = "identity"
LOG1P = "log1p"

_TRANSFORMS = frozenset({IDENTITY, LOG1P})

_MAD_TO_SIGMA = 1.4826


@dataclass(frozen=True)
class Refusal:
    """A typed refusal. It deliberately has nowhere to put a statistic."""

    reason: str
    detail: str
    # Present on the two alpha-driven refusals so a caller can print WHY
    # without re-deriving the policy arithmetic.
    allocated_alpha: Optional[float] = None
    required_references: Optional[int] = None


@dataclass(frozen=True)
class RobustDeviation:
    """``median`` and ``scaled_mad`` are in transformed units; the relative
    effect is always in raw units, and is ``None`` when its denominator is 0."""

    median: float
    scaled_mad: float
    deviation: float
    relative_effect: Optional[float]
    n: int
    transform: str


@dataclass(frozen=True)
class TailRank:
    """``min_attainable_p`` is the method's floor at this ``n``: no target,
    however extreme, can produce a smaller two-sided p."""

    p_upper: float
    p_lower: float
    p_two_sided: float
    n: int
    min_attainable_p: float
    ties: int


@dataclass(frozen=True)
class Feasible:
    allocated_alpha: float
    required_references: int
    registered_tests: int
    reference_cap: int


def _finite(value: float, label: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number; got {value!r}.")
    return number


def _sorted_reference(values: Sequence[float]) -> List[float]:
    numbers = [_finite(v, "every reference value") for v in values]
    if not numbers:
        raise ValueError("The reference is empty; there is nothing to compare against.")
    # Sorted here so no result can depend on the order the caller happened to
    # hand the references in.
    return sorted(numbers)


def _alpha(value: float, label: str = "alpha") -> float:
    alpha = _finite(value, label)
    if not 0.0 < alpha <= 1.0:
        raise ValueError(
            f"{label} must be greater than 0 and at most 1; got {alpha!r}."
        )
    return alpha


def _whole(value: int, label: str, minimum: int) -> int:
    if int(value) != value or value < minimum:
        raise ValueError(f"{label} must be a whole number >= {minimum}; got {value!r}.")
    return int(value)


def _transformed(value: float, transform: str) -> float:
    if transform == IDENTITY:
        return value
    if value <= -1.0:
        raise ValueError(
            f"log1p is undefined at {value!r}; a count transform needs values > -1."
        )
    return math.log1p(value)


def _require_transform(transform: str) -> str:
    if transform not in _TRANSFORMS:
        raise ValueError(
            f"Unknown transform {transform!r}; expected one of {sorted(_TRANSFORMS)}."
        )
    return transform


def robust_deviation(
    reference: Sequence[float],
    target: float,
    *,
    transform: str = IDENTITY,
) -> Union[RobustDeviation, Refusal]:
    """How far ``target`` sits from the reference, in the reference's own units.

    Returns ``NO_BASELINE_VARIATION`` when the scaled MAD is 0. No epsilon is
    substituted and no weaker method is tried: a reference with no spread
    cannot say how unusual anything is, and the returned type has nowhere to
    put the number an epsilon would have manufactured.
    """
    _require_transform(transform)
    raw = _sorted_reference(reference)
    raw_target = _finite(target, "target")

    transformed = [_transformed(value, transform) for value in raw]
    middle = statistics.median(transformed)
    scaled_mad = _MAD_TO_SIGMA * statistics.median(
        [abs(value - middle) for value in transformed]
    )
    if scaled_mad == 0.0:
        return Refusal(
            NO_BASELINE_VARIATION,
            "The reference has no spread (its MAD is 0), so a deviation has no "
            "unit to be measured in.",
        )

    raw_middle = statistics.median(raw)
    return RobustDeviation(
        median=middle,
        scaled_mad=scaled_mad,
        deviation=(_transformed(raw_target, transform) - middle) / scaled_mad,
        # A zero reference median makes the ratio undefined, not 0 and not
        # infinite. Only a separately qualified absolute-effect policy may
        # proceed from here.
        relative_effect=(
            None if raw_middle == 0 else (raw_target - raw_middle) / raw_middle
        ),
        n=len(raw),
        transform=transform,
    )


def min_references_for(allocated_alpha: float) -> int:
    """The references the two-sided rank method needs to reach ``allocated_alpha``.

    ``ceil(2 / alpha) - 1``, computed over exact rationals so the policy
    boundary is not decided by a float rounding artifact.
    """
    alpha = _alpha(allocated_alpha, "allocated_alpha")
    return math.ceil(Fraction(2) / Fraction(alpha)) - 1


def tail_rank_p(
    reference: Sequence[float],
    target: float,
    *,
    allocated_alpha: float,
) -> Union[TailRank, Refusal]:
    """An empirical two-sided tail rank of ``target`` against ``reference``.

    Ties count conservatively — toward both tails — which the ``>=`` / ``<=``
    below already does. Returns ``INSUFFICIENT_RESOLUTION`` when ``n`` is too
    small for the method's own floor, ``2 / (n + 1)``, to meet the allocated
    alpha: a small p there would be an artifact of the reference size, not
    evidence.
    """
    alpha = _alpha(allocated_alpha, "allocated_alpha")
    values = _sorted_reference(reference)
    point = _finite(target, "target")
    n = len(values)

    required = min_references_for(alpha)
    if n < required:
        return Refusal(
            INSUFFICIENT_RESOLUTION,
            f"{n} reference(s) put the smallest attainable two-sided p at "
            f"2/{n + 1}, which cannot meet an allocated alpha of {alpha}; "
            f"{required} would be needed.",
            allocated_alpha=alpha,
            required_references=required,
        )

    p_upper = (1 + sum(1 for value in values if value >= point)) / (n + 1)
    p_lower = (1 + sum(1 for value in values if value <= point)) / (n + 1)
    return TailRank(
        p_upper=p_upper,
        p_lower=p_lower,
        p_two_sided=min(1.0, 2 * min(p_upper, p_lower)),
        n=n,
        min_attainable_p=2.0 / (n + 1),
        ties=sum(1 for value in values if value == point),
    )


def feasibility(
    total_alpha: float,
    registered_tests: int,
    reference_cap: int,
) -> Union[Feasible, Refusal]:
    """Can this policy ever produce a confirmable result?

    Asked before outcomes are inspected. A policy whose own cap forbids the
    evidence it demands is rejected as ``POLICY_INFEASIBLE`` rather than left
    waiting forever.
    """
    alpha = _alpha(total_alpha, "total_alpha")
    tests = _whole(registered_tests, "registered_tests", 1)
    cap = _whole(reference_cap, "reference_cap", 0)

    allocated = alpha / tests
    required = min_references_for(allocated)
    if required > cap:
        return Refusal(
            POLICY_INFEASIBLE,
            f"{tests} registered test(s) splitting a total alpha of {alpha} "
            f"allocate {allocated} each, which needs {required} references — "
            f"more than the cap of {cap}.",
            allocated_alpha=allocated,
            required_references=required,
        )
    return Feasible(
        allocated_alpha=allocated,
        required_references=required,
        registered_tests=tests,
        reference_cap=cap,
    )
