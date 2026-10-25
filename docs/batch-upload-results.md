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

`tests/unit/test_batch_upload_outcomes.py` exercises these branches with a
mock uploader and temporary video fixtures, without credentials or API calls.
