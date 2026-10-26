# Meta earnings probe

`python -m seo_manager.analytics.meta_earnings_probe` asks the Graph API which
Facebook Page earnings metrics the stored Page token can read. Run it before
building anything that compares templates or videos on Meta earnings: a metric
the token cannot read is not a signal, and the probe names the refusal.

## Run it

```bash
python -m seo_manager.analytics.meta_earnings_probe --sops-file <dir>/meta_publishing.json.enc
# or, with the credentials JSON already decrypted:
<decrypt> | python -m seo_manager.analytics.meta_earnings_probe --creds-stdin
```

The credentials are the `meta_publishing.json` fields: `page_id`,
`page_access_token` and, optionally, `graph_version` (default `v21.0`). The
probe is read-only. It never prints the token, and it replaces the token with
`<redacted>` if the Graph API echoes it in an error message.

## What it asks

One `GET /{page-id}/insights?metric=<m>&period=day` per Page-level earnings
metric in Meta's Insights reference (read 2026-10-26):

| Metric | What Meta reports |
|---|---|
| `monetization_approximate_earnings` | Approximate earnings across monetization tools |
| `content_monetization_earnings` | Content monetization earnings (Meta marks it "in development") |
| `creator_monetization_qualified_views` | Views that qualify for monetization |
| `page_daily_video_ad_break_earnings_by_crosspost_status` | Daily in-stream ad earnings, split by crosspost status |

The edge needs the `read_insights` and `pages_read_engagement` permissions and
the ANALYZE task on the Page.

## Output

```text
META_EARNINGS verdict=available|unavailable|missing-credentials|sops-error|usage
META_EARNINGS metric=<m> status=ok points=<n> latest=<newest value as JSON>
META_EARNINGS metric=<m> status=empty points=0
META_EARNINGS metric=<m> status=error code=<graph error code> message=<graph message>
```

- `available`: at least one metric returned data points.
- `unavailable`: every metric came back empty or refused. A refusal with code
  `10` or `200` means the token lacks one of the permissions above.
- A per-metric refusal with code `200` and "only visible for Page admins with
  access to monetization insights" is a Page-role gap, not a token scope: the
  person who issued the token needs the monetization insights permission on the
  Page (in Meta Business Suite). Other earnings metrics can still read `ok` in
  the same run, as `monetization_approximate_earnings` and
  `content_monetization_earnings` did on a live Page on 2026-10-26.
- `status=empty` means the call worked but Meta has no data. That is not the
  same as earning zero.

Exit codes: `0` when the probe ran, `3` for a SOPS failure or missing
credential fields, `64` for usage errors.
