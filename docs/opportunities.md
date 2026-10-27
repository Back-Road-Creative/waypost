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

## Daily report

`seo-manager collect report --tenant T [--db PATH] [--youtube-credentials PATH]
[--limit N] [--json]` reads the collection store (never writes it, never creates
it; a missing database exits 2) and prints:

- **Source health**: exactly one entry per expected source
  (`youtube_analytics_search_terms`, `youtube_reach`, `search_console`), plus any
  other source found in health. A source with no health row is `never_ran` ("no
  run recorded: never run or not configured": the store cannot tell the two
  apart). Otherwise the last outcome (`fresh`, `partial`, `failed`, `empty`,
  `unconfigured`, or `unknown`), age since the last success, `error_class` and
  `next_eligible`.
- **Recovery history is not recorded.** Health keeps only the latest state, so an
  incident that recovered leaves no trace; the report says so
  (`recovery_history: "not_recorded"`) rather than listing recoveries.
- **Open opportunities**: the top `--limit` (default 10) in the rule's order, with
  `open_total` for the full count. `priority` is an ordering key, not a confidence.
- **Needs evidence**: every `needs_evidence` opportunity with its
  `missing_evidence` reasons.
- **Next evaluation**: the earliest `next_evaluation`, or none.

Video titles and descriptions are read only when `--youtube-credentials` is given
(YouTube Data `videos.list`, up to 50 ids per call, read-only, bounded retry; each
call costs quota units). Without it nothing is fetched, `video_text.state` is
`not_configured`, and each video's opportunity is `needs_evidence` with
`video_text_unavailable`. A failed fetch leaves those videos without text and
records only the exception type (`video_text.error_class`). `video_text.state` is
`fetched` (no error), `partial` (an error, but some texts were read), `error`
(nothing read, including `client_build_failed:<Type>`), or `not_needed`
(credentials given, but no search-term observations to fetch text for).

Exit codes: 0 when a report was produced (failures are shown inside it), 2 when
the store is missing or unreadable.

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
