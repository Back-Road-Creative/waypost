"""Nightly volume and cadence decision: ``python -m seo_manager.analytics.volume_decide``.

Reads an EvidenceBundle (``--evidence <file|->``), reads each knob's current state
from the decision log (``--db``), calls the pure controller, appends one row per
knob (holds included) in a single transaction and prints exactly one
``VOLUME_DECISION`` line per knob.

Refusals are loud and write nothing: an unreadable store prints
``STATE_UNREADABLE`` and exits 3; a bundle this reader does not understand prints
``EVIDENCE_REFUSED`` and exits 2. When analytics is not ``ok`` the rows are
``blind`` and each line names the one human act that unblocks it.

With ``--brand-yaml`` it also decides WHICH weekdays each region's long-form uses
(``scheduling.lf_days``): every ``lf_days`` row carries ``evidence["dow"]``, and
``--schedule-db`` gives each region's ready depth (unreadable counts as 0, so a raise
holds ``SUPPLY_BOUND``). It also decides WHICH hours each yaml region posts shorts at
(``scheduling.shorts_times``): one ``shorts_times`` key per region with seed hours,
every row carrying ``evidence["ranked"]``, and other regions' shorts hours fenced
off. Without ``--brand-yaml`` nothing changes.
"""

import argparse
import json
import os
import sqlite3
import sys
from collections.abc import Mapping, Sequence
from dataclasses import fields, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from seo_manager.core.profile import DAY_ABBR, Cadence, canon_region, load_cadence
from seo_manager.scheduling import lf_days, shorts_times
from seo_manager.scheduling.store import ScheduleStore

from . import cadence_decisions as store
from . import volume_policy as policy
from .evidence import EvidenceBundle, EvidenceRefused, parse_bundle, parse_ts
from .volume_controller import (
    BLIND_REVERT,
    HOLD,
    JOINT_CEILING,
    ROLLBACK,
    STATE_UNREADABLE,
    STEP,
    SUPPLY_BOUND,
    Decision,
    Evidence,
    KnobState,
    ShortOutcome,
    apply_decision,
    decide,
)

EXIT_REFUSED = 2
EXIT_UNREADABLE = 3

_HUMAN_ACT = {
    "scope_missing": "re-run the YouTube authorisation once",
    "no_token": "re-run the YouTube authorisation once",
    "error": "read health.detail in the evidence export and fix the analytics fetch",
}
_STATUS = {
    "step": "ok",
    "hold": "hold",
    "rollback": "rollback",
    "validated": "validated",
    "blind_revert": "blind",
}
_SOURCE = {"rollback": "rollback", "blind_revert": "blind_revert"}
_DT_FIELDS = (
    "last_change_at", "probation_open_since", "learned_ceiling_until",
    "lockout_until", "cohort_cutoff", "pending_confirm",
)  # fmt: skip
_STATE_FIELDS = [
    f.name for f in fields(KnobState) if f.name not in ("knob", "region", "value")
]


def _state_to_json(state: KnobState) -> dict[str, Any]:
    out = {}
    for name in _STATE_FIELDS:
        v = getattr(state, name)
        out[name] = v.isoformat() if isinstance(v, datetime) else v
    return out


def _state_from_row(row: store.StoredDecision) -> KnobState:
    kw: dict[str, Any] = {}
    for name in _STATE_FIELDS:
        if name in row.state:
            v = row.state[name]
            kw[name] = datetime.fromisoformat(v) if name in _DT_FIELDS and v else v
    return KnobState(row.knob, row.region, row.value, **kw)


