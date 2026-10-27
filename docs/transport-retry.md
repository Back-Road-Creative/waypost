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
