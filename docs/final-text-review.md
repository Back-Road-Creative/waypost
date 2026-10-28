# Final-text review

How Waypost refuses an upload whose on-screen text, title, description or tags were not reviewed. For the bounded-region rules see [bounded-region-review.md](bounded-region-review.md); for titles that name one verified region see [region-only-title-decisions.md](region-only-title-decisions.md).

Versions in force: review `final-text-review-6`, text policy `final-text-policy-4`, template set `drive-templates-4`, title policy `title-policy-2`. A test keeps these in step with the code.

`analytics.final_text_review.review_final_text` puts the approved on-screen, thumbnail and Shorts text, the final `select_title` title, and the location/date evidence behind each into one fingerprinted record; it refuses on missing or ambiguous evidence, reports `disagree` when the title changed after approval, and never approves (`human_gate_required` is always true).

`verify_review` refuses an upload whose title or text differs from what was reviewed.

Record v2 carries the route: each place the retained footage holds for at least `FinalTextPolicy.min_place_seconds` must be named on every whole-video surface and the long-form title (a Short's text and title name its own segment), or a region may stand in only when its `region_claims` item contains every such place with full GPS coverage, no gaps and no samples outside, or with a matching pinned Census bounded-route proof for coverage/gaps (see docs/bounded-region-review.md) (`place_dropped`, `route_reduced`, `region_partial`, `region_coverage`, `region_gaps`, `region_outside`, each also reported as `region_not_contained`). A drive that holds no place long enough to name may send an empty `route_places` only beside region claims, each containing no place; every whole-video surface and the long-form title must then name one of those regions that holds the drive by the same measurements (`region_dropped`). Without a region claim an empty route is still `missing_field:route_places`, and a claim that contains no place on a route that has places is `region_claim_malformed`.

Every name on a surface needs a claim: once the cited names are taken out, any word not in `FinalTextPolicy.filler_words` is refused as `name_unbacked:<surface>:<word>`, and a cited location must be on the retained route (`place_not_on_route`) or, on a Short, in its own segment (`place_not_in_segment`); roads are cited as `road` evidence.

Review v4 (record v2) types every visible surface (`analytics.final_text_surfaces`), so a description, its chapters and its tags are reviewed without any free prose: a `place_line` is checked as above, a `template` passes only when its `template_id` is registered in `TEMPLATES` and the SHA-256 of its exact bytes matches (`template_unknown`, `template_changed`), a `chapter` is a segment surface over its own `segment_places` (`chapter_without_window`), and a `tag` is backed by evidence or comes from a registered `tag.*` template; any other kind is refused (`surface_kind`).

Review v5 adds a fifth kind, `param_template` (`analytics.final_text_params`, `analytics.drive_templates`): a registered skeleton with typed slots (`number`, `unit`, `duration`, `enum`, `place`; no free-string slot can be registered).

The hash covers the skeleton with its slot markers and the slot specs, not the filled text, so one changed byte is refused (`template_unknown`, `template_changed`); each slot value must be legal for its type (`param_slot`), a place slot cites an evidence id that must back like a `place_line` (`param_place`, `name_unbacked`, `place_not_on_route`), and the text must equal the skeleton filled (`param_text`).

A record's `description_layout` (`gms-drive-1`) makes the description the ordered registered blocks only (`layout_unknown`, `layout_free_line`, `layout_order`, `layout_missing`) and keeps tags in the registered order (`tag_order`); non-place tags come only from the registered `tag.*` set.

`generation.drive_text.build_drive_text(DriveTextData)` turns typed drive data into those parts with no I/O and raises on a bad value, including every GMS body line (the start-to-end arc with up to five passing places, or else towns with or without roads, then roads, states and features, each capped and raising past its cap, never truncating) and the footer playlist and cadence lines (which need the site line); `through` alone names twelve and counts the rest (`… +N more`).

The empty `description.gap` template is GMS's blank line after its body lines, and `LAYOUT_GAPS` makes it present exactly when a body line is (`layout_gap`).

Templates are `drive-templates-4`; `REVIEW_VERSION` is `final-text-review-6` and the text policy is `final-text-policy-4`, so a record written under another template set is refused.

`build_visible` emits these entries and takes template text only from the registry.

A hash binds a template's bytes, not their meaning, so templates change only in a reviewed PR.

`verify_review` also refuses a missing or empty review, an edited one, and a changed source, plan, title policy or text policy (`FinalTextReviewRefused.code` / `.reasons`).

`YouTubeUploader.upload_video` requires `final_text_review` and `final_text_context` and checks them on the request body before any API call, dry runs included.

The check also binds the description and tags actually sent: `verify_final_text` always passes `body["snippet"]["description"]` and `tags` to `verify_review`, which refuses them unless they equal `compose_description` / `compose_tags` of the reviewed surfaces (`stale:description`, `stale:tags`).

Description lines (`description` / `description.*` place lines and templates) and chapters, joined by newlines, make the description; `tag` surfaces make the tags; on-screen surfaces (`ost`, `thumbnail`, `shorts_ost`) are not part of either, so callers must build the description and tags they send with those two functions.
