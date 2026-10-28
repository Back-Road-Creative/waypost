# Waypost SEO opportunities and automatic recovery plan

Prepared 2026-10-27, America/New_York. **Proposal only; no runtime changes.**
Requirement: identify SEO opportunities, improve results, recover automatically,
and use free capabilities wherever practical. This is a product plan, not agent
governance or authorization to publish changes to connected accounts.

Evaluated 2026-10-27 against source baseline `dddc9ec` (see "Evaluation findings"
at the end). Source `file:line` references below remain against that ref.
The original plan merged as PR #67 at `f7eebd7`; this revision incorporates the
subsequent evaluation and review recommendations. **Only the plan is being
updated. Implementation, deployment, scheduling and account changes are not
being executed or authorized by this revision.**

## Recommendation

Build a measured loop: collect search evidence → identify an opportunity →
prepare a grounded change → apply within an explicit policy → verify the remote
result → measure outcomes → retain, hold, or restore the previous version.

Waypost has valuable parts of this loop, especially factual review, provenance,
title selection, and cadence rollback. It does **not** yet offer an integrated
SEO opportunity engine or general self-healing SEO. Prioritize first-party data,
honest recommendations, and operational recovery before more title generators.

"Free" means no required subscription, paid API, or paid model inference. Existing
compute, storage, development, API quotas, and occasional operator time still
have costs. Commercial free tiers are optional cross-checks, not dependencies.
Full parity with commercial keyword/backlink databases is not a credible free
commitment. Useful owned-channel and owned-site coverage is achievable.

## Verification scope and baseline

