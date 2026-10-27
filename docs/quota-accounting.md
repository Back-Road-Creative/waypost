# Quota accounting

`seo_manager.quota.QuotaManager` budgets API quota by **(project, API, bucket)**,
with optional per-brand allocations underneath a bucket. Every brand using a
Google Cloud project shares that project's buckets. State lives in
`<storage>/<project_id>/quota_usage.json`, under the group-writable file lock.

## Buckets and defaults

Defaults follow Google's YouTube Data API v3 quota documentation (checked
2026-10-27). **Verify your project's actual limits** in the Google Cloud
console: they vary by project and can change. Every limit is overridable.

| Bucket (`api/bucket`) | Default | Operations |
| --- | --- | --- |
| `youtube_data/default` | 10,000 units/day | `videos.update`, `thumbnails.set`, `playlists.insert`, `playlistItems.insert` 50; `videos.list`, `channels.list`, `playlistItems.list`, `playlists.list` 1 |
| `youtube_data/videos.insert` | 100 calls/day | `videos.insert`, 1 per call |
| `youtube_data/search.list` | 100 calls/day | `search.list`, 1 per call |
| `youtube_analytics`, `youtube_reporting`, `search_console` (`default`) | unknown | no assumed number |

A bucket with no limit is **unbudgeted**: counted and listed in
`generate_report()["unbudgeted"]`, never refused. Override at construction:

```python
QuotaManager(
    "my-project",
    limits={"youtube_data/videos.insert": 6, "search_console/default": 1200},
    brand_allocations={"youtube_data/videos.insert": {"brand-a": 4, "brand-b": 2}},
)
```

A brand with an allocation is capped at it; brands without one share what the
allocations leave over (allocations summing past the limit raise `ValueError`).
An unknown method needs `api=` on `reserve`; its cost is 1 call unless `cost=`.

## Reserve, then settle

```python
res = qm.reserve("videos.update", brand_id="brand-a")  # QuotaExceededError if full
# send the request, then one of:
qm.settle(res, Outcome.USED)     # succeeded
qm.settle(res, Outcome.FAILED)   # reached Google and failed: charged
qm.release(res)                  # never left the process: refund
```

`reserve` checks and records the cost in one critical section under the file
lock, so two concurrent reservers cannot both pass when only one fits; the cost
counts as used at once. `settle` returns `False` (no change) if the reservation
is no longer outstanding (settled already, or the quota day reset);
`actual_cost=` corrects a charge.

**Failed requests are charged by default**: Google charges a request that
reaches the API even if it fails (at least 1 unit even if invalid). Pass `charge_on_failure=False` only if you know better. A reservation
never settled (a crashed process) stays charged until the day resets.

## Reset rule

The quota day is the **America/Los_Angeles** day (`zoneinfo`), ending at 00:00
Pacific (07:00 or 08:00 UTC by DST), not UTC midnight. `next_reset()` gives it.

## Legacy API and old state

`track_operation`, `check_quota`, `get_usage` and the other old methods still
exist and describe the shared `youtube_data/default` pool. Unknown names such
as `"upload"` charge that pool; known methods and aliases (`video_upload`)
charge their own bucket. `OperationCost.VIDEO_UPLOAD` and `VIDEO_SEARCH` are
now 1 call, not the obsolete 1600 and 100 units.

State written before buckets existed (no `version` key) is migrated: if it was
written on today's Los Angeles day, its total and per-brand usage move into
`youtube_data/default` (not split across buckets) and the file is rewritten in
the new format. Older state is dropped. An unreadable file starts a fresh day
with a warning.
