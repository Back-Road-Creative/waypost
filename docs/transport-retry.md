# Transport errors and bounded read retry

`seo_manager/transport.py` classifies Google API failures and retries
idempotent reads inside a fixed bound. The analytics collector
(`analytics/youtube_sync.py`) uses it for every API read.

## Error classes

`classify(exc)` uses the HTTP status and the provider `reason`
(`error.errors[].reason`). A reason beats a status. Anything that is not an
`HttpError` or a connection/timeout fault returns `None` and is re-raised
unchanged (it is a bug, not transport).

| Class | When | Retried by `retry_read` |
|---|---|---|
| `TransientError` | 5xx, 408, 429, `rateLimitExceeded`, `userRateLimitExceeded`, `backendError`, `internalError`, `TimeoutError`, `ConnectionError` | yes, within the bound |
| `QuotaExhaustedError` | `quotaExceeded`, `dailyLimitExceeded` (any status) | never; carries `next_eligible` |
| `AuthError` | 401, or `forbidden`, `insufficientPermissions`, `authError`, `invalidCredentials`, `unauthorized`, `accessNotConfigured` | never; one actionable item (re-authorize the token) |
| `InvalidRequestError` | other 400 and 404 | never |
| `UnknownTransportError` | everything else | never (unknown is not transient) |

Errors keep `status`, `reason`, `retry_after` and the original as `__cause__`.

## Retry bound

`retry_read(fn, *, policy=RetryPolicy(), sleep, rand, now)`: 4 attempts,
1 s base, 30 s per-sleep cap, 60 s total-sleep cap. Backoff is exponential
with full jitter (`rand() * min(max_delay, base_delay * 2**n)`). `Retry-After`
(seconds or HTTP-date) replaces it, capped at `max_delay`; a past or garbage
value falls back to backoff. A sleep that would pass `max_total_delay` is not
taken; the last `TransientError` is raised. `sleep`, `rand`, `now` are
injectable.

## Quota reset

YouTube Data API daily quota resets at midnight America/Los_Angeles.
`next_quota_reset(now)` returns the next such midnight strictly after `now`,
built from the Pacific calendar date (not `now + 24h`), so a 23- or 25-hour
DST day lands on the real midnight. `now` must be timezone-aware.

## Reads only

`retry_read` is for idempotent reads. Never wrap a write whose outcome may
have succeeded (upload, insert, update): a retry after a timeout can
duplicate or overwrite. For writes, `classify`, then reconcile by reading back.

## Collector behaviour

- Reads retry transient errors; a missing video, an invalid request or a
  transient failure past the bound still gives `None` (unknown metrics stay
  `None`, never 0). `QuotaExhaustedError` and `AuthError` propagate.
- `batch_sync` stops calling the API on quota or auth. The failing and untried
  videos come back `success=False` with `error_class` (`quota_exhausted` or
  `auth`); quota adds `deferred=True` and `next_eligible` (ISO-8601).
- The daemon keeps `SyncOutcome`: some synced is `PARTIAL`, none `FAILED`.
  `metrics` adds `videos_deferred`, `sync_next_eligible`, `sync_blocked_by`;
  an auth stop adds `auth_error_reauthorize_token` to `changes_made`.

## Uploads: resume, then reconcile

`videos.insert` is a write, so `YouTubeUploader.upload_video` never just runs it
again. The code is in `seo_manager/youtube/upload_recovery.py`.

- **Journal.** Each attempt is keyed by the request body plus the file's name
  and size. Before the first byte is sent, the attempt is recorded `in_flight`
  under `<data home>/upload-journal/`. It ends as `uploaded` (with the ID),
  `not_uploaded` (refused, or the session never started) or `unknown`.
- **Resume.** A transient fault mid-upload (5xx, 429, a socket timeout or
  reset, an SSL error) resumes the same session. The client asks how many bytes
  arrived and continues, so no second video can be created. `Retry-After` paces
  the resume; otherwise the backoff is jittered and exponential, bounded by
  `resume_policy`.
- **Reconcile.** When resumes run out, or a run finds an `in_flight` or
  `unknown` record, the uploader reads the channel's uploads playlist. It looks
  for exact title and description sent no earlier than the attempt, less
  `CLOCK_SKEW`.
  - One match is the upload.
  - Several matches, or a failed lookup, raise `UploadOutcomeUnknown`.
  - No match within `RECONCILE_GRACE` (15 minutes) of the attempt also raises
    `UploadOutcomeUnknown`, because a fresh upload can take a while to appear.
  - No match after the grace period means it was never created, so it is sent
    again.
  - An unreadable record has no start time and never times out. Check the
    channel, then delete the file the error names.
- **Same file, new metadata.** Each record also keeps a `file_key` (file name
  and size) and the title and description it was sent with. Before sending
  under a key with no record, the uploader looks for `in_flight` or `unknown`
  records with the same `file_key` and another key (for example, the metadata
  was regenerated after a crash). Each is looked up by its own title and
  description:
  - Found: the old record becomes `uploaded` and `UploadOutcomeUnknown` is
    raised, naming the existing video. The file is already uploaded with
    earlier metadata, so update that video; the ID is never returned as if the
    new metadata had been applied.
  - Absent past `RECONCILE_GRACE`: the old record becomes `not_uploaded` and the
    new upload goes ahead.
  - Absent within the grace period, ambiguous, or a failed lookup: raises
    `UploadOutcomeUnknown`.
  - An `uploaded` record of the same file also blocks while its video still
    exists (checked with `videos.list`), whether it was settled by this check or
    by an ordinary upload. If the video is gone, the record becomes
    `not_uploaded` and the new upload goes ahead. This also refuses a deliberate
    re-upload of the same file (same name and size) with new metadata while the
    old video exists and its record is under 30 days old, because that would be
    a duplicate video. To override, delete the old video (the existence check
    then lets it through) or remove its journal record.
  - A record written before `file_key` existed cannot be matched by file and is
    skipped; its own key still reaches it.
- **Pruning.** Once per uploader instance, `UploadJournal.prune` deletes
  `uploaded` and `not_uploaded` records started more than `JOURNAL_RETENTION`
  (30 days) ago. It never deletes `in_flight`, `unknown`, unreadable or
  undated records. A prune `OSError` is logged and never blocks an upload.
- **Seen again.** An `uploaded` record is confirmed with `videos.list` and
  returned; it is uploaded again only if the video was deleted.
- **Terminal errors.** A 400 or 404 returns `None` (`not_uploaded`), as before.
  `QuotaExhaustedError` and `AuthError` now raise instead of returning `None`.
- **Quota.** Given a `QuotaManager`, the uploader reserves `videos.insert` before
  sending. The reservation is refunded if the request could not be built,
  charged as failed on a refusal, and kept when the outcome is unknown. The
  local `QuotaExceededError` means nothing was sent. Reconcile reads, the
  thumbnail and the playlist insert are reserved and settled too.
- **Follow-ups.** The thumbnail and playlist insert run after the video exists,
  including one found by reconcile. A failure there is reported, never raised.

The reconcile lookup relies on `playlistItems.list` on the owner's uploads
playlist showing private videos with their real title and description. That
has not been checked against the live API yet.
