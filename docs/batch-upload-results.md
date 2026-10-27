# Batch upload outcomes

An upload is successful only when the underlying uploader returns a nonempty
video ID. `None`, an empty string or whitespace is a failed upload, even when
the uploader returned without raising an exception. Such a result has
`success=False`, no video ID or URL, and an error explaining the missing ID.

`continue_on_error=True` proceeds to the next video after this failure;
`False` stops the batch immediately. Failure and success totals follow the
per-video outcomes. The uploader's validation-only `DRY_RUN_SUCCESS` result
continues to be accepted for dry runs. Existing exception handling and quota
accounting are unchanged.

A spent daily quota (`QuotaExhaustedError`), a local budget refusal
(`QuotaExceededError`) or refused credentials (`AuthError`) stops the batch
whatever `continue_on_error` says. That video and every untried one come back
`success=False` with an `error_class`; earlier successes stand:

| Error | `error_class` | `deferred` | `next_eligible` |
|---|---|---|---|
| `QuotaExhaustedError` | `quota_exhausted` | true | the quota reset, ISO-8601 |
| `QuotaExceededError` | `quota_budget` | true | none |
| `AuthError` | `auth` | false | none |

Any other failure that carries an `error_class` attribute keeps it on its
result (an interrupted upload that cannot be found yet is `outcome_unknown`)
and follows `continue_on_error`.

`tests/unit/test_batch_upload_outcomes.py` exercises these branches with a
mock uploader and temporary video fixtures, without credentials or API calls.

## Quota model

`BatchUploader` sizes a batch with the same `QuotaManager` the uploader
charges; every cost comes from its operation table, none is hard-coded.

- `videos.insert` is one call in its own `youtube_data/videos.insert` bucket
  (100 per day by default), not 1600 units. The pre-check needs that bucket's
  `remaining` to be at least the number of videos.
- `thumbnails.set` (50) and `playlistItems.insert` (50, for a video whose
  metadata sets `playlist_id`) come from the default 10,000-unit pool. The
  pre-check needs the pool's `remaining` to cover them.
- An unbudgeted bucket (limit `None`) always passes.
- The pre-check is advisory: it reserves nothing. Reservations happen per
  request inside `YouTubeUploader`.

`quota_used`, on `VideoUploadResult` and `BatchUploadResult`, counts
default-pool units only (thumbnail and playlist calls). Inserts show in their
own bucket (`QuotaManager.get_bucket_usage("youtube_data", "videos.insert")`),
not in `quota_used`.

`BatchUploader(credentials_path, project_id, *, quota=None, brand_id=None)`
passes `quota` and `brand_id` to `YouTubeUploader`. Without `quota` it builds
one stored under `data_home() / "quota"` (`$SEO_MANAGER_DATA_HOME`, see
`seo_manager/paths.py`), not under the working directory.
