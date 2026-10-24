# seo_manager.scheduling

Tenant-cadence slot booking for long-form drives.

- `core/profile.py` `load_cadence(path)` reads `seo: cadence:` of a brand yaml:
  `lead_hours`, `ready_depth`, `superlong_day`, `timezone`, and per region `dow`,
  `time`, `day_times`, `shorts_times`. Fails closed (`ValueError`) on unknown
  keys, bad times, or `day_times` weekdays outside `dow`. Weekdays match whole
  (`tue`, `Tuesday`); `tue fri` or `sunburn` raises.
- **yaml `shorts_times` is only the seed too.** `load_cadence(path, decisions_db=...)`
  replaces a region's windows with its latest `shorts_times` row, else keeps the seed
  (`scheduling/shorts_times.py`; rules in `docs/cadence-decisions.md`).
- **yaml `dow` is only the seed.** Waypost decides each region's long-form
  weekdays (`docs/cadence-decisions.md`). `load_cadence(path, decisions_db=None)`
  replaces a region's `dow` with the set in its latest `lf_days` row
  (`evidence.dow`), else keeps the seed; a `day_times` entry survives only if its
  day is still in `dow`. A malformed stored set raises `ValueError`, an unreadable
  log `DecisionStoreUnreadable`. `Cadence.dow_source` maps region to `EffectiveDow`
  (`dow`, `source` `seed|decision`, `applied_at`, `row_id`); `schedule report`
  prints it as `lf_days`.
- `scheduling/lf_days.py`: `choose_dow` (the chooser), `lf_weekday_medians`,
  `effective_dow`.
- `scheduling/store.py` `ScheduleStore(path, cadence.tz)`: `schedule.sqlite3`
  (`lf_bookings`) under a group-writable file lock plus `BEGIN IMMEDIATE`.
  `book` raises `SlotTaken` on a clash. `tz` is required and the slot functions
  raise `ValueError` if it differs from the cadence zone. `bookings()` on a
  missing database returns `[]` and creates nothing.
- `scheduling/slots.py`: `next_free_slot`,
  `validate_anchor` (the long-form weekday check, run as an upstream gate; `anchor_problems` is the same check as `(date, weekday)` data), `assert_free`, `lf_series_dates` (the LF series rule: LF n = LF1 + 7 * (n - 1), same weekday; the video pipeline and the claim both use it).

Occupancy is per calendar date in the cadence zone. The same region blocks;
another region only with `block_cross_region`; a booking or caller with no
region blocks all.

Own bookings: pass `project=` and `lf_index=` (`assert_free`: `{date: lf_index}`)
to skip only that `(project, lf_index)` row, the key `book` uses (it refuses a
second LF of one project on a held date). Without `lf_index` every row of
`project` is skipped, so pass it when `book` follows. A launch check or a publish-phase re-run must pass the project.

Regions are canonical (stripped, lower-case) everywhere: the cadence keys, `book`,
`bookings(region)` and the clash test. `ScheduleStore(path, cadence.tz, cadence.regions)`
needs the known regions; `book` and `assert_free` raise `ValueError` on an unknown one, so
`'North'` can never dodge a booking held under `'north'`.

Differences from the video pipeline's own slot logic: superlong parts are out of scope
for the claim, so a pipeline that publishes them decides their slot itself (or uses
`next_free_superlong_slot` below). `next_free_slot` uses each weekday's own designed
time, where the pipeline used one flat time.

## Claim protocol

Every slot decision lives here; callers only ask and execute.

- `store`: `ready_drives(project, region, lf_count, output_dir, state_path, mirror_sha,
  audit_log, created_at, status, claim_id, lf_mode, ingest_mode)` (`auditing|ready|claimed|complete|failed`) and
  `alerts_sent(kind, key, sent_at)` (`mark_alert`: once per 24 h). `register_ready` upserts
  (a failed drive becomes claimable again; claimed or complete is never resurrected;
  `lf_mode` / `ingest_mode` record how the audit ran, `claim` returns them, and the publish
  pass must run the same: a full_drive LF fails the best_moments duration cap),
  `set_status` (only `auditing`<->`ready` and `auditing`/`ready`->`failed`; `claimed` and
  `complete` are entered and left only through `claim_drive` / `finish_claim`, which book and
  free the slots in the same transaction), `init()`, `ready_depth(region)`, `release_unconfirmed(project)`. Reads on a missing
  database return empty and create nothing.
- `scheduling/claim.py` `claim(store, cadence, region, now=, lead_hours=, expect_sha=,
  dry_run=)`: oldest `ready` drive of the region; books one weekly series: the
  first LF1 slot whose every LF1 + 7k date (k < `lf_count`) is free in the region (own rows
  exempt), because the publish phase puts LF n at LF1 + 7*(n-1) days and refuses a taken date.
  A re-claim after a published LF keeps that series fixed and raises `SlotTaken` if a later
  date got taken meanwhile, returns `None` when nothing is ready or the first slot is beyond
  `lead_hours`. The write is `ScheduleStore.claim_drive`: one `BEGIN IMMEDIATE` that
  re-reads the drive, refuses unless still `ready` (`ValueError`) and at `expect_sha`
  (`StaleDrive`), refuses a held date (`SlotTaken`), books every slot with no video id
  and marks the drive `claimed`. All or nothing. An open claim in the region is returned
  again (`already_claimed`), so a re-run never takes a second slot.
  A re-claim of a failed-then-re-registered drive keeps every LF that already has a video id
  (same row, same date) and plans only the rest; `claim_drive` refuses to overwrite or move
  a row with a video id. The claim lists those LFs in `confirmed_lfs` (1-based); their dates are
  history, so a caller must not pass a confirmed LF1's date as a new `--lf-publish-date`. The LF
  offsets come from `slots.lf_series_dates`, the one series rule. A claim older than one lead window comes back `stale=True` and
  raises a `stale_claim` alert (`mark_alert`, once per 24 h; not on `--dry-run`) -- close it
  with `complete` or `fail`. `claimed_at` is recorded on `ready_drives` (added by the first
  write to an older database). `--dry-run` on a missing database returns `None` and creates
  no directory or lock; on an existing one it never alters the schema.