def _knob_keys(
    bundle: EvidenceBundle, db: Path, cadence: Cadence | None = None
) -> list[tuple[str, str | None]]:
    regions: list[str | None] = sorted(
        {s.region for s in bundle.shorts if s.region}
    ) or [None]
    if cadence is not None:  # the yaml's regions, whether or not they posted shorts
        regions = sorted(cadence.regions)
    wanted = {("shorts_per_lf", None), ("shorts_times", None)}
    wanted |= {("lf_days", r) for r in regions}
    if cadence is not None:
        wanted |= {
            ("shorts_times", r) for r, c in cadence.regions.items() if c.shorts_times
        }
    wanted |= set(store.keys(db))
    order = {k: i for i, k in enumerate(policy.KNOBS)}
    return sorted(wanted, key=lambda k: (order.get(k[0], 99), k[1] or ""))


def _evidence(
    bundle: EvidenceBundle,
    depth_by_region: Mapping[str, int] | None = None,
    fenced: Mapping[str, Sequence[int]] | None = None,
    canon: bool = False,
) -> Evidence:
    shorts = [
        ShortOutcome(
            s.video_id, parse_ts(s.posted_at),
            canon_region(s.region) if canon else s.region,
            s.weekday, s.hour_local, s.day7_views, s.source,
        )
        for s in bundle.shorts
    ]  # fmt: skip
    return Evidence(
        shorts,
        bundle.health.analytics_status,
        parse_ts(bundle.as_of),
        None,
        dict(depth_by_region or {}),
        dict(fenced or {}),
    )


def _fence(states: Sequence[KnobState]) -> dict[str, list[int]]:
    """Per region, the hours every OTHER region currently posts shorts at."""
    mine: dict[str, list[int]] = {}
    for s in states:
        if s.knob == "shorts_times" and s.region is not None:
            try:
                mine[s.region] = [h for h, _ in shorts_times.parse_windows(s.value)]
            except ValueError:
                continue  # the controller reports an unreadable state
    return {r: sorted(shorts_times.fence_for(r, mine)) for r in mine}


def _with_ranked(
    decisions: list[Decision], evidence: Evidence, now: datetime
) -> list[Decision]:
    """Every per-region ``shorts_times`` row carries the measured hours, best first."""
    cutoff = now - timedelta(days=policy.SETTLE_DAYS)
    out = []
    for d in decisions:
        if (
            d.knob == "shorts_times"
            and d.region is not None
            and isinstance(d.value, list)
        ):
            rows = [
                x
                for x in evidence.shorts
                if x.region == d.region and x.posted_at <= cutoff
            ]
            ranked = shorts_times.ranked_json(shorts_times.rank_hours(rows))
            d = replace(d, evidence_json={**d.evidence_json, "ranked": ranked})
        out.append(d)
    return out


def _carry_check(
    decisions: list[Decision], states: Sequence[KnobState]
) -> list[Decision]:
    """A move a region's shorts windows cannot carry becomes a ``JOINT_CEILING`` hold.

    One LF's drive posts at its share of the region's windows
    (``shorts_times.share_size``) on each of the 7 days to the next LF. A
    ``shorts_per_lf`` raise past the narrowest region's share, or an ``lf_days``
    raise that shrinks a region's share under the live ``shorts_per_lf``, would be
    rejected by the store and fail the whole night's batch, so the hold is made
    here, with the region named.
    """
    cap = store.LF_PUBLISH_INTERVAL_DAYS
    days = {
        s.region: s.value
        for s in states
        if s.knob == "lf_days" and isinstance(s.value, int)
    }
    windows = {
        s.region: len(s.value)
        for s in states
        if s.knob == "shorts_times"
        and s.region is not None
        and isinstance(s.value, list)
    }
    if not windows:
        return decisions
    live = [s.value for s in states if s.knob == "shorts_per_lf"]
    spl = live[0] if live and isinstance(live[0], int) else policy.SHORTS_FLOOR

    def carries(region: str | None, n_days: int) -> int:
        return shorts_times.share_size(windows[region], n_days) * cap

    out = []
    for d in decisions:
        why = None
        if d.knob == "shorts_per_lf" and d.value != d.prev:
            room, region = min(
                (carries(r, days.get(r, 1)), r or "") for r in sorted(windows)
            )
            if d.value > room:
                why = (
                    f"{region}: {windows[region]} shorts window(s) over "
                    f"{days.get(region, 1)} LF day(s) x {cap} cannot carry "
                    f"shorts_per_lf {d.value}"
                )
        elif d.knob == "lf_days" and d.value != d.prev and d.region in windows:
            if isinstance(d.value, int) and spl > carries(d.region, d.value):
                why = (
                    f"{d.region}: {windows[d.region]} shorts window(s) over "
                    f"{d.value} LF day(s) x {cap} cannot carry shorts_per_lf {spl}"
                )
        if why is not None:
            d = replace(
                d, action=HOLD, value=d.prev, reason_code=JOINT_CEILING,
                basis=why, evidence_json={}, state_patch={},
            )  # fmt: skip
        out.append(d)
    return out


