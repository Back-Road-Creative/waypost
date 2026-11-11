# Account-scoped content timing

`seo_manager.scheduling.content_times.recommend_time(Request(...))` is a pure
producer of timing suggestions. It proposes a concrete UTC instant from a
specific account's measured history, or returns `Unavailable` with a stable
reason and data requirement. It performs no network calls, reservations,
approval, uploads or publishing.

## Request and evidence contract

`Request` is a frozen dataclass with `platform`, immutable `account_id`,
`content_format`, IANA `timezone`, timezone-aware `now`, an `observations` tuple
and an `occupied_slots` tuple of timezone-aware instants. The caller supplies
the clock and the current account's occupied slots. No system clock or profile
fallback is consulted. Occupancy means exact instant equality, including when
different UTC offsets describe the same instant; it is not a spacing policy.

Each `Observation` contains `post_id`, the same platform/account/format,
`metric`, aware `posted_at` and `observed_at`, and a finite nonnegative
`day7_value`. `observed_at` is when the trusted adapter actually obtained that
historical seven-day value, not a timestamp invented by the caller to refresh
old data. The producer cannot authenticate observations: an account-aware
analytics adapter must establish their source and identity before calling it.
Lifetime totals must not be relabeled as seven-day totals.

Supported combinations are Facebook `video` / `views`, Facebook `reel` /
`reel_total_plays`, and YouTube `short` or `video` / `views`. These reuse the
existing [reach metric contract](../seo_manager/analytics/evidence.py).
`photo` returns `unsupported_content` and explicitly identifies the missing
account-scoped photo metric/evidence contract. Video history is not substituted.
The older `ShortRecord` has no account identity and is not accepted directly.
No existing regional YouTube cadence seed becomes a Facebook recommendation.

Every observation must match; the function refuses the whole request instead
of quietly dropping contradictory evidence. Duplicate IDs, malformed values,
naive timestamps, future timestamps, and measurements taken less than seven
days after posting are refused. Policy version `account-content-times/1`
requires posts no older than 90 days and observations no older than 21 days
(`volume_policy.BLIND_DAYS`). These are explicit conservative software policy
bounds, not measured user preferences. Each input tuple is bounded to 10,000
entries. Unsupported or malformed requests have typed unavailable results.

## Recommendation and provenance

The producer converts publication instants to the requested timezone, then
reuses [rank_hours](../seo_manager/scheduling/shorts_times.py): an hour's score
is the mean of its weekday-cell means. Each hour needs at least
`volume_policy.PROBATION_MIN_N` samples (currently nine). This reduces bias from
an uneven weekday mix; it does not establish causality or guarantee reach.
Tied scores prefer the lower clock hour.

The best measured hour is tried first over seven local calendar dates starting
today. Past, occupied, ambiguous fall-back and nonexistent spring-forward
instants are skipped. If that hour has no free candidate in the horizon, the
next measured hour is tried. A valid suggestion is always strictly after
`now`. This is a ranking over observed clock hours, not a learned weekday
recommendation or a general promise of the optimal posting time.

`Available` contains:

- `proposed_at`: aware UTC datetime; `timezone` retains the intended display zone.
- `platform`, `account_id`, `content_format`: the exact evidence identity.
- `basis="measured_account_history"`, `reason="best_measured_hour"` and
  `producer_version`.
- `sample_count`: the observations supporting the selected hour.
- `evidence_age_seconds`: age of the oldest observation supporting that hour.
- `evidence_sha256`: SHA-256 of canonical, sorted normalized evidence plus
  platform/account/format/timezone/metric and producer version. Input reordering
  does not change it. It is a fingerprint, not a signature or source proof.

`Unavailable.reason` distinguishes `invalid_request`, `unsupported_content`,
`no_evidence`, `invalid_evidence`, `incompatible_evidence`, `duplicate_evidence`,
`future_evidence`, `stale_evidence`, `unsettled_evidence`,
`insufficient_evidence` and `no_free_slot`. A phone UI can keep the Waypost option
visible with this explanation while also offering immediate or user-selected
timing. It must not invent a fallback date when advice is unavailable.

## Approval and scheduling boundary

Preserve the resolved timestamp and provenance with the publication plan shown
to the user. A later rerun may suggest a different time; it must never silently
replace an approved plan. This module does not select any user's timing mode,
define a late-execution window, or reserve its suggestion. A separate scheduler
must atomically check conflicts and bind the approved plan, account, timestamp
and explicit execution window. An offline host cannot execute a local schedule;
missed-window and unknown-remote-outcome handling belong to that dispatcher.

This first producer does not add a provider collector, CLI, Facebook photo
metric, account-specific configured window, or bridge UI integration. Callers
with no compatible verified history receive an honest unavailable result.
