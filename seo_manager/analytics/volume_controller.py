"""Pure volume and cadence controller.

``decide(evidence, states, now)`` returns one ``Decision`` per knob state: shorts
per LF, LF days per region, shorts posting times. At most ONE knob moves per call,
by ONE step; everything else holds with a reason code from a fixed vocabulary.
No I/O, no clock, no database: the nightly runner that reads evidence, persists
decisions and prints ``VOLUME_DECISION`` lines is a separate unit.

How a knob decides (all numbers are named rails in ``volume_policy``):

1. Blind: analytics not ok, or no settled short for ``BLIND_DAYS`` -> one step
   toward ``last_validated``, never past it.
2. Lockout after a rollback -> ``ROLLBACK_LOCKOUT``.
3. Probation: every applied change is watched. A post-change cohort of at least
   ``PROBATION_MIN_N`` whose median falls more than ``PROBATION_MAD_MULTIPLE``
   scaled MADs below the baseline (``robust_deviation``) rolls the change back;
   otherwise the value becomes ``last_validated``.
4. Cooldown, then the marginal-tail rule (the video pipeline's
   volume rule): the bottom ``TAIL_K`` shorts each under
   ``DOWN_FRACTION`` x median step down; each at or over ``UP_FRACTION`` x median
   step up, but only on ``UP_MIN_N`` evidence and a second, distinct cohort.

``shorts_times`` (per region, a list of ``"HH:MM"``) takes the same gates but its
rule is a swap, not a tail: the weakest measured top-of-hour window is replaced by
the best measured candidate hour (``scheduling.shorts_times``) when that hour's
median day-7 views sit ``PROBATION_MAD_MULTIPLE`` scaled MADs above the window's.
A rollback or blind revert restores ``last_validated`` wholesale (one swap away).
A ``shorts_times`` state with no region or a non-list value just holds.

A statistics ``Refusal`` is never turned into a number: it becomes the hold's
reason code. Every hold has a code; there is no default value.

``Decision.state_patch`` is the new ``KnobState`` fields a decision implies;
``apply_decision`` applies it, so a caller (and the tests) can chain decisions.
"""

import statistics
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from seo_manager.scheduling import shorts_times as times

from . import volume_policy as policy
from .anomaly_stats import LOG1P, Refusal, robust_deviation, tail_rank_p

# Reason codes (the fixed vocabulary; ANALYTICS_<status> is built per row).
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
CONFIRMING = "CONFIRMING"
COOLDOWN = "COOLDOWN"
IN_DEADBAND = "IN_DEADBAND"
AT_FLOOR = "AT_FLOOR"
AT_CEILING = "AT_CEILING"
JOINT_CEILING = "JOINT_CEILING"
LEARNED_CEILING = "LEARNED_CEILING"
ROLLBACK_LOCKOUT = "ROLLBACK_LOCKOUT"
SUPPLY_BOUND = "SUPPLY_BOUND"
NO_BASELINE_VARIATION = "NO_BASELINE_VARIATION"
STATE_UNREADABLE = "STATE_UNREADABLE"
HOUR_SWAP = "HOUR_SWAP"

HOLD = "hold"
STEP = "step"
ROLLBACK = "rollback"
BLIND_REVERT = "blind_revert"
VALIDATED = "validated"

_MOVES = {ROLLBACK: 0, BLIND_REVERT: 1, STEP: 2}
_DAY = timedelta(days=1)


@dataclass(frozen=True)
class ShortOutcome:
    video_id: str
    posted_at: datetime
    region: Optional[str]
    weekday: int
    hour_local: int
    day7_views: float
    source: str


@dataclass(frozen=True)
class Evidence:
    shorts: Sequence[ShortOutcome]
    analytics_status: str
    as_of: datetime
    ready_depth: Optional[int] = None
    # Per-region ready LF drives; an ``lf_days`` knob reads its own region's depth
    # and falls back to ``ready_depth`` for a region not listed.
    ready_depth_by_region: Mapping[str, int] = field(default_factory=dict)
    # Per-region clock hours a ``shorts_times`` swap may not take: the hours another
    # region posts shorts at. Long-form publish hours are not fenced.
    fenced_hours: Mapping[str, Sequence[int]] = field(default_factory=dict)