def _new_row(
    d: Decision,
    prior: KnobState,
    last_ok: int | None,
    now: datetime,
    dow: Sequence[int] | None = None,
) -> store.NewRow:
    blind = d.reason_code.startswith("ANALYTICS_")
    status = "blind" if blind else _STATUS.get(d.action, "hold")
    evidence = dict(d.evidence_json)
    evidence["next_eval"] = d.next_eval.isoformat()
    if dow is not None:
        evidence["dow"] = sorted(dow)
    return store.NewRow(
        knob=d.knob,
        region=d.region,
        value=d.value,
        prev=d.prev,
        status=status,
        source=_SOURCE.get(d.action, "policy"),
        reason_code=d.reason_code,
        basis=d.basis,
        applied_at=now,
        evidence_window=d.evidence_window,
        evidence_n=d.evidence_n,
        need_n=d.need_n,
        floor=d.floor,
        ceiling=d.ceiling,
        rollback_of=last_ok if d.action == "rollback" else None,
        evidence=evidence,
        state=_state_to_json(apply_decision(prior, d)),
    )


def _line(d: Decision, status: str, dow: Sequence[int] | None = None) -> str:
    blind = d.reason_code.startswith("ANALYTICS_")
    parts = [
        "VOLUME_DECISION",
        f"knob={d.knob}",
        f"region={d.region or '-'}",
        f"action={'blind' if blind else d.action}",
        f"value={json.dumps(d.value, separators=(',', ':'))}",
        f"reason={d.reason_code}",
        f"policy_rev={policy.POLICY_REV}",
        f"n={d.evidence_n}",
        f"need={d.need_n}",
        f"next_eval={d.next_eval.date().isoformat()}",
    ]
    act = _HUMAN_ACT.get(status) if blind else None
    if act:
        parts.append(f'human_act="{act}"')
    if dow is not None:  # always last: a reader that splits on spaces keeps its fields
        parts.append("dow=" + ",".join(DAY_ABBR[x] for x in sorted(dow)))
    return " ".join(parts)


def _load(source: str) -> Any:
    text = sys.stdin.read() if source == "-" else Path(source).read_text()
    return json.loads(text)


def _ready_depths(
    cadence: Cadence, schedule_db: Path | None
) -> tuple[dict[str, int], str | None]:
    """Ready drives per region, and why unreadable (then 0 everywhere). Never creates the db."""
    zeros = {r: 0 for r in cadence.regions}
    if schedule_db is None:
        return zeros, "no --schedule-db given"
    if not schedule_db.exists():
        return zeros, f"{schedule_db} does not exist"
    try:
        sched = ScheduleStore(schedule_db, cadence.tz, cadence.regions)
        return {r: sched.ready_depth(r) for r in cadence.regions}, None
    except (sqlite3.Error, OSError, ValueError) as exc:
        return zeros, f"{schedule_db}: {exc}"


