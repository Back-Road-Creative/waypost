# YouTube search terms (read-only)

`seo_manager/collection/youtube_search_terms.py` collects, per video, the top
search terms that led viewers to it, through the Analytics API v2 client that
`YouTubeAnalyticsSync` already builds. It only reads; the collection cycle owns
writing to the store.

## Request

One `reports().query()` per video, through `retry_read`:

| Parameter | Value |
| --- | --- |
| `ids` | `channel==MINE` |
| `metrics` | `views,estimatedMinutesWatched` |
| `dimensions` | `insightTrafficSourceDetail` |
| `filters` | `video==<id>;insightTrafficSourceType==YT_SEARCH` |
| `sort` | `-views` |
| `maxResults` | 25 by default |

## Window

Dates are Pacific calendar dates. `endDate` = today (Pacific) minus
`settle_days` (default 3, so late-reported data settles); `startDate` =
`endDate` minus `window_days` (default 28) plus 1. Observation windows run from
Pacific midnight of `startDate` to Pacific midnight of the day after `endDate`,
so a daylight-saving change cannot shift a boundary. The checkpoint (stream
`search_terms`) stores the `endDate` collected; if it is already at or past the
target `endDate`, no API call is made.

## Observations

Source `youtube_analytics_search_terms`. Subject `<video_id>|<term>`, metrics
`views` and `estimated_minutes_watched`; a column the API did not return is
`None`, never 0. A subject over 200 characters becomes
`<video_id>|sha256:<32 hex>` with the raw term in provenance (truncated).

## Completeness

Each video also gets a `search_term_rows` observation (subject = video id)
whose value is the number of rows returned and whose provenance reads
`maxResults=<n>;at_cap=<true|false>`. The list is the top referrers, not the
whole set. A term that is absent is unknown, not zero demand; no rows are
emitted for unseen terms.

## Failures

Quota and auth errors raise at once. A video that stays transient or is
rejected as invalid after retry is skipped and the outcome is `PARTIAL`. A partial run does not advance the
checkpoint, so the next run retries the whole window; the store keeps the
repeated reports as revisions, not double counts. If
every video fails, the last error is raised. No videos configured returns
`EMPTY` without calls.

## Quota and limits

Analytics API quota is separate from Data API quota and must be budgeted
separately: this costs one Analytics query per video per collection. The
numbers are observed referrals from YouTube search. They are not total search
volume and not keyword difficulty.
