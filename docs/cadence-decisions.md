# Cadence and volume: who decides

## Who decides

`seo_manager` decides three things, every night, from analytics:

- shorts per long-form (LF),
- LF days per region,
- shorts posting times.

`python -m seo_manager.analytics.volume_decide` reads an EvidenceBundle
(`--evidence <file|->`, schema in `seo_manager/analytics/evidence.py`), reads each
knob's current state from the decision log, runs the pure controller
(`volume_controller.decide`), appends one row per knob and prints one
`VOLUME_DECISION knob=... region=... action=... value=... reason=... policy_rev=...`
line per knob, holds included.

The log is the `cadence_decisions.sqlite3` file passed as `volume_decide --db` (and read by
`schedule --decisions-db` / `SEO_DECISIONS_DB`); it has no default location. It is
append-only (SQL triggers reject UPDATE and DELETE); the current value of a knob is
its latest row. Only `volume_decide` writes it.

A caller (the video pipeline) only reads `cadence_decisions.current(db, knob, region)`. It never
computes, defaults or overrides a value. An empty or missing log yields the seed
(`source="seed"`: shorts 7, the floor) without creating a file; an unreadable log
raises `DecisionStoreUnreadable`, and the nightly run prints `STATE_UNREADABLE` and
exits non-zero with nothing written, so planning halts instead of guessing.

No human sets these numbers. The rails they cannot cross (floor, ceiling, joint
weekly ceiling, cooldown, rollback, the coupling invariant
`shorts_per_lf <= windows * 7`) are named constants in `volume_policy.py`, changed
only as a policy revision in code. A write outside the rails is rejected.

When analytics is not `ok` (`scope_missing`, `no_token`, `error`) the rows are
`blind` and the line names the one human act: for `scope_missing` and `no_token`,
re-run the YouTube authorisation once.

## Which weekdays: the LF day set

`scheduling/lf_days.py` turns the `lf_days` count into weekdays; `brand.yaml` `dow` is only the seed.

- **Stored** in `evidence.dow` (sorted, Mon=0, `len == value`) on every `lf_days` row
  written with `--brand-yaml`. A hold copies the prior set; a step stores the chosen
  set; a rollback or blind revert restores the latest earlier row's set of that size,
  unless another region now uses one of its days: then the chooser picks a free one.
- **Seed rule.** `effective_dow(db, region, seed_dow)` returns the yaml `dow`
  (`source="seed"`) while no row carries `dow`; after that the latest row wins
  (`"decision"`). A malformed set raises `ValueError`, an unreadable log
  `DecisionStoreUnreadable`. `load_cadence(path, decisions_db=...)` and
  `schedule --decisions-db` apply it (`docs/scheduling.md`).
- **Choice** (`choose_dow`, deterministic, one step at a time). Up: among weekdays unused
  by any LF region or the superlong day, the largest minimum circular distance; tie to the higher median day-7 views of settled long-forms on
  that weekday (unmeasured ranks lowest), then the lower weekday. Down: drop the
  weakest measured day; none measured, keep the widest spacing, tie drops the higher.
- **No free weekday** on an up step is a `JOINT_CEILING` hold (unreachable today).
- **Inputs.** `volume_decide --brand-yaml Y [--schedule-db S]` (env `SEO_BRAND_YAML`,
  `SEO_SCHEDULE_DB`): yaml regions are `lf_days` keys, starting at `len(dow)`.
  Without the yaml, output and rows are exactly as before (no `dow`).
- **Supply.** A region's `ScheduleStore.ready_depth` is what its raise is checked
  against (`SUPPLY_BOUND`). Schedule db unset, missing or unreadable is depth 0, so a
  raise holds and the basis says so. The db is never created. Shorts ignore depth.
- **Output.** The `lf_days` line ends with `dow=tue,fri`.

## Which hours: the shorts times

Each yaml region with `shorts_times` gets its own `shorts_times` knob. The yaml list
is only the SEED; the latest `shorts_times` row (a sorted list of `"HH:MM"`) replaces
it through `load_cadence(path, decisions_db=...)` (`scheduling/shorts_times.py`,
`effective_times`). A malformed stored row raises, naming the region and row id.

- **Candidates.** Top-of-hour windows only. The rail is
  `SHORTS_HOUR_FLOOR`..`SHORTS_HOUR_CEILING` (8..18 in `volume_policy.py`, a
  design proposal, not a measured fact), minus the hours another
  region currently posts shorts at. Long-form publish hours are not fenced. An
  off-hour seed window is never swapped in or out.
- **Ranking.** Settled shorts (day-7 views) are bucketed by (weekday, hour); an hour's
  score is the mean of its cell means. An hour counts as measured at
  `PROBATION_MIN_N` settled shorts, ties go to the lower hour.
- **One swap per night.** The weakest measured current window gives way to the best
  measured candidate only when `robust_deviation` (log1p) of the candidate against
  the weak window reaches `PROBATION_MAD_MULTIPLE` (`HOUR_SWAP`). Otherwise it holds
  (`IN_DEADBAND`, or `INSUFFICIENT_EVIDENCE` with n and need). The window count never
  changes. Blind, lockout, probation, cooldown and rollback work as for the other
  knobs; a rollback restores the previous list whole.
- **One list per region, one share per LF day.** A region's list is every hour its
  shorts post at. The drive of each LF day takes an equal share
  (`drive_share`: the sorted windows dealt round-robin over the region's sorted LF
  days, `share_size = windows // LF days` each, the last LF day starting at the
  earliest window), so two LFs of one region whose shorts windows overlap never want
  the same hour. A day off the cadence takes the first LF day's share; a window left
  over by an uneven split belongs to no drive.
- **Coupling.** `shorts_per_lf <= share_size x 7` per region, enforced in the pure
  module and the store (which knows a region's LF day count only from an `lf_days`
  row, else assumes 1). The decider holds (`JOINT_CEILING`) a `shorts_per_lf` raise
  the narrowest region cannot carry, and an `lf_days` raise that would shrink a
  region's share under the live `shorts_per_lf`, rather than let the store reject
  the night.
- **Output.** Each per-region row carries `evidence["ranked"]`: the measured hours,
  best first.
- **Known limit.** A swap only reaches an hour that already has enough settled
  shorts; nothing makes the knob try an unmeasured hour.
