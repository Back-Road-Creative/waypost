# YouTube reach report ingestion

`seo_manager.collection.youtube_reach.YouTubeReachAdapter` reads thumbnail
impressions and impression click rate from the YouTube Reporting API's reach
reports and returns them as collection-store observations. The Analytics API v2
does not return these; the reach probe (`youtube_reach_probe`) says whether a
token can read them.

## What is read

- Existing reporting jobs of type `channel_reach_basic_a1` (dimensions: date,
  channel, video) and `channel_reach_combined_a1` (adds traffic source type and
  detail, operating system, device type). Other job types are ignored.
- Each report's CSV, by header name, never by column position.
- Metrics `video_thumbnail_impressions` and `video_thumbnail_impressions_ctr`.
  An empty or malformed cell is `None` (unknown), never zero.

Each row's `date` (YYYYMMDD) becomes one America/Los_Angeles day (23 to 25 hours
across a DST change). `reported_at` is the report's `createTime`, so a report
YouTube re-issues for the same window lands as a new revision; the earlier
one stays in history.

## Basic and combined are never summed

A video appears in both report types. They are stored as separate sources,
`youtube_reach_basic` (subject = video id) and `youtube_reach_combined`
(subject = `video|traffic_source_type|traffic_source_detail|operating_system|device_type`,
replaced by `sha256:<32 hex>` over 200 characters, with the raw key in
provenance). Nothing adds the two, and nothing here computes a total.

## Rate unit is unverified

The ctr cell is stored exactly as reported. Its unit (fraction or percent) and
its denominator are not established until a real, redacted report proves them,
which is a step only JP can run. Until then every ctr observation carries
`rate_unit=unverified;report=<id>;type=<type>` in provenance. Do not normalise
or compare it with other rates by magnitude.

## Read-only, no job creation

The adapter never creates a reporting job; that stays the probe's
`--create-job`. With no reach job, or no report newer than the checkpoint, it
returns EMPTY and the checkpoint it was given. It does not write the store.

## Checkpoint

Stream `reach_reports` (source `youtube_reach`); the cursor is the newest
`createTime` handled. The next call asks for reports created after it
(`createdAfter`, also filtered client-side), oldest first, at most
`max_reports` (default 30) per call; more waiting means PARTIAL. Reports that
share a `createTime` are never split across calls, so the cap can be exceeded
by that group.

- A report missing a required column is skipped for good and the outcome is
  PARTIAL; no rows are taken from it.
- A transient download failure holds the cursor before that report, so the
  next run retries it. Rows re-read this way are not duplicated in the store.
- Quota and auth errors propagate at once.

## Not done yet

- Title and template attribution: joining a video to the title and thumbnail
  template that produced it needs a Framegrade inventory.
- The `TemplateObservation` link (title_selection.py) that would consume these
  observations.
- Normalising the ctr rate, which waits on the unit check above.
