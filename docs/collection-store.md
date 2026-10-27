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

## Not here yet

Source adapters (a part 2): mapping YouTube Analytics and other source
responses, and transport errors (`QuotaExhaustedError`, `AuthError`, ...) onto
`SourceHealth`. The JSONL event store (`storage/event_store.py`) is separate.