@dataclass(frozen=True)
class KnobState:
    knob: str
    region: Optional[str]
    value: Any
    last_change_at: Optional[datetime] = None
    last_validated: Any = None
    probation_open_since: Optional[datetime] = None
    rollback_count: int = 0
    learned_ceiling: Optional[int] = None
    learned_ceiling_until: Optional[datetime] = None
    lockout_until: Optional[datetime] = None
    cohort_cutoff: Optional[datetime] = None
    pending_confirm: Optional[datetime] = None


@dataclass(frozen=True)
class Decision:
    knob: str
    region: Optional[str]
    action: str
    prev: Any
    value: Any
    reason_code: str
    basis: str
    evidence_n: int
    need_n: int
    floor: Optional[int]
    ceiling: Optional[int]
    evidence_window: Optional[Tuple[datetime, datetime]]
    evidence_json: Dict[str, Any]
    next_eval: datetime
    state_patch: Dict[str, Any] = field(default_factory=dict)


def apply_decision(state: KnobState, decision: Decision) -> KnobState:
    """The state after ``decision``. Pure; the caller persists it."""
    return replace(state, **decision.state_patch)


def _bounds(knob: str) -> Tuple[Optional[int], Optional[int]]:
    if knob == "shorts_per_lf":
        return policy.SHORTS_FLOOR, policy.SHORTS_CEILING
    if knob == "lf_days":
        return policy.LF_DAYS_FLOOR, policy.LF_DAYS_CEILING_PER_REGION
    return None, None


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _readable(s: Any) -> Optional[str]:
    """None when the state is usable, else why it is not."""
    if not isinstance(s, KnobState):
        return "not a KnobState"
    if s.knob not in policy.KNOBS:
        return f"unknown knob {s.knob!r}"
    floor, ceiling = _bounds(s.knob)
    if floor is None or ceiling is None:
        return _times_unreadable(s)
    for label, v in (("value", s.value), ("last_validated", s.last_validated)):
        if v is None and label == "last_validated":
            continue
        if not _is_int(v) or not floor <= v <= ceiling:
            return f"{label} {v!r} outside [{floor}, {ceiling}]"
    if s.probation_open_since is not None and (
        s.last_change_at is None or s.last_validated is None
    ):
        return "probation open without last_change_at and last_validated"
    return None


def _is_times(s: KnobState) -> bool:
    """A per-region ``shorts_times`` list: the only shape the swap rule decides."""
    return (
        s.knob == "shorts_times" and s.region is not None and isinstance(s.value, list)
    )


def _times_unreadable(s: KnobState) -> Optional[str]:
    if not _is_times(s):
        return None
    try:
        times.parse_windows(s.value)
        if s.last_validated is not None:
            times.parse_windows(s.last_validated)
    except ValueError as exc:
        return str(exc)
    if s.probation_open_since is not None and (
        s.last_change_at is None or s.last_validated is None
    ):
        return "probation open without last_change_at and last_validated"
    return None


@dataclass(frozen=True)
class _Ctx:
    now: datetime
    evidence: Evidence
    shorts_value: int
    lf_total: int


def _dec(
    s: KnobState,
    ctx: _Ctx,
    action: str,
    value: Any,
    code: str,
    basis: str,
    n: int = 0,
    need: int = 0,
    window: Optional[Tuple[datetime, datetime]] = None,
    ev: Optional[Dict[str, Any]] = None,
    patch: Optional[Dict[str, Any]] = None,
    nxt: Optional[datetime] = None,
) -> Decision:
    lo, hi = _bounds(s.knob)
    head = (s.knob, s.region, action, s.value, value, code, basis, n, need, lo, hi)
    return Decision(*head, window, ev or {}, nxt or ctx.now + _DAY, patch or {})


def _hold(s: KnobState, ctx: _Ctx, code: str, basis: str, **kw: Any) -> Decision:
    return _dec(s, ctx, HOLD, s.value, code, basis, **kw)


def _move(
    s: KnobState,
    ctx: _Ctx,
    action: str,
    code: str,
    basis: str,
    value: Any,
    patch: Dict[str, Any],
    **kw: Any,
) -> Decision:
    full = {"value": value, "last_change_at": ctx.now, **patch}
    return _dec(s, ctx, action, value, code, basis, patch=full, **kw)


