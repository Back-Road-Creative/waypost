# Search Console evidence

`seo_manager.collection.search_console.SearchConsoleAdapter` collects owned-site
search performance (clicks, impressions, CTR, position) into `Observation`s.
It is read-only: it calls `searchanalytics().query(...)` and never writes the
store; the collection cycle owns persistence.

Credentials: the caller supplies an authorized `searchconsole` v1 resource built
with the `webmasters.readonly` scope. Property ownership and live verification
are outside this module.

## Windows and timezone

Search Console reports days in Pacific time, so `report_zone` defaults to
`America/Los_Angeles`. `endDate = today - settle_days` (default 3) and
`startDate = endDate - window_days + 1` (default 28), both in that zone. With
the `date` dimension each observation covers that one Pacific day; without it,
the whole request window.

## Paging and caps

Requests page with `startRow` until a page returns fewer than `row_limit` rows
(default and maximum 25,000) or none; a past-the-end page returns zero rows
without error. At `max_pages` (default 4) collection stops, the outcome is
`PARTIAL` and provenance carries `page_cap_hit=true`.

## Final versus all

`data_state="final"` (default) asks for settled data. With `"all"`, rows on or
after the response's `metadata.first_incomplete_date` (or
`first_incomplete_hour`) carry `incomplete=true`; earlier rows do not. Keep
incomplete rows out of settled comparisons.

## Subjects and provenance

Subject is `<type>|<dims tag>|<non-date values joined by |>`, for example
`web|qp|<query>|<page>`; the tag (`q` query, `p` page, `c` country, `d` device,
`a` searchAppearance) keeps different aggregation levels from colliding. Keys
over 200 characters become `<type>|sha256:<32 hex>` with the raw key in
provenance (truncated to fit 500). Provenance always records
`type=...;dataState=...;dims=...`. Never add together rows from different
dimension sets: they overlap.

## Top rows only

The API returns top rows, not every row. An absent query or page is not zero
demand. A missing metric is stored as unknown (`None`), never zero.

## Combinations refused at construction

Unknown type, dimension or `data_state`; duplicate or empty lists;
`searchAppearance` with any other dimension; the `query` dimension with
`discover` or `googleNews`; `row_limit` outside 1..25,000.

## Checkpoint and outcomes

Stream `search_analytics`, cursor = last `endDate` collected. A cursor at or
past the target end makes no calls and returns `EMPTY`. `FRESH` or `EMPTY`
(success, no rows) advances the cursor. `PARTIAL` because some search types
failed transiently returns the prior checkpoint unchanged, so the next run
retries. `PARTIAL` from the page cap alone advances the cursor, since a retry
would return the same truncated rows; raise `max_pages` or narrow the
dimensions to see more. All types
failing, quota exhaustion or an auth error raise a `TransportError`.