def _prior(
    db: Path, row: store.StoredDecision, cadence: Cadence | None
) -> tuple[KnobState, tuple[int, ...] | None]:
    """State and (``lf_days`` under ``--brand-yaml``) weekday set; no weekday row = yaml seed."""
    if (
        cadence is not None
        and row.knob == "shorts_times"
        and row.region in cadence.regions
    ):
        seed = [t.strftime("%H:%M") for t in cadence.regions[row.region].shorts_times]
        if seed:
            eff = shorts_times.effective_times(db, row.region, seed)
            if eff.source == "seed":
                return KnobState("shorts_times", row.region, list(eff.times)), None
            return _state_from_row(row), None
    if cadence is None or row.knob != "lf_days":
        return _state_from_row(row), None
    region = row.region
    if region in cadence.regions:
        eff = lf_days.effective_dow(db, region, cadence.regions[region].dow)
        if eff.source == "seed":
            return KnobState("lf_days", region, len(eff.dow)), eff.dow
        return _state_from_row(row), eff.dow
    dow = row.evidence.get("dow")  # a logged region the yaml no longer lists
    known = isinstance(dow, list) and len(dow) == row.value
    return _state_from_row(row), (tuple(sorted(dow)) if known else None)


def _dow_for(
    d: Decision,
    prior_dow: tuple[int, ...],
    taken: frozenset,
    medians: Mapping[int, float],
    earlier: Sequence[store.StoredDecision],
) -> tuple[int, ...]:
    """The weekday set a decision implies; ``ValueError`` if none can be chosen."""
    if d.action not in (STEP, ROLLBACK, BLIND_REVERT) or d.value == len(prior_dow):
        return prior_dow
    if d.action != STEP:  # going back: reuse the set that was in force at that size
        for h in reversed(earlier):
            dow = h.evidence.get("dow")
            known = isinstance(dow, list) and len(dow) == d.value == h.value
            if known and not taken.intersection(dow):  # another region's day now
                return tuple(sorted(dow))
    return lf_days.choose_dow(prior_dow, d.value, taken=taken, weekday_medians=medians)


def _lf_dows(
    db: Path,
    bundle: EvidenceBundle,
    cadence: Cadence,
    decisions: list[Decision],
    priors: list[tuple[KnobState, tuple[int, ...] | None]],
) -> tuple[list[Decision], list[tuple[int, ...] | None]]:
    """A weekday set per ``lf_days`` decision; a raise with no free day becomes a ``JOINT_CEILING`` hold."""
    now_utc = parse_ts(bundle.as_of)
    prior = {
        d.region: p[1]
        for d, p in zip(decisions, priors, strict=False)
        if p[1] is not None
    }
    fixed = set() if cadence.superlong_day is None else {cadence.superlong_day}
    out_d: list[Decision] = []
    out_w: list[tuple[int, ...] | None] = []
    for d, (_, mine) in zip(decisions, priors, strict=False):
        if d.knob != "lf_days" or mine is None:
            out_d.append(d)
            out_w.append(None)
            continue
        taken = (
            frozenset(x for r, ds in prior.items() if r != d.region for x in ds) | fixed
        )
        meds = lf_days.lf_weekday_medians(
            bundle.longforms, d.region, cadence.tz, now_utc
        )
        try:
            earlier = store.history(db, "lf_days", d.region)
            dow = _dow_for(d, mine, taken, meds, earlier)
        except ValueError as exc:
            d = replace(
                d, action=HOLD, value=d.prev, reason_code=JOINT_CEILING,
                basis=f"{d.region}: {exc}", evidence_json={}, state_patch={},
            )  # fmt: skip
            dow = mine
        out_d.append(d)
        out_w.append(dow)
    return out_d, out_w