def _raise_blocked(s: KnobState, new: int, ctx: _Ctx) -> Optional[Tuple[str, str]]:
    """Why raising ``s`` to ``new`` is refused, or None."""
    _, ceiling = _bounds(s.knob)
    if ceiling is not None and new > ceiling:
        return AT_CEILING, f"{s.knob} {new} is above its ceiling {ceiling}"
    if s.knob == "shorts_per_lf":
        weekly = new * ctx.lf_total
    else:
        total = ctx.lf_total + (new - s.value)
        if total > policy.LF_DAYS_CEILING_PER_WEEK:
            cap = policy.LF_DAYS_CEILING_PER_WEEK
            return JOINT_CEILING, f"{total} LF days/week is above {cap}"
        weekly = total * ctx.shorts_value
    if weekly > policy.JOINT_WEEKLY_SHORTS_CEILING:
        cap = policy.JOINT_WEEKLY_SHORTS_CEILING
        return JOINT_CEILING, f"{weekly} shorts/week is above {cap}"
    return None


def _settled(s: KnobState, ctx: _Ctx) -> List[ShortOutcome]:
    cutoff = ctx.now - timedelta(days=policy.SETTLE_DAYS)
    return [
        x
        for x in ctx.evidence.shorts
        if x.posted_at <= cutoff and (s.region is None or x.region == s.region)
    ]


def _step_toward(value: int, target: int) -> int:
    return value + (1 if target > value else -1)


def _blind(s: KnobState, settled: List[ShortOutcome], ctx: _Ctx) -> Optional[Decision]:
    status = ctx.evidence.analytics_status
    code = None if status == "ok" else f"ANALYTICS_{status.upper()}"
    lv = s.last_validated
    if code is None and lv is not None and lv != s.value:
        newest = max((x.posted_at for x in settled), default=s.last_change_at)
        if newest is not None and ctx.now - newest > timedelta(days=policy.BLIND_DAYS):
            code = "ANALYTICS_STALE"
    if code is None:
        return None
    if lv is None or lv == s.value:
        return _hold(s, ctx, code, f"analytics blind ({code}); nothing to revert")
    new = lv if _is_times(s) else _step_toward(s.value, lv)
    blocked = _raise_blocked(s, new, ctx) if _is_int(new) and new > s.value else None
    if blocked:
        return _hold(s, ctx, blocked[0], f"blind revert refused: {blocked[1]}")
    patch = {"probation_open_since": None, "pending_confirm": None}
    basis = f"blind ({code}): one step toward last_validated {lv}"
    return _move(s, ctx, BLIND_REVERT, code, basis, new, patch)


