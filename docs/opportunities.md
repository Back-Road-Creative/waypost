# Opportunities

`seo_manager/opportunities/` turns stored observations into reviewable
findings. Rules are pure and deterministic: observations and current video
text go in, `Opportunity` records come out. No I/O, no network, no store
writes. Callers read observations with `CollectionStore.latest(tenant,
source=...)` and pass them in.

## Contract

`Opportunity` (frozen) carries: `id`, `tenant`, `rule`, `status`, `asset`
(video id), `observed`, `evidence` (`EvidenceRef` tuple: source, subject,
metric, window, reported_at), `window_start`/`window_end`, `proposed_change`,
`priority`, `priority_reasons`, `effort`, `risk`, `automation`, `validation`,
`next_evaluation` (aware datetime), `missing_evidence`, `policy_version`.
`as_dict()` gives JSON-safe output (ISO datetimes, lists).

- `status` is `open` or `needs_evidence`. A missing input is shown in
  `missing_evidence`, never guessed.
- `priority` is a versioned **ordering key** set by product policy. It is not
  a confidence, a probability or a predicted gain. `None` sorts last.
- `automation` is `manual_review_only` for every rule here.

## Rule `search_demand_not_in_metadata`

`find_search_demand_opportunities(observations, video_text, *, tenant, now,
policy=DEFAULT_POLICY)`; source: `youtube_analytics_search_terms`
(`docs/youtube-search-terms.md`).

1. Only the given tenant's observations of that source are read; another
   tenant's observation raises `ValueError`. Per video only the newest
   `window_end` is used. Duplicate revisions keep the latest `reported_at`.
2. The term is the subject after the first `|`. A hashed subject
   (`sha256:`) is recovered from provenance `term=...` only when the
   provenance was not truncated (under 500 characters) and its hash matches;
   otherwise the opportunity is `needs_evidence` with `term_text_truncated`.
3. A term is eligible when its `views` is known and at least
   `policy.min_views`. Unknown views are never treated as zero and never
   produce an opportunity.
4. Coverage: NFKC, casefold, word tokens (`\w+`). A term is covered when every
   token appears in the title plus description tokens; covered terms produce
   nothing.
5. Video text absent or `None`: one `needs_evidence` opportunity per video
   (`video_text_unavailable`) citing all its eligible terms.
6. At most `policy.max_terms_per_video` per video, highest views first.
7. If the video's `search_term_rows` provenance says `at_cap=true`, the
   priority reasons say only the top-n referrers are known. If that
   observation is absent, `completeness_unknown` is added (the opportunity is
   not blocked).
8. `priority` = the term's views. Reasons give the views with the window and
   the term's share of the video's reported search views, or say the share is
   unknown when any term's views are unknown.
9. `id` = `opp_` + first 16 hex of sha256 over tenant, rule, video id and
   normalized term (unit separator joined; term empty for the per-video
   `needs_evidence` case). It is stable across reruns and windows.
10. `next_evaluation` = `now` + `policy.reevaluate_after`. Output order is
    priority descending (`None` last) then id, independent of input order.

## Policy versioning

`SearchDemandPolicy` holds `version` (`search-demand-1`), `min_views` (10),
`max_terms_per_video` (5) and `reevaluate_after` (7 days). Changing any
number means a new `version`; each opportunity records the version that
produced it.

## Guards

- Footage or page evidence is required for every place, activity or claim:
  the proposed change says to add wording only where the footage shows it.
- Query text is never stuffed into content; it is quoted for a reviewer.
- The numbers are observed referrals from YouTube search. They are not total
  search volume and not a predicted lift; no text field claims either.