def run(
    evidence_src: str,
    db: Path,
    today: date,
    brand_yaml: str | None = None,
    schedule_db: Path | None = None,
) -> int:
    try:
        bundle = parse_bundle(_load(evidence_src))
    except (EvidenceRefused, ValueError, OSError) as exc:
        print(f"EVIDENCE_REFUSED reason={exc}")
        return EXIT_REFUSED
    cadence: Cadence | None = None
    if brand_yaml:
        try:
            cadence = load_cadence(brand_yaml)
        except (ValueError, OSError) as exc:
            print(f"CADENCE_REFUSED reason={exc}")
            return EXIT_REFUSED
    now = datetime.combine(today, time.min)
    try:
        priors = []
        for knob, region in _knob_keys(bundle, db, cadence):
            row = store.current(db, knob, region)
            priors.append(_prior(db, row, cadence))
    except (store.DecisionStoreUnreadable, ValueError) as exc:
        print(f"STATE_UNREADABLE reason={exc}")
        return EXIT_UNREADABLE
    depths: dict[str, int] = {}
    unread: str | None = None
    if cadence is not None:
        depths, unread = _ready_depths(cadence, schedule_db)
    states = [p for p, _ in priors]
    fenced = _fence(states) if cadence is not None else {}
    evidence = _evidence(bundle, depths, fenced, canon=cadence is not None)
    decisions = decide(evidence, states, now)
    status = bundle.health.analytics_status
    if status != "ok":
        # The controller has no rule for a shorts_times state without a region or
        # list value, so it never reaches its blind check there; the log still
        # records why nothing moved.
        code = f"ANALYTICS_{status.upper()}"
        decisions = [
            replace(d, reason_code=code, basis=f"analytics blind ({code})")
            if d.knob == "shorts_times"
            and d.action == HOLD
            and (d.region is None or not isinstance(d.value, list))
            else d
            for d in decisions
        ]
    bad = [d for d in decisions if d.reason_code == STATE_UNREADABLE]
    if bad:
        for d in bad:
            print(
                f"STATE_UNREADABLE knob={d.knob} region={d.region or '-'} reason={d.basis}"
            )
        return EXIT_UNREADABLE
    if cadence is not None:
        decisions = _carry_check(_with_ranked(decisions, evidence, now), states)
    dows: list[tuple[int, ...] | None] = [None] * len(decisions)
    if cadence is not None:
        decisions, dows = _lf_dows(db, bundle, cadence, decisions, priors)
        if unread is not None:
            decisions = [
                replace(d, basis=f"{d.basis}; ready depth could not be read: {unread}")
                if d.knob == "lf_days" and d.reason_code == SUPPLY_BOUND
                else d
                for d in decisions
            ]
    rows = []
    for ((prior, _), d), dow in zip(
        zip(priors, decisions, strict=False), dows, strict=False
    ):
        ok = [
            h.id
            for h in store.history(db, d.knob, d.region)
            if h.status == "ok" and h.id is not None
        ]
        rows.append(_new_row(d, prior, ok[-1] if ok else None, now, dow))
    try:
        store.append_many(db, rows)
    except (store.DecisionStoreUnreadable, store.DecisionRejected) as exc:
        print(f"STATE_UNREADABLE reason={exc}")
        return EXIT_UNREADABLE
    for d, dow in zip(decisions, dows, strict=False):
        print(_line(d, status, dow))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="volume_decide", description=__doc__)
    p.add_argument("--evidence", required=True, help="EvidenceBundle JSON file, or -")
    p.add_argument("--db", required=True, type=Path, help="cadence_decisions.sqlite3")
    p.add_argument("--today", type=date.fromisoformat, default=None, help="YYYY-MM-DD")
    p.add_argument(
        "--brand-yaml",
        default=os.environ.get("SEO_BRAND_YAML"),
        help="tenant brand.yaml (env SEO_BRAND_YAML): also decide the LF weekdays",
    )
    p.add_argument(
        "--schedule-db",
        type=Path,
        default=os.environ.get("SEO_SCHEDULE_DB"),
        help="schedule.sqlite3 (env SEO_SCHEDULE_DB): ready-drive depth per region",
    )
    args = p.parse_args(argv)
    return run(
        args.evidence,
        args.db,
        args.today or date.today(),
        args.brand_yaml,
        Path(args.schedule_db) if args.schedule_db else None,
    )


if __name__ == "__main__":
    sys.exit(main())