def _probation(s: KnobState, settled: List[ShortOutcome], ctx: _Ctx) -> Decision:
    assert s.last_change_at is not None  # _readable guarantees it
    need = policy.PROBATION_MIN_N
    post = [x for x in settled if x.posted_at > s.last_change_at]
    base = [x for x in settled if x.posted_at <= s.last_change_at]
    for label, rows in (("post-change", post), ("baseline", base)):
        if len(rows) < need:
            basis = f"probation open: {len(rows)} settled {label} shorts, need {need}"
            return _hold(s, ctx, INSUFFICIENT_EVIDENCE, basis, n=len(rows), need=need)
    post_median = statistics.median(x.day7_views for x in post)
    base_views = [x.day7_views for x in base]
    window = (min(x.posted_at for x in post), max(x.posted_at for x in post))
    dev = robust_deviation(base_views, post_median, transform=LOG1P)
    if isinstance(dev, Refusal):
        kw = {"n": len(post), "need": need, "window": window}
        return _hold(s, ctx, dev.reason.upper(), dev.detail, **kw)
    tail = tail_rank_p(base_views, post_median, allocated_alpha=policy.DOWN_ALPHA)
    ev: Dict[str, Any] = {
        "post_median": post_median,
        "baseline_median_log1p": dev.median,
        "scaled_mad_log1p": dev.scaled_mad,
        "deviation": dev.deviation,
        "p_lower": None if isinstance(tail, Refusal) else tail.p_lower,
        "baseline_n": len(base),
    }
    kw = {"n": len(post), "need": need, "window": window, "ev": ev}
    if dev.deviation >= -policy.PROBATION_MAD_MULTIPLE:
        patch = {
            "last_validated": s.value,
            "probation_open_since": None,
            "cohort_cutoff": window[1],
            "rollback_count": 0,
        }
        basis = "post-change cohort held its baseline"
        done = _hold(s, ctx, "VALIDATED", basis, patch=patch, **kw)
        return replace(done, action=VALIDATED)
    if _is_times(s):
        new = s.last_validated
    elif s.last_validated != s.value:
        new = _step_toward(s.value, s.last_validated)
    else:
        new = s.value
    blocked = _raise_blocked(s, new, ctx) if _is_int(new) and new > s.value else None
    if blocked:
        return _hold(s, ctx, blocked[0], f"rollback refused: {blocked[1]}", ev=ev)
    count = s.rollback_count + 1
    days = min(policy.COOLDOWN_DAYS[s.knob] * 2**count, policy.LOCKOUT_CAP_DAYS)
    patch: Dict[str, Any] = {
        "probation_open_since": None,
        "pending_confirm": None,
        "rollback_count": count,
        "lockout_until": ctx.now + timedelta(days=days),
        "cohort_cutoff": window[1],
    }
    if (
        _is_int(new) and s.value > new
    ):  # an up-step failed: remember its value as a ceiling
        patch["learned_ceiling"] = s.value
        until = ctx.now + timedelta(days=policy.LEARNED_CEILING_DAYS)
        patch["learned_ceiling_until"] = until
    ev["lockout_days"] = days
    limit = policy.PROBATION_MAD_MULTIPLE
    basis = (
        f"post-change median {post_median} is {dev.deviation:.2f} scaled MAD from "
        f"baseline (limit -{limit}); back to {new}"
    )
    return _move(s, ctx, ROLLBACK, "PROBATION_FAILED", basis, new, patch, **kw)


def _evaluate(s: KnobState, ctx: _Ctx) -> Decision:
    settled = _settled(s, ctx)
    if s.knob == "shorts_times" and not _is_times(s):
        basis = "shorts_times has no decision rule without a region and a list value"
        kw = {"n": len(settled), "need": policy.PROBATION_MIN_N}
        return _hold(s, ctx, INSUFFICIENT_EVIDENCE, basis, **kw)
    blind = _blind(s, settled, ctx)
    if blind is not None:
        return blind
    if s.lockout_until is not None and ctx.now < s.lockout_until:
        basis = f"locked out until {s.lockout_until.isoformat()}"
        return _hold(s, ctx, ROLLBACK_LOCKOUT, basis, nxt=s.lockout_until)
    if s.probation_open_since is not None:
        return _probation(s, settled, ctx)
    if s.last_change_at is not None:
        free_at = s.last_change_at + timedelta(days=policy.COOLDOWN_DAYS[s.knob])
        if ctx.now < free_at:
            basis = f"last change {s.last_change_at.isoformat()}"
            return _hold(s, ctx, COOLDOWN, basis, nxt=free_at)
    if _is_times(s):
        return _times_rule(s, settled, ctx)
    return _tail_rule(s, settled, ctx)