- `complete(claim_id)` keeps the bookings; `fail(claim_id)` frees those without a video id.
  Repeating the same outcome is a no-op; the opposite one raises.
- CLI: `python -m seo_manager.cli schedule [--brand-yaml Y] [--db D]
  [--decisions-db C] {claim --region R
  [--lead-hours H] [--expect-sha S] [--dry-run] | complete C | fail C | ready-depth --region R |
  register --project P --region R --lf-count N --mirror-sha S [--lf-mode M] [--ingest-mode I] [--status auditing|ready] |
  set-status --project P --status S}`.
  JSON on stdout; exit 2 on any refusal, including a locked database or a malformed
  brand.yaml. `--db` (or `SEO_SCHEDULE_DB`) is required, with no code-relative default, and must name an
  existing file: every command except `register` and `import-ledger` exits 2 on a missing one (a typo must not
  read as "nothing ready") unless `--init` is given, which creates it: keep
  the file wherever the deployment keeps its per-tenant data, and pass the same `--db` / `SEO_SCHEDULE_DB` to every scheduled job and hand run so they share one database.
  `--decisions-db` (env `SEO_DECISIONS_DB`) points `schedule` at the tenant's
  `cadence_decisions.sqlite3` so slot booking follows the decided weekdays; unset,
  the brand.yaml `dow` is used as written.
- A claim never books the base date (`include_today=False`): a 14:00 slot ten
  minutes away is not a slot a multi-GB upload can meet. `store.claim_drive` refuses a
  slot on the claim's day, so the decision and the write agree.
- With `--expect-sha`, ready drives at another mirror sha are skipped (not blocking the
  region) and a `stale_sha` alert is recorded per project once a day; only when every
  ready drive is stale does the claim raise `StaleDrive`. Retire one with
  `schedule set-status --project P --status failed`.
- A read on a database created before the claim tables (no `ready_drives`) treats drives and alerts as empty and
  never alters the file.

## Report, backlog, import

- `slots.next_free_superlong_slot`: `cadence.superlong_day` (else any day), one weekly queue
  across regions (`block_cross_region=True`), 6 h minimum lead.
- `report.build(store, cadence, now=)` builds a read-only schedule report dict:
  `scheduled_longforms` (upcoming bookings), `queue_ends`, `next_available` (one row per
  region and its own designed day, earliest per region flagged, with shorts windows) and
  `next_available_superlong`. Differences from the video pipeline's own schedule report: the cadence binds each region to
  its own weekdays (the pipeline listed region x every day); shorts counts and booked titles
  live in the pipeline's content ledger and social queue, not in this store. Also not
  reproduced: `shorts_windows` is one flat `shorts_times` list per region (the pipeline varies
  it by weekday); there is no `shorts_required`; there is no `kind` (`superlong_part` vs
  `longform`; it needs the title); the superlong day is one `cadence.superlong_day` for all
  regions and its slot time is the region's own. Slot dates and times of longforms,
  `queue_ends` and `next_available` are equal:
  `test_import_ledger_then_report_matches_reference_schedule_report` replays a captured
  pipeline report (`tests/scheduling/fixtures/`).
- `backlog.load(drive_backlog.yaml)` is the fail-closed manifest reader (unknown key, empty
  sources, duplicate project raise); `next_row` is the first row of a region not yet past its
  audit: no `ready_drives` entry, or one still `auditing` (a ready, claimed, complete or
  failed row is never returned, so a failed row is not retried on its own); `shortfall` is
  `ready_depth` minus audited drives waiting.
- A row for footage with no GPS of its own may name a whole-day track, `day_gpx`, and
  `camera_utc_offset_hours`, the hours the camera clock runs from UTC (US Eastern summer is
  `-4`). The two are set together or not at all, because a track sliced on the wrong clock
  lands hours off with no error; the offset is a number from -14 to 14. `backlog-next`
  prints both (`""` and `null` when unset).
- CLI adds `report [--now]`, `next --region R [--after]`, `backlog-next --manifest M --region R`
  (the next row to audit as JSON, or `{"row": null}`; an audit job reads it, then
  `register --status auditing`, then `set-status ready`) and `import-ledger <json>`: the
  one-shot backfill of a content-ledger JSON file's long-forms into `lf_bookings` (idempotent;
  a same-region date conflict is counted in `skipped_conflicts` and each skipped
  `(project, date, video_id)` is listed under `skipped`; a region the cadence does not know
  exits 2 before anything is written). `claim` JSON carries `stale`. The parity check against the pipeline's own schedule command is that test
  (a captured report plus its ledger), not a manual step.

## Callers

A video pipeline takes every slot decision from here: its ledger plus `schedule.sqlite3`
are presented as a duck-typed store (`tz` + `bookings()`), and its config as a `Cadence`.
seo_manager imports nothing from the pipeline. A pipeline that resolves the superlong day
itself keeps doing so; this package only offers `next_free_superlong_slot`.
