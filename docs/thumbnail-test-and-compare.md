# Test & Compare companion

YouTube Studio's **Test & Compare** feature lets an operator upload up to 3
thumbnail variants for a video and has YouTube show them to different
viewers to pick a winner. It is UI-only: there is no API to upload the
variants, no API to say which one was selected, and no API to read back
which one "won" — Studio decides and shows the result only in its own UI.

This service cannot drive Test & Compare end-to-end. What it does instead:

1. **Prepare the 3 variants.**
   `YouTubeThumbnailGenerator.prepare_test_and_compare_variants(...)`
   (`seo_manager/youtube/thumbnail_generator.py`) generates the 3 candidate
   images by calling the existing `generate_variations_from_background` —
   no drawing code is duplicated — and writes a `test_compare_manifest.json`
   next to them naming each variant's style and file path. The operator
   uploads these 3 files into Studio's Test & Compare UI by hand.

2. **Record which one was uploaded.**
   `ThumbnailABTracker.record_test_compare_selection(...)`
   (`seo_manager/analytics/thumbnail_ab_tracker.py`) is a manual record:
   the operator states, after uploading, which of the 3 prepared styles
   they actually used. It stores that through the existing
   `record_thumbnail` path, plus the prepared-styles list and manifest
   path as provenance only — neither ever enters the CTR analysis.

3. **Read the outcome.**
   `ThumbnailABTracker.get_test_compare_outcome(content_id)` reuses the
   tracker's existing stored record — the same `ThumbnailPerformance` that
   `update_performance` / `batch_update_from_analytics` already populate
   from an Analytics sync. It adds no new Analytics read path. `ctr` stays
   `None` (never a fabricated `0.0`) until a real sync has written a rate
   for that `content_id`, and any rate that does arrive still carries its
   `ctr_source` — the same card-click-rate-is-not-a-thumbnail-CTR refusal
   (see `ANALYTICS_CTR_LIMITATION`) applies here.

## Explicit follow-up (not built here)

Studio never exposes *which variant won* Test & Compare through any API —
not the Analytics API, not a CSV export keyed to variant. There is
currently **no automated way to learn the Test & Compare winner**; the
`get_test_compare_outcome` read above can only report whatever ordinary
per-video Analytics (`cardClickRate`, or a future Studio CSV thumbnail-CTR
ingest per `ANALYTICS_CTR_LIMITATION`) already attaches to the video as a
whole, not a per-variant result. Building that would require either a
Studio UI scrape (out of scope: this service makes no network calls it
doesn't own) or Google shipping a Test & Compare API. Until then, "which
variant won" is read by the operator from Studio's own UI and, if it
matters downstream, entered by hand — this module does not guess it.