def _times_rule(s: KnobState, settled: List[ShortOutcome], ctx: _Ctx) -> Decision:
    """One swap: the weakest measured current hour for the best measured candidate."""
    need = policy.PROBATION_MIN_N
    windows = times.parse_windows(s.value)  # _readable guarantees it
    mine = sorted(h for h, m in windows if m == 0)  # only top-of-hour windows move
    fenced = ctx.evidence.fenced_hours.get(s.region or "", ())
    cands = [h for h in times.candidate_hours(fenced) if h not in mine]
    ranked = times.rank_hours(settled, need)
    score = {r.hour: r for r in ranked}
    ev: Dict[str, Any] = {"ranked": times.ranked_json(ranked), "candidates": cands}
    weak = [score[h] for h in mine if h in score]
    best = [score[h] for h in cands if h in score]
    if not weak or not best:
        pool = mine if not weak else cands
        n = max((sum(1 for x in settled if x.hour_local == h) for h in pool), default=0)
        what = "a current top-of-hour window" if not weak else "a candidate hour"
        basis = f"{n} settled shorts at the best-sampled {what}, need {need}"
        return _hold(s, ctx, INSUFFICIENT_EVIDENCE, basis, n=n, need=need, ev=ev)
    out = min(weak, key=lambda r: (r.score, r.hour))
    inn = min(best, key=lambda r: (-r.score, r.hour))
    window = (
        min(x.posted_at for x in settled),
        max(x.posted_at for x in settled),
    )
    kw: Dict[str, Any] = {"n": inn.n, "need": need, "window": window, "ev": ev}
    ref = [x.day7_views for x in settled if x.hour_local == out.hour]
    dev = robust_deviation(ref, inn.median, transform=LOG1P)
    if isinstance(dev, Refusal):
        return _hold(s, ctx, dev.reason.upper(), dev.detail, **kw)
    ev.update(out_hour=out.hour, in_hour=inn.hour, deviation=dev.deviation)
    limit = policy.PROBATION_MAD_MULTIPLE
    if dev.deviation < limit:
        basis = (
            f"best candidate {inn.hour:02d}:00 is {dev.deviation:.2f} scaled MAD above "
            f"the weakest window {out.hour:02d}:00 (needs {limit})"
        )
        return _hold(s, ctx, IN_DEADBAND, basis, **kw)
    new = sorted(
        [w for w in s.value if w != times.hhmm(out.hour)] + [times.hhmm(inn.hour)]
    )
    patch = {"probation_open_since": ctx.now}
    if s.last_validated is None:
        patch["last_validated"] = s.value
    basis = (
        f"swap {out.hour:02d}:00 for {inn.hour:02d}:00: median day-7 views "
        f"{inn.median:.0f} is {dev.deviation:.2f} scaled MAD above the weakest "
        f"window's (limit {limit})"
    )
    return _move(s, ctx, STEP, HOUR_SWAP, basis, new, patch, **kw)


def _tail_rule(s: KnobState, settled: List[ShortOutcome], ctx: _Ctx) -> Decision:
    floor, _ = _bounds(s.knob)
    assert floor is not None
    start = s.pending_confirm or s.cohort_cutoff
    cohort = [x for x in settled if start is None or x.posted_at > start]
    n, need = len(cohort), policy.DOWN_MIN_N
    if n < need:
        basis = f"{n} settled shorts in this cohort, need {need}"
        return _hold(s, ctx, INSUFFICIENT_EVIDENCE, basis, n=n, need=need)
    views = sorted((x.day7_views for x in cohort), reverse=True)
    window = (min(x.posted_at for x in cohort), max(x.posted_at for x in cohort))
    median = statistics.median(views)
    if median <= 0:
        basis = "median day-7 views is 0"
        return _hold(
            s, ctx, NO_BASELINE_VARIATION, basis, n=n, need=need, window=window
        )
    bottom = views[-policy.TAIL_K :]
    down_at, up_at = median * policy.DOWN_FRACTION, median * policy.UP_FRACTION
    ev: Dict[str, Any] = {
        "median": median,
        "bottom": bottom,
        "down_below": down_at,
        "up_at_or_above": up_at,
        "as_of": ctx.evidence.as_of.isoformat(),
    }
    kw = {"n": n, "need": need, "window": window, "ev": ev}
    clear = {"pending_confirm": None} if s.pending_confirm is not None else {}
    k, up = policy.TAIL_K, policy.UP_FRACTION
    if all(v < down_at for v in bottom):
        if s.value <= floor:
            basis = f"bottom {k} {bottom} under {down_at:.0f}; already at floor {floor}"
            return _hold(s, ctx, AT_FLOOR, basis, patch=clear, **kw)
        patch = {"probation_open_since": ctx.now, "pending_confirm": None}
        if s.last_validated is None:
            patch["last_validated"] = s.value
        basis = (
            f"bottom {k} {bottom} each under {policy.DOWN_FRACTION:.0%} of median "
            f"{median:.0f} (under {down_at:.0f})"
        )
        return _move(s, ctx, STEP, "TAIL_DOWN", basis, s.value - 1, patch, **kw)
    if not all(v >= up_at for v in bottom):
        basis = "bottom shorts sit between the down and up bands"
        return _hold(s, ctx, IN_DEADBAND, basis, patch=clear, **kw)
    kw["need"] = policy.UP_MIN_N
    if n < policy.UP_MIN_N:
        basis = f"up needs {policy.UP_MIN_N} settled shorts in the cohort, have {n}"
        return _hold(s, ctx, INSUFFICIENT_EVIDENCE, basis, **kw)
    new = s.value + 1
    blocked = _raise_blocked(s, new, ctx)
    depth = ctx.evidence.ready_depth
    if s.knob == "lf_days" and s.region is not None:
        depth = ctx.evidence.ready_depth_by_region.get(s.region, depth)
    thin = depth is not None and depth < policy.SUPPLY_MIN_READY_DEPTH
    if blocked is None and s.knob == "lf_days" and thin:
        blocked = SUPPLY_BOUND, f"ready depth {depth} under the supply minimum"
    live = s.learned_ceiling_until is not None and ctx.now < s.learned_ceiling_until
    if blocked is None and live and new >= (s.learned_ceiling or new + 1):
        blocked = LEARNED_CEILING, f"{new} reached the learned ceiling"
    if blocked is not None:
        return _hold(s, ctx, blocked[0], blocked[1], **kw)
    if s.pending_confirm is None:
        basis = (
            f"up signal in cohort 1 of {policy.UP_CONFIRM_COHORTS}; need a distinct "
            "second cohort"
        )
        patch = {"pending_confirm": window[1]}
        return _hold(s, ctx, CONFIRMING, basis, patch=patch, **kw)
    patch = {"probation_open_since": ctx.now, "pending_confirm": None}
    if s.last_validated is None:
        patch["last_validated"] = s.value
    basis = (
        f"bottom {k} {bottom} each at or over {up:.0%} of median {median:.0f} in a "
        "second distinct cohort"
    )
    return _move(s, ctx, STEP, "TAIL_UP", basis, new, patch, **kw)