Reviewed public Waypost `main` at
[`dddc9ec35e6cbe3d6a93753e954b6b7d04fa7ddb`](https://github.com/Back-Road-Creative/waypost/tree/dddc9ec35e6cbe3d6a93753e954b6b7d04fa7ddb).
GitHub returned that SHA as current during this review; its `ci` run was successful.
PR #67 subsequently merged with passing Linux, Windows and cleanliness checks;
`main` was `f7eebd7` with no open PRs at the subsequent review. These are dated
observations, not promises about later repository state.

Source references below are relative to this document and refer to that baseline.
The old private workspace package is not the source of truth. Earlier improvement
plans contain completed items; do not recreate their retired auto-applier or
duplicate the existing reach probe.

| Capability | Verified evidence | Implication |
| --- | --- | --- |
| Fact-bound metadata and upload checks | [final review](../seo_manager/analytics/final_text_review.py) (`human_gate_required=True` always, :14, :1081; `verify_review` :1142 refuses malformed, tampered or stale reviews), [uploader](../seo_manager/youtube/uploader.py) (`verify_final_text` :336 → `verify_review` :353; `validate_upload_body` :312), [contract](final-text-review.md) | Reuse these checks. The review module verifies integrity and never approves. `verify_review` explicitly leaves human approval to its caller; this upload path does not independently inspect an `approved` record. Inventory the enforcing consumer and test its boundary before new writes. |
| Evidence-aware title selection | [selector](../seo_manager/analytics/title_selection.py) (`TitlePolicy` :135: `min_n=400`, `max_evidence_age_days=28`, `min_relative_lift=0.10`, `max_title_chars=100`; exclusion codes :69-74), [contract](title-selection.md) | Separates format and search/browse evidence; rejects `NO_EVIDENCE`, `METRIC_NOT_TITLE_EVIDENCE` (card CTR), `STALE`, `N_BELOW_MIN`. `TemplateObservation` (:112) is built only by tests today. A relative lift rule is not a causal experiment. |
| Cadence recovery | [controller](../seo_manager/analytics/volume_controller.py) (`_probation` :292, `COOLDOWN` :51, `ROLLBACK` :65, `BLIND_REVERT` :66, `Decision` :115, `KnobState` :99), [decision persistence](../seo_manager/analytics/cadence_decisions.py) (append-only SQLite with UPDATE/DELETE triggers :70-75, FileLock + `BEGIN IMMEDIATE` :247-254, read-only URI :180, `rollback_of` :64) | Already has probation, cooldown, rollback and blind reversion. Reuse the pattern and the store shape; do not describe it as a new feature. |
| Continuous learning | [daemon](../seo_manager/analytics/continuous_optimization_daemon.py) (`main` :1289 runs one cycle, cron owns the schedule; `CycleRunRecord` → `last_run.json` written on every exit incl. crash :472-497; `SyncOutcome` FRESH/PARTIAL/FAILED/EMPTY/UNCONFIGURED :65-86; `DaemonState` :377), [contract](continuous-optimization-daemon.md), [no-writer test](../tests/unit/test_learned_config_has_no_writer.py) | Learned JSON is a snapshot with no production consumer. The auto-applier was removed deliberately (#5). The test also pins `title_selection` away from `seo_optimizer`/`learned_config` and `max_title_chars == 100`. No cron/systemd unit ships in the repo. |
| Metrics identity and missing data | [metric source](../seo_manager/analytics/metric_source.py) (`UNKNOWN` :18, `is_thumbnail_click_through` :31), [evidence v2](../seo_manager/analytics/evidence.py) (`PLATFORMS` :34, per-platform `REACH_METRIC` never pooled :43-51, `None` kept :186-194), [sync](../seo_manager/analytics/youtube_sync.py) (`ctr=None`, never a fake 0, :171-172) | Keep unknown distinct from zero, and keep Facebook/YouTube counters distinct. Card CTR cannot justify title or thumbnail changes. |
| Thumbnail reach acquisition | [probe](../seo_manager/analytics/youtube_reach_probe.py) (`probe` :56-133; job creation only under `--create-job` :78-86; CSV column read :104-130; prints only) | Discovers report types, can create jobs, inspects report columns. Persists nothing; no path into `TemplateObservation`. |
| Recommendation quality | [optimizer](../seo_manager/analytics/seo_optimizer.py): `get_optimal_title_length` :440 returns `(50, 70)` on empty `title_lengths` :452; `generate_recommendations` :473 emits "Expand title to 50-70 chars for better CTR" :499 and "target 8-12 tags" :545; `optimize_metadata` slices `tags[:12]` :580; `predict_performance` :602 already carries `estimated_ctr_sample_size`/`estimated_ctr_source` but `confidence = score/factors` :664 is nonzero with empty lists | Empty history still returns a 50–70 title range and a "better CTR" suggestion. Prediction confidence is a heuristic score, not calibrated probability (45-char title with no data → 0.125). |
| Legacy SEO rules | [validator](../seo_manager/validation/youtube_best_practices.py) (title 50–70 :70-91, API cap 100 :122, description ≥200 :150-156, tags 10–15 :220-238), [YouTube adapter](../seo_manager/platforms/youtube.py) (`get_title_limit` 70 :28-33, `get_tag_limit` 15 :46-51, hard slice `tags[:15]` :72) | Three different tag targets exist (8–12 optimizer, 10–15 validator, 15 hard cap adapter) and two title caps (70 adapter, 100 API/selector). Separate API constraints from style advice and measured evidence. |
| Quota and transport | [quota manager](../seo_manager/quota/manager.py) (`OperationCost` :39-48 lacks `videos.update`; per-brand/operation tracking :348-357; atomic state :184), uploader in-memory `quota_used` :292, [batch uploader](../seo_manager/youtube/batch_uploader.py) `SAFE_DAILY_LIMIT` 8000 :220-259 (since removed: #73 and #80 replaced it with per-bucket quota in `quota/manager.py`, `DEFAULT_LIMITS`; no `SAFE_DAILY_LIMIT` remains in `seo_manager/`) | `QuotaManager` is imported by tests only; no production caller. No shared HTTP retry/backoff or `Retry-After` handling in the inspected API call paths; `HttpError` is swallowed at `youtube_sync.py:149,:187` and `uploader.py:307,:384,:413` (the probe reports it, :214). |
| Stores available | [cadence decisions](../seo_manager/analytics/cadence_decisions.py) (append-only SQLite, above), [event store](../seo_manager/storage/event_store.py) (JSONL; `upsert_event` :104-151 and `_save_indexes` :335-338 rewrite non-atomically), [atomic JSON](../seo_manager/fsio.py) `atomic_write_json` :14, [public-tree check](../tools/check_public_tree.py) :102 rejects committed `.db/.sqlite/.sqlite3` (+`-wal/-shm/-journal`) | New durable stores follow the cadence-decisions shape and live under an ignored runtime path. `SEOManager` constructs this store (`core/manager.py:150`) and passes it to `AnalyticsTracker` (:156); Metricool importers also use it. Fix crash safety as S1b without changing the JSONL format. |
| Technical website SEO and search-demand collectors | Search of `seo_manager/`, `tests/`, `docs/` for Search Console/webmasters, Lighthouse, sitemap, robots, JSON-LD, `YT_SEARCH`, `insightTrafficSource` | No implementation found (the one "Lighthouse" hit is a locality noun in `core/locality.py:36`). `canonical_url` (`core/metadata.py:54`) is a field, not a crawler. `youtube_sync.py:57-64` requests only views, watch time, average view percentage and card metrics per video, with no dimensions. Consuming sites may already implement parts. |
| Thumbnail experiments | [companion](thumbnail-test-and-compare.md), `prepare_test_and_compare_variants` (`thumbnail_generator.py:397`, writes the manifest with plain `json.dump` :457), `record_test_compare_selection` (`thumbnail_ab_tracker.py:250`) | Prepares variants and records the operator's selection, but retrieves no variant-level result. Expand the companion rather than inventing a winner from video-level CTR. |

### Checks actually performed

- Inspected a fresh isolated clone at the exact SHA above, relevant source,
  tests, docs, dependencies, and CI configuration.
- Ran eight explicit test files through the workspace pytest slot gate:
  `test_youtube_reach_probe.py`, `test_title_selection.py`,
  `test_thumbnail_ctr_provenance.py`, `unit/test_learned_config_has_no_writer.py`,
  `unit/test_daemon_cycle_run_evidence.py`,
  `unit/test_trend_observation_carries_its_evidence.py`,
  `unit/test_volume_controller.py`, and `unit/test_evidence_bundle.py`.
  Two distinct invocations were recorded on 2026-10-27:

  - Original: `python -m pytest -o addopts= -q <eight files>` — **152 passed in
    3.28 s, exit 0**. This explicit scoped run did not measure coverage.
  - Evaluation: `python -m pytest <eight files>` with configured coverage —
    **152 passed in 9.53 s, exit 1**, because whole-package coverage was 23%
    against the 66% floor. Assertions passed; that invocation's overall gate
    did not. It does not supersede or correct the original command's exit code.

  Both used Python 3.12.3 from an existing dependency environment with the clone
  on `sys.path`. Neither certifies a fresh install or the full suite. No API
  calls were made by these tests. This plan-only revision does not rerun them
  or present those historical results as new execution.
- Reproduced the empty-history recommendation with all pattern lists empty:
  "Expand title to 50-70 chars for better CTR", "target 8-12 tags", and nonzero
  prediction `confidence` for a short example title. Code path: `patterns` is a
  non-empty dict of empty lists, so the `if not self.patterns` guard at :633 does
  not fire. This is a diagnostic finding, not a shipped fix.
- Checked the first-party vendor and platform documentation linked below
  (re-fetched 2026-10-27; every quoted number below matched). Product claims are
  vendor-documented capabilities, not hands-on benchmarks.

Not verified here: deployed package identity, live token scopes, reach-report
availability for any particular channel, production job health, Search Console
ownership, current site crawl results, or actual ranking/traffic improvement.
Those are explicit implementation acceptance gates below. No credentials were
decrypted, report jobs created, accounts connected, or published text changed.

## Commercial comparison and free substitutes

| Product / benchmark | Verified capability and free boundary | Waypost response |
| --- | --- | --- |
| [vidIQ](https://vidiq.com/plans/) | Research, ideas and optimization; Free currently lists 150 monthly AI credits and limited access. Paid plans broaden research and optimization. | Own-query opportunity discovery, topic coverage, and explainable ranking should be automatic without depending on credits. Optional manual research can supplement first-party data. |
| [TubeBuddy](https://www.tubebuddy.com/pricing) | Lists Keyword Explorer, SEO Studio, A/B testing and productivity tools. Free tools include thumbnail preview and its extension; do not assume paid testing/bulk features are free. | Prepare grounded title/thumbnail candidates, previews and bounded update jobs. Use native experiments when eligible. No subscription or browser scraping dependency. |
| [Semrush](https://www.semrush.com/siteaudit/) | Free SEO Checker provides a prioritized snapshot; the fuller Site Audit advertises recurring crawls and progress tracking as an upgrade. Exact free account/API allowances were not verified. | Stable opportunity IDs, severity, affected assets, recurrence detection and before/after checks should be standard. Do not depend on a free trial for unattended operation. |
| [Ahrefs Free / Webmaster Tools](https://ahrefs.com/webmaster-tools) | Verified-site audits, search/backlink insights and analytics; page lists 5,000 crawl credits per month per verified project, 1K backlinks/keywords visible at once. | Optional independent owned-site audit/backlink review. No assumed free API or comprehensive competitor access. Import only supported exports with provenance. |
| [Screaming Frog](https://www.screamingfrog.co.uk/seo-spider/) | Free crawl covers up to 500 URLs per crawl; the prose restricts configuration, saved crawls, JS rendering and API integrations to paid, while the comparison table lists some under Free. Treat the restriction as the safe reading. | Good manual spot-check. Automated audits should run against local build artifacts with open tools and bounded live checks. |
| [YouTube Studio native A/B tests](https://support.google.com/youtube/answer/16391400) | Up to three title/thumbnail combinations, winner chosen by watch time (not CTR); desktop Studio only, advanced features required; Shorts, scheduled lives and premieres excluded. | Extend existing companion to title-plus-thumbnail manifests and explicit operator-recorded results. Keep Studio steps manual unless a supported API is verified. |

Copy the useful workflow—discovery, prioritization, change history, experiments
and recurring verification. Do not copy unexplained "SEO scores" or pretend
owned-channel queries measure market-wide search demand.

## Evidence sources to add or complete

1. **YouTube Reporting reach → persisted observations.** Google documents
   `channel_reach_basic_a1` (dimensions date, channel_id, video_id) and
   `channel_reach_combined_a1` (adds traffic_source_type, traffic_source_detail,
   operating_system, device_type), both with `video_thumbnail_impressions` and
   `video_thumbnail_impressions_ctr`. Never sum basic and combined for one video.
   This corrects the "Studio-only / API does not expose" wording that still
   stands in `docs/YOUTUBE_ANALYTICS_SETUP.md:232-234` (the same file describes
   the Reporting API probe at :236-258), `youtube_sync.py:47-56`,
   `metric_source.py:6-10`, `thumbnail_ab_tracker.py:432` and
   `docs/thumbnail-test-and-compare.md:5-7,:39-49`: the Analytics API and
   Reporting API are different surfaces. The existing probe is the starting
   point, not evidence of a working importer.
   [Report definitions](https://developers.google.com/youtube/reporting/v1/reports/channel_reports).
   Establish rate units from the provider contract and a real report before
   normalizing; preserve the raw rate and denominator, never guess from magnitude.
   [Metric definitions](https://developers.google.com/youtube/reporting/v1/reports/metrics).

2. **YouTube search terms and traffic sources.** Request shape: filter
   `video==<id>;insightTrafficSourceType==YT_SEARCH`, dimension
   `insightTrafficSourceDetail`, bounded `startDate`/`endDate`, `sort=-views`,
   `maxResults`. Google's own sample is a "top 10" query; the page does not state
   a row cap, so record how many rows came back and treat the list as the top
   referrers, not the whole set. Fit this into the existing per-video Analytics
   loop in `youtube_sync.py` (:153-199) rather than a second client; Analytics API
   quota is separate from Data API quota and must be budgeted separately. Search
   terms represent observed referrals, not total search volume or difficulty.
   [Dimensions](https://developers.google.com/youtube/analytics/dimensions),
   [request examples](https://developers.google.com/youtube/analytics/sample-requests).

3. **Owned-site Search Console.** Collect query/page/date/device/country and
   `type` (`web` default, `image`, `video`, `news`, `discover`, `googleNews`)
   with clicks, impressions, CTR and position. `rowLimit` 1–25,000 (default
   1,000); page with `startRow`; a past-the-end page returns zero rows without
   error. Request `dataState=final` for settled windows and record it in
   provenance. `all`/`hourly_all` may include incomplete data: use the response's
   `first_incomplete_date` or `first_incomplete_hour` and the provider timezone
   to identify affected rows. Keep incomplete windows out of settled comparisons;
   do not mark all historical rows partial merely because `all` was requested.
   The API explicitly returns top rows and does not
   guarantee every row; an omitted query is not zero demand. Never add together
   overlapping query/page aggregates.
   [API contract](https://developers.google.com/webmaster-tools/v1/searchanalytics/query).

4. **Technical and video-page audits.** Reuse existing site build checks first;
   add absent checks for status, canonicals, intended robots/noindex, redirects,
   internal links, duplicate metadata, sitemap membership, thumbnail availability,
   and truthful VideoObject/Clip data. Audit actual watch-page discoverability,
   not just JSON syntax. Metadata alone cannot promise video indexing.
   [Video guidance](https://developers.google.com/search/docs/appearance/video),
   [structured data](https://developers.google.com/search/docs/appearance/structured-data/video).
   Local [Lighthouse](https://developer.chrome.com/docs/lighthouse/overview) gives
   repeatable lab checks; use CrUX where field data exists. PageSpeed documentation
   says CrUX field data in that API is planned for removal (use the CrUX API or
   CrUX History API), so keep lab/field adapters separate and treat absent field
   data as unknown.
   [PageSpeed contract](https://developers.google.com/speed/docs/insights/v5/get-started).

5. **Optional distribution inputs.** Add Bing Webmaster evidence only after its
   current API/auth/quotas are verified; this review could not retrieve the
   selected Bing API documentation. Consider deduplicated IndexNow submissions
   for changed eligible owned URLs after successful publication. Submission is a
   notification, not proof of crawling/indexing or Google support.
   [IndexNow protocol](https://www.indexnow.org/documentation).
   Trends exports, free vendor reports, referral logs and a small approved
   competitor list can enrich ideas. They remain supplementary; no assumed
   unlimited Trends API or scraped SERP/rank service.

All connectors need site/channel identity, observation and ingestion timestamps,
window, provenance, schema version, completeness and error status. Separate API
unavailable, permission denied, delayed report, partial results and true zero.
Use SOPS-managed credentials for the reference deployment, through an explicit
provider boundary based on the reach probe's existing path. Keep channel/brand
identity explicit and avoid persistent plaintext token copies. `BrandCredentials`
exists in the library, but its existence does not make it the selected mechanism
for this deployment. New connectors share the same credential-provider contract;
credential migration or new account consent is outside this plan-only revision.

## Opportunities Waypost should produce

Every opportunity includes an ID, asset, observed problem, evidence links and
window, proposed change, priority reasons, effort/risk, automation eligibility,
validation method, and next evaluation time. Show "needs evidence" explicitly.
Priority weights are versioned product policy, never measured confidence.

| Opportunity | Detection and useful action | Guard against false conclusions |
| --- | --- | --- |
| Search demand not reflected in metadata | Queries already bringing relevant traffic but absent from a factual title/description; draft a clearer summary | Require footage/page evidence for every place, activity and claim; don't stuff query text into content |
| High exposure, weak selection | Thumbnail reach with lower CTR than comparable format/source cohorts; propose a title/thumbnail experiment | Adjust for source, content age and exposure; no universal good-CTR threshold or causal claim |
| Good engagement, weak reach | Supported watch-time/retention evidence plus limited discovery; suggest related links, playlists, useful watch pages or a topic follow-up | Low exposure can reflect low demand; a hypothesis is not a predicted lift |
| Declining search discovery | Settled comparable date windows show a persistent change; inspect indexing, changed metadata and query/source mix | Account for seasonality, data delay, format and demand shifts before suggesting rollback |
| Topic or geographic coverage gap | Cluster owned-channel queries and verified asset facts; identify useful unanswered coverage | Distinguish observed gaps from speculative ideas; never invent filming coverage or estimated market volume |
| Competing owned pages | Same query reaches several related URLs with unstable distribution; review intent and internal links | Multiple URLs are not automatically harmful; never auto-canonicalize/delete based on overlap alone |
| Technical loss | Broken intended links, missing required metadata, sitemap drift, invalid video data or unavailable thumbnails | Respect deliberately private/noindex content and intentional removals |
| Accessibility and navigation | Missing factual captions where speech exists, inaccurate chapters, weak internal navigation | Do not invent speech for silent footage, timestamps or translations; keep factual review |

Prefer title, thumbnail, description and actual content relevance over tag-count
work. YouTube says tags play a minimal role in discovery except for commonly
misspelled content, and that title, thumbnail and description matter more.
Existing advice to reach a fixed number of tags should not outrank measured
opportunities. [YouTube guidance](https://support.google.com/youtube/answer/146402).

## Automatic recovery and controlled changes

The proposed lifecycle is `detected → qualified → prepared → eligible → applied
→ verified → observing → retained / reverted / blocked`. Each transition records
its inputs and reason. A write receipt is not proof the remote state changed;
a read-back is not proof traffic improved.

| Action class | Automation target | Required boundary |
| --- | --- | --- |
| Read, reconcile and retry | Automatic daily collection, backfill, checkpoint recovery and idempotent replay | Per-source quota budget, bounded retries with jitter, Retry-After handling, duplicate suppression and last-success age — all net-new: nothing in `seo_manager/` retries today |
| Deterministic local repair | Automatic regeneration of derived files and draft fixes from approved facts | Change the source of truth; rerun build/audit; preserve intentional exclusions; restore last known good artifact on failure |
| Grounded metadata/thumbnail draft | Automatic preparation, preview, scoring explanations and review packaging | Current final-text review remains authoritative; new text invalidates a prior fingerprint |
| Published metadata change | Reviewed during the pilot; later eligible for standing authorization for a narrowly versioned action class after the promotion criteria below pass and that policy is approved | Snapshot, reviewed exact payload, revision check, limited batch, read-back, cooldown, reversible receipt and emergency stop |
| Factual/editorial uncertainty | Automatic evidence gathering and one actionable blocked item | No automatic invented locality, date, speech, canonical migration, deletion or public/private status change |

Operational healing should restore collection and reconcile interrupted work.
An expired/revoked permission becomes one actionable authorization item.
Separate transient throttling from exhausted daily quota: eligible 5xx/429 and
rate-limit responses receive bounded backoff with jitter and `Retry-After`;
daily `quotaExceeded` defers work until the relevant reset and records the next
eligible time. Invalid requests and missing permissions are terminal for that
attempt. Classify provider error reasons as well as HTTP status. For an
interrupted upload, query/resume the existing resumable session or reconcile its
outcome before creating another upload. Do not apply a generic write retry to a
possibly successful operation.
[Error definitions](https://developers.google.com/youtube/data/reference/rest/v3/errors),
[upload recovery](https://developers.google.com/youtube/data/guides/using_resumable_upload_protocol).

 Quarantine corrupt state and retain the last good snapshot;
do not reset it to a plausible empty state. Concurrency needs locks/leases and
an idempotency key over asset, source revision, policy and proposed payload.

For a remote metadata update, read the current resource, preserve required fields,
write only intended parts, then read it back. `videos.update` replaces mutable
properties within the specified `part`: a property omitted from the request body
has its existing value deleted (exceptions: `status.selfDeclaredMadeForKids`,
`status.containsSyntheticMedia`), so a partial-looking snippet erases tags or
description. It costs 50 units in the applicable quota bucket. Before wiring
`QuotaManager` into production, reconcile its full cost model: current Google
documentation lists separate upload and search buckets, each with a default
100 calls/day at one unit per call, alongside 10,000 daily units for other
endpoints. The existing upload=1600 and search=100 constants are not a current
model (replaced by the per-bucket model in `quota/manager.py`, #73 and #80). Budget by project, API and bucket, with brand allocations underneath;
Analytics, Reporting and Search Console need their own verified limits. Read the
actual configured project limits during implementation; do not assume every
project has the defaults. Reserve quota atomically before dispatch, reconcile
attempts, and account for failed requests under the provider's rules. Merely
adding `VIDEO_UPDATE = 50` is insufficient.
[Current quota model](https://developers.google.com/youtube/data/determine_quota_cost).
Today the
uploader has no `videos().update`, no read-before-write and no read-back
(`uploader.py:281-305`). Reserve budget for verification and recovery. A
timed-out write needs reconciliation before retry, and rollback must not
overwrite a later human edit (compare `snippet` read-back against the snapshot,
not just the receipt).
[YouTube update contract](https://developers.google.com/youtube/data/reference/rest/v3/videos/update).

For optimization experiments, start with configurable pilot defaults of one
eligible published change per channel per day, one active experiment per asset,
and a seven-day minimum cooldown. These are proposed operating policies, not
scientifically established optima. Confirmed delivery failures use a separate
recovery policy so an experiment cooldown does not prevent restoration. No
auto-promotion on thin, stale, mismatched or missing evidence; noisy CTR movement
alone cannot trigger restoration.

Promote an action class from reviewed to automatic only when its versioned
policy specifies eligible assets/fields, excluded cases, batch and quota limits,
review requirements, rollback conditions and an emergency stop. Before promotion,
all failure-injection cases must pass, every pilot write must have a matching
read-back and durable receipt, and there must be no unresolved unauthorized
change, duplicate write or rollback defect. Declare the pilot's observation
window and sample requirement before it starts; insufficient activity keeps the
class in reviewed mode. Explicit approval of that bounded policy grants standing
authorization within its scope. It does not waive factual checks or authorize
other action classes. Collection, diagnosis and draft preparation can operate
automatically once configured; ambiguous facts still require review.

## Implementation units and acceptance

These units are ordered by dependency, not calendar estimates. Each unit lands
as one or more reviewable PRs under the 1000-line sum cap. Independent units can
be authored concurrently; shared files and consumer contracts determine stacking.
Code changes include their matching documentation in the same change.

Status, 2026-10-28 (America/New_York): merged are S0a (#71, #78), S0b (#69), S0c
(#72, #73, #74, #76, #77, #80, #81, #82), S1b (#70), S1a (#75, #83), S3a (#85),
S3b (#86), S4a (#89) and the first S4b rule (#92). The live check behind #76
passed: playlist reads return private videos with their real title and
description. S3a's read-only live smoke passed: one `collect run` collected
search terms and reach rows, and skipped Search Console with no site configured.
S2 has reach ingestion (#84) and a redacted report sample (#97): ctr is stored
with an unverified rate unit until that sample is run against the live token, and
title attribution is not started. S8a has `collect run` (#88), `collect report`
(#90) and SOPS-backed credentials for both (#96); recovery outcomes are not
reported because source health keeps only the latest state, and nothing
schedules either command yet. S6 has an offline audit of a built site (#98); live
checks and store ingestion are not started. S4b has its Search Console rule
(#92); reach rules wait on the S2 rate-unit proof and website/technical rules on
S6 ingestion. S5's first action class is decided: Waypost emits the reviewed
title payload as a Framegrade retitle worklist with a fingerprint, Framegrade's
existing retitle tool applies it after a human reviews its dry run, and Waypost
records Framegrade's audit log and revert file; it is not built yet. S7 and S8b
are not started. The baseline text in this document is otherwise unchanged
and describes the code as of the plan revision.

Waypost owns evidence interpretation and policy; Framegrade/other consumers
execute and display decisions; site repositories own templates and deployment.
A consumer inventory precedes cross-repo edits. Runtime databases stay outside
the tracked product tree. Reuse the cadence store's transactional patterns, with
explicit durability, migrations, recovery and rebuildable projections. Append-only
triggers protect history but do not establish crash durability or remote success.

| Unit | Priority / dependencies | Deliverable | Required proof before enabling |
| --- | --- | --- | --- |
| S0a: honest recommendations | P0; independent | Separate API limits, editorial advice and measured associations. Replace uncalibrated prediction confidence with explicit evidence state and, if useful, a separately named heuristic score. Predictions stay unavailable until a validated method exists; setting confidence to zero only for empty history is insufficient. Reconcile title/tag policy and its docs. Preserve selector independence from `seo_optimizer`. | Empty, sparse, populated, stale and mixed-source cases; no fabricated gain or probability; compatibility/migration coverage; existing title/provenance tests green |
| S0b: independent documentation corrections | P0; independent subset only | Correct Analytics-vs-Reporting wording, scope claims, retired autofix settings and misleading generic rollback claims. Keep accurate cadence rollback documentation. S0a/S0c carry their own changed-contract docs; S0b does not pre-document those APIs. | Diff, format and link review; applicable existing documentation-contract checks. Do not add prose-grep tests merely to mirror wording |
| S0c: transport and quota contracts | P0; independent | Shared typed errors and bounded retry policy; exhausted daily quota defers, transient failures back off, uploads resume/reconcile before retry. Replace obsolete quota costs with project/API/bucket accounting and per-brand allocations. Adopt helper in existing collectors and integrate upload recovery without blind repeat inserts. | Fake clients cover status/reason combinations, reset/DST boundaries, Retry-After, budget reservation races, failed requests and ambiguous upload outcomes; matching docs |
| S1a: collection contracts and new storage | P0; S0c for integration | Versioned bounded observations, checkpoints and source health; transactional SQLite with migrations, durable commits, recovery and replayable projections. Reuse `SyncOutcome`/`CycleRunRecord` vocabulary. | Duplicate/revised/late reports, interrupted transactions, migration/recovery, tenant isolation and Windows checks; property tests for no double counting and unknown staying unknown |
| S1b: existing event-store crash safety | P0; independent, stack if files overlap | Preserve JSONL for existing manager, analytics and Metricool consumers. Use format-preserving atomic replacement for rewrites, explicit append recovery, and reconstructable indexes; never serialize the JSONL stream as a JSON document with `atomic_write_json`. Avoid an unrelated store migration. | Existing consumers remain compatible; crash between data and index writes; truncated append; concurrent writers; restart/rebuild produces the correct event set |
| S2: reach and title attribution | P0; S1a | Extend the existing probe into ingestion. Inventory Framegrade's serialized `title_decision` and publish-state records; link the chosen template/title revision and effective interval to the returned video ID in local durable state. An absent custom field in YouTube's request body proves no local attribution gap. Reuse existing records; add only missing linkage. | Real redacted report replay and rate-unit proof; basic/combined deduplication; no cross-revision attribution; publish/restart/reconciliation linkage; live capability check before enabling |
| S3a: YouTube search demand | P1; S1a | Supported search-term queries using the existing Analytics client; bounded windows and explicit completeness/provenance | Pagination, omitted terms, delayed data, API combinations, budget/auth failures and brand isolation; read-only live smoke |
| S3b: Search Console | P1; S1a; independent of S3a | Owned-site query/page evidence with valid dimensions per search type, finalized windows and recorded aggregation/completeness; partial data marked using response metadata | Supported type/dimension combinations, pagination, final/partial windows, missing rows, property ownership and quota/auth failures |
| S4a: first YouTube opportunities | P1; S0a/S1a/S3a | Deterministic search-demand and metadata-relevance opportunities, stable IDs, evidence links and explicit missing-evidence reasons. Add reach/CTR rules as S2 becomes available. No dependency on S3b or website audits. | Golden positive/counterexample fixtures, deterministic reruns, factual eligibility and no market-volume/causal-lift claims |
| S4b: expanded opportunities | P1; S4a plus each relevant source (S2/S3b/S6) | Add reach, website and technical opportunities as their inputs arrive; unavailable sources cannot block supported rules | Per-rule eligibility/completeness and cross-source isolation; no fabricated findings from missing inputs |
| S5: repair executor | P1; S0c/S1a/S4a | Durable action journal, exact reviewed payload, read-before-write/read-back, conflict detection, bounded retry/reconcile and restoration. Inventory and test the actual consumer approval gate; Waypost's review fingerprint alone is not approval. Preserve intentional human edits. | Failures around every write, ambiguous timeout, concurrent edits, quota exhaustion, approval/review mismatch, read-back failure and failed rollback; meaningful uploader coverage replacing applicable xfails |
| S6: technical site adapter | P1; S1a/S4a | Inventory existing site checks; ingest build audits and bounded live checks; propose fixes in source repositories. Search Console is optional enrichment. | Broken-link/noindex/canonical/sitemap/schema fixtures, build and rendered checks, deployment verification and intentional exclusions |
| S7: learning and experiments | P2; S2/S5 | Tested production consumer for qualified per-format evidence; native title/thumbnail experiment companion and atomic manifest; measurement/retention policy | Baseline retained with insufficient data; experiment provenance, watch-time/retention guardrails; observational labels and pilot promotion criteria |
| S8a: daily read-only report | P1; S1a/S4a | Extend existing report/status surfaces with top opportunities, source health, recovery outcomes, missing evidence and next evaluation. Use the existing scheduler owner and review any schedule artifact in its owning repo. | Never-ran/unconfigured/partial/error fixtures; deduplicated incident/recovery reporting; automated collection-to-report acceptance; no dependency on S5 or S3b |
| S8b: repair/operator integration | P1; S5/S8a | Review queue, action receipts, restoration outcomes and policy eligibility; apply controls only after their action class qualifies | Complete preparation-to-read-back acceptance with approval, stop and rollback exercised |

Sequence after implementation is separately authorized:

1. S0a, the independent S0b corrections, S0c and S1b may progress concurrently
   where ownership permits. Matching behavior documentation stays with its code.
2. Integrate S1a after S0c; then S2, S3a and S3b can progress independently.
3. Deliver S4a and S8a as soon as YouTube search evidence is usable. S2 adds reach
   rules when ready; Search Console and technical adapters do not block this.
4. Expand through S4b/S6 and introduce reviewed repairs through S5/S8b. S7 and
   automatic public-change classes follow their qualification criteria.

The first milestone is a trustworthy daily opportunity report with working data
recovery: fresh evidence or explicit failure/unknown states, source-linked
suggestions, restart-safe collection and a next evaluation time. It does not
require published metadata changes or all website integrations.

S2 reuses merged probe PRs #61/#64 and inventories current consumer state before
adding attribution. S7 intentionally revises the no-consumer invariant with a
real tested reader; simply writing `learned_config.json` is not completion.
Existing factual review, fallback and cadence controls remain integration tests.

## Recommended decisions carried into this revision

| Decision | Recommendation recorded in the plan |
| --- | --- |
| Automation | Automatic collection, reconciliation, diagnosis and drafts; standing authorization per qualified repair class after explicit policy approval |
| Published changes | Keep the existing consumer approval boundary during the pilot and prove it in integration tests; use the promotion criteria above for later automation |
| Change rate | Configurable one-change/day and seven-day cooldown defaults for experiments; separate confirmed-failure recovery policy |
| Credentials | One SOPS-backed provider contract for the reference deployment; no connector-by-connector storage choice |
| Commercial coverage | First-party evidence and useful opportunities first; commercial subscriptions and free-tier credits remain optional |
| Older backlog | Reconcile completed/superseded decisions before reopening them. The older Python >=3.10 proposal is superseded by current `pyproject.toml` >=3.12; retain that floor unless a separately justified compatibility change is proposed |

## Operating cadence, cost and success criteria

- Proposed routine: daily incremental collection/reconciliation; audits on site
  build and weekly bounded live checks; weekly deduplicated opportunities and
  experiment review. Poll delayed reports at an appropriate interval, not in a
  tight loop. Measure data freshness separately from scheduler heartbeat
  (`last_run.json` is written even when the cycle fails: freshness comes from
  `SyncOutcome`, not from the file's mtime).
- Local deterministic rules and existing CPU jobs form the required path.
  LLM drafting, paid tools, free-tier credits and browser automation are optional.
  Enforce request/page/storage budgets per source; defer optional work before
  starving publishing or recovery. Cache requests and retain raw evidence only
  for a documented retention period.
- Acceptance for operational automation: every action has a durable receipt,
  replay makes no duplicate write, failure injection proves restoration, and
  stale/unconfigured collectors cannot report success. Report recovery rate and
  human interventions from actual incidents, with counts and windows.
- Acceptance for SEO usefulness: a reviewer can trace every recommendation to
  fresh evidence or an explicitly labelled hypothesis; rejected suggestions have
  reasons; track review acceptance, useful opportunities completed, verified
  technical defects resolved, and recurrence.
- Acceptance for improvement: compare settled, nonoverlapping windows and matched
  format/source cohorts. Report search clicks, search-referred views, watch time,
  and measured CTR with denominators. Freeze the evaluation plan before a change,
  control repeated peeking/multiple comparisons, and use native randomized tests
  when available. An observed gain remains observational without a suitable
  experiment. No promised percentage uplift or universal score target.

## Boundaries and remaining verification

The next implementation session must verify deployed versions and actual source
health, then perform a read-only capability check for configured channels/sites.
Reconcile existing reach-report jobs and their latest outputs before creating
anything. A created job is not a completed report; do not duplicate jobs or
assume reports already exist.

No arbitrary scheduled SEO rewrites, invented locations, keyword stuffing,
auto-generated doorway pages, fake backlinks, bulk link outreach, or automatic
canonical/redirect changes. Useful watch pages should add real information and
navigation, not multiply thin pages to chase queries.
[Google spam policies](https://developers.google.com/search/docs/essentials/spam-policies).

Website runtime health, consumer integration, native experiment eligibility,
account-specific quotas and SEO lift remain unverified until the acceptance
checks above run. The plan is source-verified and vendor-documentation-checked;
it does not claim an exhaustive commercial feature audit or live production
certification.

## Evaluation findings and review reconciliation

The prior evaluation recorded verification at 2026-10-27T15:26:23Z against source
baseline `dddc9ec`: 24 cited paths, 14 behavioral claims, 11 inventory findings,
nine vendor pages and the eight-file test rerun. Those counts describe that
historical evaluation, not checks newly executed by this documentation revision.
The subsequent review checked relevant source paths, original test logs, current
GitHub state and current Google API documentation. Corrections are incorporated
in the operative sections above:

- PR #67 is merged; the source baseline remains explicit.
- Review integrity and consumer approval are distinct. The uploader does not
  establish the presence of a caller approval record merely by verifying a review.
- Both 152-test invocations are retained with their own command options and exit
  codes; the later coverage failure does not invalidate the earlier scoped pass.
- Local attribution must be inventoried in consumer decision/publish state.
  YouTube request-body fields are not evidence of what local state contains.
- Retry policy separates daily quota exhaustion from transient failures. The
  quota model must include current provider buckets before production adoption.
- JSONL serves manager/analytics consumers as well as Metricool. S1b preserves
  its format; new SQLite durability requires more than append-only triggers.
- Confidence remains uncalibrated even with populated patterns. S0a distinguishes
  evidence, heuristics and validated predictions throughout the contract.
- S0b retains independent documentation corrections, while contract changes carry
  their own docs. Dependencies now determine implementation order; S3 and S8 are
  split so a useful daily report can arrive before broader integration.
- SOPS is the deployment credential choice. Automation policy, pilot conditions
  and confirmed-failure recovery are explicit recommendations, not enabled work.

The independent documentation findings remain: distinguish Analytics from
Reporting APIs; correct outdated OAuth-scope claims; remove retired autofix
configuration and unsupported generic rollback claims while retaining documented
cadence rollback. Reuse existing fake clients, report/status surfaces, evidence
vocabulary and hypothesis tests where they exercise a real contract.

Validation for this revision is documentation-scoped: review the final diff,
relative links, Markdown structure, dependency references and recommendation
coverage. Historical runtime tests are not rerun solely for prose changes.
Implementation tests and live account acceptance remain future work. No runtime
code, credentials, schedule, deployment, upload or published metadata is changed.
