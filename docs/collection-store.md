# Collection store

`seo_manager.collection` holds the contracts for collected evidence and the
SQLite store that records it. Waypost owns interpretation; this layer only
records what a source reported, when, and how the attempt ended.

## Contracts (`collection/contracts.py`)

All are frozen dataclasses carrying `schema_version` (currently 1). Invalid
input raises `ValueError`.

- `Observation`: tenant, source, subject, metric, an aware UTC window
  (`window_end > window_start`, at most 366 days), `value`, `reported_at`, optional
  `provenance` (1-500 characters). Identifiers are non-empty and at most 200
  characters. `value` is a finite number within +/-2**53, or `None`.
  **`None` means unknown and is never coerced to zero.**
- `Checkpoint`: tenant, source, stream, an opaque cursor (at most 2000 characters),
  `updated_at`.
- `SourceHealth`: tenant, source, `last_attempt_at`, `last_success_at`,
  `last_outcome` (a `SyncOutcome`, or `None` = unknown), `error_class`
  (for example `quota_exhausted`, `auth`, `transient`), `next_eligible`. Ages are
  computed on read (`age_since_success(now)`), never stored.

## Store (`collection/store.py`)

`CollectionStore(path)` takes a required path. Runtime databases live outside the
tracked product tree; the caller chooses the location. Only `init()` creates a
file; any other call on a missing file raises `StoreMissingError`.

### Revisions and projection

Every distinct report is appended to `observation_revisions` (append-only,
protected by triggers). The identity of a report is the observation key plus
`reported_at`, value and provenance, so reporting the same thing twice is a
no-op and nothing is double counted. A revised value adds a revision. The
`observation_latest` projection holds, per (tenant, source, subject, metric,
window), the revision with the greatest `(reported_at, value, provenance)`, a
total order, so arrival order does not matter. A late report for another window
touches only that window. `rebuild_projections()` recomputes the projection from
history, and `projection_matches_history()` checks the stored one against it.

### Durability

`PRAGMA synchronous=FULL`; a cross-process file lock (`<db>.lock`); one
`BEGIN IMMEDIATE` transaction per `write_batch(tenant, observations, checkpoint,
health)`, so observations, the checkpoint advance and the health update commit
together or not at all. Any exception rolls back. Append-only triggers protect
history from edits, not from a crash; the transaction and `synchronous=FULL`
do that. Connections are always closed (Windows cannot rename open files).

### Migrations

`PRAGMA user_version` is the schema version; `MIGRATIONS` is an ordered list of
steps applied inside one transaction. A database newer than the code raises
`SchemaTooNewError` and is left untouched: no downgrade, no reset.

### Recovery

A file that is not a readable SQLite database (failed `quick_check` or open)
raises `CorruptStoreError`. The file is kept as is, and a copy
`<db>.corrupt-<timestamp>` is made best-effort. The store never substitutes an
empty database for an unreadable one.

### Tenant isolation

Every read and write takes a tenant. A batch refuses an observation, checkpoint
or health record for another tenant, and every query filters on tenant.

## Collection cycle (`collection/cycle.py`)

`run_cycle(store, tenant, adapters, *, now)` asks each adapter in turn and returns
one `SourceRun` per source (`source`, `status`, `outcome`, `added`,
`error_class`). Adapters only fetch; the cycle is the only writer.

Adapter protocol: an object with `source: str`, `stream: str` and
`collect(tenant, checkpoint, now) -> (observations, checkpoint_or_None, outcome)`.
The cycle passes `store.checkpoint(tenant, source, stream)`. The outcome is
`FRESH` (complete), `PARTIAL` (a sub-request failed or a page/row cap was hit) or
`EMPTY` (succeeded, no rows). A plain 3-tuple is accepted as well as `CollectResult`.
A whole-source failure raises a `seo_manager.transport.TransportError` subclass
(a terminal one, or a `TransientError` that outlasted `retry_read`).

Per source:

- `deferred`: stored `next_eligible` is after `now`. No call, no write.
- `collected`: one `write_batch` with the observations, the checkpoint advance and
  the success health. Re-collecting the same report adds 0 revisions.
- `failed`: one `write_batch(health=...)` only. No observations, and the
  checkpoint does not advance. A non-transport exception from an adapter is a
  bug: it is recorded as `adapter_error` and the cycle moves on to the next adapter.

| Raised | `error_class` | `next_eligible` |
| --- | --- | --- |
| `QuotaExhaustedError` | `quota_exhausted` | `exc.next_eligible` |
| `AuthError` | `auth` | none |
| `TransientError` | `transient` | none |
| `InvalidRequestError` | `invalid_request` | none |
| `UnknownTransportError`, other `TransportError` | `unknown` | none |
| any other exception | `adapter_error` | none |

Every failure records `last_outcome=FAILED` and `last_attempt_at=now`, and carries
`last_success_at` over from the previous health (never reset, never set to now).
A success sets both times to `now` and clears `error_class` and `next_eligible`.

### Database location

`paths.collection_db()` is `$SEO_COLLECTION_DB` if set, else
`<data home>/collection.sqlite3` (see `paths.py`; never repo-relative, not
created by resolving it). Only `CollectionStore.init()` creates the file.

### Health status

`seo-manager collect status --tenant T [--db PATH] [--json]` prints one line per
source: last outcome (`unknown` when none), age since the last success (`never`),
`error`, `next_eligible`. A database that does not exist exits 2 with a message and
is not created; a database with no health rows says no sources have run. It reads
through `CollectionStore.list_health(tenant)` (tenant-scoped, read-only).

## Running a cycle

`seo-manager collect run --tenant T [--db PATH] [--youtube-credentials PATH]
[--video-mapping PATH] [--search-console-site URL] [--json]` runs `run_cycle` once
with `now` set to the current UTC time. It is the daily entry point; scheduling it
stays with the caller's existing scheduler, and this repo ships no schedule artifact.
The database is created only through `CollectionStore.init()`, which is safe on an
existing file.

`--youtube-credentials` is an authorized-user token JSON file; `--video-mapping` is
the daemon's `video_id -> content_id` JSON (the video ids are its sorted keys).

| Source | Needs |
|---|---|
| `youtube_analytics_search_terms` | credentials and a non-empty video mapping |
| `youtube_reach` | credentials |
| `search_console` | credentials and `--search-console-site` |

A source that cannot run is printed as `<source>  not run: <reason>` and is never
written to the store or shown as a success. The reason is
`not_configured:<input>` (`youtube_credentials`, `video_mapping`,
`video_mapping_empty`, `search_console_site`) or `client_build_failed:<ExceptionType>`
(only the type is shown, never a message or token content). Built adapters print one
line each: source, status, outcome, `added`, `error`.

Exit codes: 0 when at least one source ran and none failed; 1 when any source failed
(the others still ran); 2 when no source is configured, or on a store error. `--json`
prints the same rows as a list of objects (`status: "not_run"` with a `reason` for a
source not run).

## Not here yet

A schedule for `collect run` (the caller's scheduler owns it), and adapters beyond
YouTube Analytics search terms, YouTube reach and Search Console. The JSONL event
store (`storage/event_store.py`) is separate.