def decide(
    evidence: Evidence, states: Optional[Sequence[KnobState]], now: datetime
) -> List[Decision]:
    """One decision per knob state; at most one of them a move, by one step."""
    ctx0 = _Ctx(now, evidence, policy.SHORTS_FLOOR, policy.LF_DAYS_CEILING_PER_WEEK)
    unreadable = "knob state could not be read"
    if states is None:
        return [
            _hold(KnobState(k, None, None), ctx0, STATE_UNREADABLE, unreadable)
            for k in policy.KNOBS
        ]
    seen = set()
    usable: List[KnobState] = []
    out: Dict[int, Decision] = {}
    for i, s in enumerate(states):
        why = _readable(s)
        key = (getattr(s, "knob", None), getattr(s, "region", None))
        if why is None and key in seen:
            why = f"duplicate state for {key}"
        if why is not None:
            bad = s if isinstance(s, KnobState) else KnobState(policy.KNOBS[0], None, 0)
            out[i] = _hold(bad, ctx0, STATE_UNREADABLE, why)
            continue
        seen.add(key)
        usable.append(s)
    shorts = [x.value for x in usable if x.knob == "shorts_per_lf" and x.region is None]
    lf_values = [x.value for x in usable if x.knob == "lf_days"]
    ctx = _Ctx(
        now,
        evidence,
        shorts[0] if shorts else policy.SHORTS_FLOOR,
        sum(lf_values) if lf_values else policy.LF_DAYS_CEILING_PER_WEEK,
    )
    decisions = [
        out[i] if i in out else _evaluate(s, ctx) for i, s in enumerate(states)
    ]
    movers = sorted(
        (_MOVES[d.action], policy.KNOBS.index(d.knob), i)
        for i, d in enumerate(decisions)
        if d.action in _MOVES
    )
    for _, _, i in movers[1:]:
        d = decisions[i]
        basis = "one knob moves per evaluation; another moved first"
        decisions[i] = replace(
            d, action=HOLD, value=d.prev, reason_code=COOLDOWN, basis=basis,
            evidence_json={}, state_patch={},
        )  # fmt: skip
    return decisions
