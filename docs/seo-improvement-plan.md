# Waypost SEO opportunities and automatic recovery plan

Prepared 2026-10-27, America/New_York. **Proposal only; no runtime changes.**
Requirement: identify SEO opportunities, improve results, recover automatically,
and use free capabilities wherever practical. This is a product plan, not agent
governance or authorization to publish changes to connected accounts.

## Recommendation

Build a measured loop: collect search evidence → identify an opportunity →
prepare a grounded change → apply within an explicit policy → verify the remote
result → measure outcomes → retain, hold, or restore the previous version.

Waypost has valuable parts of this loop, especially factual review, provenance,
title selection, and cadence rollback. It does **not** yet offer an integrated
SEO opportunity engine or general self-healing SEO. Prioritize first-party data,
honest recommendations, and operational recovery before more title generators.

“Free” means no required subscription, paid API, or paid model inference. Existing
compute, storage, development, API quotas, and occasional operator time still
have costs. Commercial free tiers are optional cross-checks, not dependencies.
Full parity with commercial keyword/backlink databases is not a credible free
commitment. Useful owned-channel and owned-site coverage is achievable.

## Verification scope and baseline

Reviewed public Waypost `main` at
[`dddc9ec35e6cbe3d6a93753e954b6b7d04fa7ddb`](https://github.com/Back-Road-Creative/waypost/tree/dddc9ec35e6cbe3d6a93753e954b6b7d04fa7ddb).
GitHub returned that SHA as current during this review; its `ci` run was successful.
No open Waypost PRs were returned at the time of the check. These are dated
observations, not promises about later repository state.

Source references below are relative to this document and refer to that baseline.
The old private workspace package is not the source of truth. Earlier improvement
plans contain completed items; do not recreate their retired auto-applier or
duplicate the existing reach probe.

| Capability | Verified evidence | Implication |
| --- | --- | --- |
| Fact-bound metadata and upload checks | [final review](../seo_manager/analytics/final_text_review.py), [uploader](../seo_manager/youtube/uploader.py), [contract](final-text-review.md) | Reuse these checks. Current review always requires a human gate. This plan does not silently remove it. |
| Evidence-aware title selection | [selector](../seo_manager/analytics/title_selection.py), [contract](title-selection.md) | Already separates format and search/browse evidence and rejects insufficient, stale, or wrong-denominator observations. A relative lift rule is not a causal experiment. |
| Cadence recovery | [controller](../seo_manager/analytics/volume_controller.py), [decision persistence](../seo_manager/analytics/cadence_decisions.py) | Already has probation, cooldown, rollback and blind reversion. Reuse the pattern; do not describe it as a new feature. |
| Continuous learning | [daemon](../seo_manager/analytics/continuous_optimization_daemon.py), [contract](continuous-optimization-daemon.md), [no-writer test](../tests/unit/test_learned_config_has_no_writer.py) | Learned JSON is a snapshot with no production consumer. The misleading auto-applier was removed deliberately. |
| Metrics identity and missing data | [metric source](../seo_manager/analytics/metric_source.py), [evidence v2](../seo_manager/analytics/evidence.py) | Keep unknown distinct from zero, and keep Facebook/YouTube counters distinct. Card CTR cannot justify title or thumbnail changes. |
| Thumbnail reach acquisition | [probe](../seo_manager/analytics/youtube_reach_probe.py) | Already discovers report types, can create jobs, and inspects report columns. It reports capability; it does not ingest observations into title learning. |
| Recommendation quality | [optimizer](../seo_manager/analytics/seo_optimizer.py), especially `get_optimal_title_length`, `generate_recommendations`, `predict_performance` | Empty history still returns a 50–70 title range and a “better CTR” suggestion. Prediction confidence is a heuristic score, not calibrated probability. |
| Legacy SEO rules | [validator](../seo_manager/validation/youtube_best_practices.py), [YouTube adapter](../seo_manager/platforms/youtube.py) | Universal display-length and preferred-tag-count claims need separation from actual API constraints and measured evidence. |
| Technical website SEO and search-demand collectors | Search of `seo_manager/` and `tests/` for Search Console/webmaster, Lighthouse, sitemap, robots, `YT_SEARCH`, `insightTrafficSource` | No implementation found for those collectors/audits. `canonical_url` as metadata is not a crawler. This finding is limited to Waypost; consuming sites may already implement parts. |
| Thumbnail experiments | [companion](thumbnail-test-and-compare.md) | Prepares variants and records selection, but does not retrieve variant-level experiment results. Expand the companion rather than inventing a winner from video-level CTR. |

### Checks actually performed

- Inspected a fresh isolated clone at the exact SHA above, relevant source,
  tests, docs, dependencies, and CI configuration.
- Ran eight explicit test files through the workspace pytest slot gate:
  `test_youtube_reach_probe.py`, `test_title_selection.py`,
  `test_thumbnail_ctr_provenance.py`, `unit/test_learned_config_has_no_writer.py`,
  `unit/test_daemon_cycle_run_evidence.py`,
  `unit/test_trend_observation_carries_its_evidence.py`,
  `unit/test_volume_controller.py`, and `unit/test_evidence_bundle.py`.
  **152 passed in 3.28 seconds.** Python 3.12.3 used an existing dependency
  environment; import inspection confirmed the reviewed clone supplied
  `seo_manager`. This was scoped verification, not a fresh-install or full-suite
  certification. No API calls were made by these tests.
- Reproduced the empty-history recommendation with all pattern lists empty:
  “Expand title to 50-70 chars for better CTR”, “target 8-12 tags”, and nonzero
  prediction `confidence` (~0.02) for an ordinary short example title. This is a
  diagnostic finding, not a shipped fix or a measurement of SEO performance.
- Checked the first-party vendor and platform documentation linked below.
  Product claims are vendor-documented capabilities, not hands-on benchmarks.

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
| [Ahrefs Free / Webmaster Tools](https://ahrefs.com/webmaster-tools) | Verified-site audits, search/backlink insights and analytics; page lists 5,000 crawl credits per month per project. | Optional independent owned-site audit/backlink review. No assumed free API or comprehensive competitor access. Import only supported exports with provenance. |
| [Screaming Frog](https://www.screamingfrog.co.uk/seo-spider/) | Free crawl covers up to 500 URLs; advanced configuration, saved crawls, JS rendering and API integrations are restricted. | Good manual spot-check. Automated audits should run against local build artifacts with open tools and bounded live checks. |
| [YouTube Studio native A/B tests](https://support.google.com/youtube/answer/16391400) | Up to three title/thumbnail combinations, selected using watch time; desktop/advanced-feature eligibility, with Shorts excluded. | Extend existing companion to title-plus-thumbnail manifests and explicit operator-recorded results. Keep Studio steps manual unless a supported API is verified. |

Copy the useful workflow—discovery, prioritization, change history, experiments
and recurring verification. Do not copy unexplained “SEO scores” or pretend
owned-channel queries measure market-wide search demand.

## Evidence sources to add or complete

1. **YouTube Reporting reach → persisted observations.** Google documents
   `channel_reach_basic_a1` and `channel_reach_combined_a1`, with
   `video_thumbnail_impressions` and `video_thumbnail_impressions_ctr`; combined
   includes traffic-source dimensions. This corrects broad “Studio-only” wording
   in existing docs: the Analytics API and Reporting API are different surfaces.
   The existing probe is the starting point, not evidence of a working importer.
   [Report definitions](https://developers.google.com/youtube/reporting/v1/reports/channel_reports).
   Establish rate units from the provider contract and a real report before
   normalizing; preserve the raw rate and denominator, never guess from magnitude.
   [Metric definitions](https://developers.google.com/youtube/reporting/v1/reports/metrics).

2. **YouTube search terms and traffic sources.** Query supported reports with
   `insightTrafficSourceType=YT_SEARCH` and `insightTrafficSourceDetail` using the
   documented request shape. Track views and supported watch-time metrics over
   bounded dates. Search terms represent observed referrals, not total search
   volume or keyword difficulty. Do not assume every dimension/metric combination
   is valid or every low-volume term is returned.
   [Dimensions](https://developers.google.com/youtube/analytics/dimensions),
   [request examples](https://developers.google.com/youtube/analytics/sample-requests).

3. **Owned-site Search Console.** Collect query/page/date/device/country and
   supported search-type slices with clicks, impressions, CTR and position.
   Page through results, preserve filters and completeness metadata. The API
   explicitly returns top rows and does not guarantee every row; an omitted query
   is not zero demand. Never add together overlapping query/page aggregates.
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
   says its field-data integration is planned for removal, so keep lab/field
   adapters separate and treat absent field data as unknown.
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
Store credentials only through the existing managed-secret mechanism.

## Opportunities Waypost should produce

Every opportunity includes an ID, asset, observed problem, evidence links and
window, proposed change, priority reasons, effort/risk, automation eligibility,
validation method, and next evaluation time. Show “needs evidence” explicitly.
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
work. YouTube says tags usually have minimal discovery value, except useful cases
such as misspellings. Existing advice to reach a fixed number of tags should not
outrank measured opportunities. [YouTube guidance](https://support.google.com/youtube/answer/146402).

## Automatic recovery and controlled changes

The proposed lifecycle is `detected → qualified → prepared → eligible → applied
→ verified → observing → retained / reverted / blocked`. Each transition records
its inputs and reason. A write receipt is not proof the remote state changed;
a read-back is not proof traffic improved.

| Action class | Automation target | Required boundary |
| --- | --- | --- |
| Read, reconcile and retry | Automatic daily collection, backfill, checkpoint recovery and idempotent replay | Per-source quota budget, bounded retries with jitter, Retry-After handling, duplicate suppression and last-success age |
| Deterministic local repair | Automatic regeneration of derived files and draft fixes from approved facts | Change the source of truth; rerun build/audit; preserve intentional exclusions; restore last known good artifact on failure |
| Grounded metadata/thumbnail draft | Automatic preparation, preview, scoring explanations and review packaging | Current final-text review remains authoritative; new text invalidates a prior fingerprint |
| Published metadata change | Initially reviewed; later eligible for narrowly versioned policy authorization if approved separately | Snapshot, reviewed exact payload, revision check, limited batch, read-back, cooldown, reversible receipt and emergency stop |
| Factual/editorial uncertainty | Automatic evidence gathering and one actionable blocked item | No automatic invented locality, date, speech, canonical migration, deletion or public/private status change |

Operational healing should restore collection and reconcile interrupted work.
An expired/revoked permission becomes one actionable authorization item, not an
endless retry loop. Quarantine corrupt state and retain the last good snapshot;
do not reset it to a plausible empty state. Concurrency needs locks/leases and
an idempotency key over asset, source revision, policy and proposed payload.

For a remote metadata update, read the current resource, preserve required fields,
write only intended parts, then read it back. YouTube's update operation can
replace mutable fields within the specified part; partial-looking payloads can
erase omitted values. It currently costs 50 quota units per call. Reserve budget
for verification and recovery. A timed-out write needs reconciliation before
retry, and rollback must not overwrite a later human edit.
[YouTube update contract](https://developers.google.com/youtube/data/reference/rest/v3/videos/update).

Start with a proposed cap of one eligible published change per channel per day,
one active experiment per asset, and a seven-day minimum cooldown. These are
conservative initial product policies, not scientifically established optima;
review them against traffic and operational capacity. No auto-promotion on thin,
stale, mismatched or missing evidence. A severe delivery regression can trigger
restoration immediately; noisy CTR movement alone cannot.

## Implementation units and acceptance

These units are ordered by dependency, not calendar estimates. Split large units
into independently reviewable changes. Waypost owns evidence interpretation and
policy; Framegrade/other consumers execute and display decisions; site repositories
own templates and deployment. A consumer inventory precedes cross-repo edits.

| Unit | Priority / dependencies | Deliverable | Required proof before enabling |
| --- | --- | --- | --- |
| S0: honest recommendations | P0; independent | Separate API limits, style suggestions, observed associations and predictions. Remove empty-history “better CTR” claims; replace uncalibrated confidence with an explicitly heuristic label or abstention. Correct Reporting-vs-Analytics documentation. | Red tests for empty/sparse/mixed-source data and unsupported gain claims; green existing provenance/title tests; compatibility and docs review |
| S1: collection contracts | P0; independent | Versioned bounded observations, checkpoint/replay store, source health and identity | Tests for duplicate/revised reports, late data, timezone boundaries, unknown vs zero, schema drift, interrupted writes and tenant isolation |
| S2: reach and title attribution | P0; S1 | Extend existing probe into ingestion; persist publish-time template ID, title revision and effective interval; produce selector evidence | Replayed real redacted report, unit conversion proof, no double counting basic+combined reports, no attribution across a title change, supported live channel capability check |
| S3: search-demand collectors | P1; S1 | YouTube search terms plus owned-site Search Console connector | Pagination, omitted rows, incomplete dates, quota/auth failures, supported API combinations, brand isolation; read-only live smoke using authorized credentials |
| S4: opportunity engine | P1; S0–S3 | Deterministic rules, factual eligibility, deduplication, ranked explanations and “needs evidence” queue | Golden fixtures for every opportunity above and counterexamples; identical rerun yields same IDs; no claim of market volume or causal lift |
| S5: repair executor | P1; S1/S4 | Durable action journal, preparation, current review gate, revision checks, retry/reconcile, rollback and stop controls | Failures before/during/after write, retry after ambiguous timeout, concurrent edits, quota exhaustion, review mismatch, failed read-back and failed rollback all tested |
| S6: technical site adapter | P1; S1/S4 | Inventory existing site checks, ingest local build audits and bounded live results, propose source fixes | Broken-link/noindex/canonical/sitemap/schema fixtures; build and rendered page checks; deployed asset verification; intentional exclusions survive |
| S7: learning and experiments | P2; S2/S5 | Real consumer for qualified per-format evidence; native-test companion for titles and thumbnails; measurement/retention policy | No-data baseline retained; genuine experiment result provenance; observational changes labelled as such; watch time/retention guardrails; abstention for insufficient evidence |
| S8: operator view and schedule | P1 read-only after S1/S4; apply after S5 | Top opportunities, recovery actions, stale sources, review queue, next evaluation and weekly outcome digest | Offline fixtures for never-ran/unconfigured/partial/error states; one notification per incident with recovery closure; a complete collect-to-read-back acceptance run |

S2 overlaps the existing reach-probe follow-up: reuse PRs #61/#64 and any ongoing
template-recording work. S7 must replace the no-consumer invariant intentionally,
with a tested production reader; merely writing `learned_config.json` again is
not completion. Existing safe fallback, factual review and cadence controls stay
part of the integration tests.

Start S0/S1 and read-only S8 work first. Complete S2 before claiming title learning.
Ship S3/S4 discovery before enabling S5 public writes. Technical fixes can move
through S6 independently when source facts and existing site ownership are clear.

## Operating cadence, cost and success criteria

- Proposed routine: daily incremental collection/reconciliation; audits on site
  build and weekly bounded live checks; weekly deduplicated opportunities and
  experiment review. Poll delayed reports at an appropriate interval, not in a
  tight loop. Measure data freshness separately from scheduler heartbeat.
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
