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
