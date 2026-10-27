# Continuous optimization daemon — mechanism

This document describes the mechanism behind the daemon's rules: the evidence vocabulary and what must not be reintroduced.

- **The learned settings are not applied anywhere, and a test pins that.** The daemon writes `state_dir/learned_config.json` as its own snapshot, read back only to report which sections changed. The `recommendation_auto_applier` that copied it into `config/learned_config.json` (`seo.title.optimal_length`, `seo.tags.recommended`, `thumbnails.default_style`, `descriptions.default_template`, `posting.default_time`) was retired: nothing ever read those keys, and the title length could not be wired honestly. It is a `{min, max}` range around the top quarter's average with no sample size, window or Short/longform split, falls back to an invented 50-70, and `select_title`'s `max_title_chars` excludes a candidate outright, so a learned cap would drop fact-complete titles. `select_title` runs on `DEFAULT_POLICY`. `tests/unit/test_learned_config_has_no_writer.py` keeps the writer gone; an admissible per-format measurement would be new work with its own tests. The stored range carries its basis (`title.basis` in the snapshot, `title_length_basis` in status): `measured`, `style` (the editorial 50-70 with nothing measured) or `null` for a state saved before the basis was recorded, which reads as unknown rather than as style.

## Evidence vocabulary

The trend step labels every comparison with `metrics["evidence_sufficiency"]`, one of:

- `sufficient` (`EVIDENCE_SUFFICIENT`) - both sides have enough impressions to give a direction.
- `below_floor` (`EVIDENCE_BELOW_FLOOR`) - a side has fewer than `MIN_IMPRESSIONS_FOR_DIRECTION` impressions.
- `sample_size_unknown` (`EVIDENCE_SAMPLE_UNKNOWN`) - the impression count is not recorded.
- `unmeasured` (`EVIDENCE_UNMEASURED`) - the rate could not be measured.
- `relative_change_undefined` (`EVIDENCE_CHANGE_UNDEFINED`) - the baseline is zero, so a relative change has no value.
- `no_prior_observation` (`EVIDENCE_NO_BASELINE`) - there is nothing earlier to compare with.

Two window kinds say what a rate covers: `cumulative_to_date` (`WINDOW_CUMULATIVE_TO_DATE`, a pooled running average over every event in the store) and `bounded` (`WINDOW_BOUNDED`, a rate over a stated period).

`MIN_IMPRESSIONS_FOR_DIRECTION` is 1000 impressions required on each side. It is a floor, not a significance test.

## Run record and sync configuration

- **Every cycle leaves a run record, and "never ran" is its own state.** `state_dir/last_run.json` (`RUN_RECORD_FILENAME`) records start, finish, the dry-run flag, the outcome (`completed` / `steps_failed` / `raised`), failed steps and the sync outcome. `read_last_run()` returns `None` when the file is absent — that `None` is the point: before it, a cron that never fired and a cron that fired nightly and found nothing left identical traces, so no scheduler could be shown to be active. A file that will not parse reads back `unreadable`, never `None` — damaged evidence is not missing evidence — and `get_status()` reports `last_cycle_run_state` (`never_recorded` / `recorded` / `unreadable`) as a key of its own, so no reader has to interpret a bare null.

- **The record is written on every exit, dry run and crash included.** `_save_state` skips a dry run because a dry run must not persist what it *learned*; a run record is not a learning, and the CLI is dry unless `--apply`, so gating it on `--apply` would leave the nightly schedule exactly as unprovable as before. A cycle that raised records `raised` with the error and **no** `finished_at`, then re-raises; a field nobody observed reads back `None`, never a plausible-looking value.

- **The age is reported; how old is too old is not decided here.** `CycleRunRecord.age_days` is computed on read via `evidence_age.age_days` and never stored (a stored age is wrong a second later). No threshold, no `STALE_AFTER`, no healthy/unhealthy verdict — a test asserts none appears. That is a product decision, and a constant invented in code would be that decision made by whoever typed first.

- **The missing YouTube sync is a CONFIGURATION gap, not a code gap.** `build_daemon` takes `youtube_credentials` and `video_mapping_file` (the `video_id -> content_id` map `_load_video_mapping` reads; the caller supplies it), exposed as `--youtube-credentials` / `--video-mapping`. **Neither has a default path and neither falls back to an environment variable** — a credential here is always an explicit argument, and a guessed path is one pointing silently at nothing. Both are required before a collector is built: credentials without a mapping reach `batch_sync` with nothing to ask about and report `empty`, which means "the account has nothing published", not "nobody said which videos".

- **An unconfigured sync names every missing input, not just the first.** `metrics["unconfigured_reasons"]`: `no_youtube_credentials_configured`, `no_mapping_file_configured`, `youtube_collector_could_not_be_built` (supplied and failed — a different report from never supplied), and `no_collector_supplied_reason_unrecorded`. Never empty; silence there would read as "configured".
