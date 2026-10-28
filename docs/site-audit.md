# Site build audit

`python -m seo_manager.site_audit` audits a site's **built output directory**
(a Hugo or Astro `public/`-style folder) and reports technical SEO defects as
deterministic JSON or text. It is offline: it reads files and nothing else.

```
python -m seo_manager.site_audit --build-dir DIR --base-url https://example.com/ \
    [--exclude PREFIX ...] [--json] [--fail-on error|warning|never]
```

## What it checks

Pages are every `*.html` under the build directory. `a/b/index.html` has the
URL `<base>a/b/`; `a/x.html` has `<base>a/x.html`.

| Check | Severity | Meaning |
| --- | --- | --- |
| `broken_internal_link` | error | `<a href>`, stylesheet `<link>`, `<img src>` or `<script src>` on this site that resolves to no file (tries the path, `path/index.html`, `path.html`; fragments and queries ignored; paths percent-decoded) |
| `noindex` | warning | `<meta name="robots"\|"googlebot">` containing `noindex` |
| `noindex_in_sitemap` | error | a `noindex` page is listed in the sitemap |
| `canonical_missing` | warning | no `rel=canonical` |
| `canonical_multiple` | error | more than one `rel=canonical` |
| `canonical_relative` | error | canonical is not an absolute http(s) URL |
| `canonical_offsite` | warning | canonical points at another host |
| `canonical_mismatch` | info | canonical points at a different on-site page (normal for duplicates) |
| `canonical_target_missing` | error | on-site canonical target has no file |
| `sitemap_missing` | error | no `sitemap.xml` at the build root |
| `sitemap_invalid` | error | not well-formed XML (the exception type only is reported) |
| `sitemap_url_missing_file` | error | a sitemap URL on this site has no file |
| `sitemap_url_offsite` | warning | a sitemap URL is on another host |
| `not_in_sitemap` | info | an indexable page has no sitemap entry (`404.html` and `404/index.html` are exempt) |
| `schema_invalid_json` | error | a `application/ld+json` block is not valid JSON |
| `schema_missing_type` | warning | an object (or each item of a list or `@graph`) lacks `@type` |
| `schema_missing_context` | warning | a top-level object lacks `@context` |
| `robots_txt_missing` | warning | no `robots.txt` at the build root |
| `robots_sitemap_missing` | error | a `Sitemap:` line points on-site at a file that does not exist |

A `<sitemapindex>` is followed for on-site children that exist as files, at
most 50 of them. `--exclude PREFIX` (repeatable, relative to the build
directory) skips pages under that prefix on purpose; they are counted as
`excluded`, still count as link targets, and produce no findings.

## What it does not check

- No network: external links are only counted (`external_links`), never fetched.
- No deployment verification: it audits files, not what is served.
- No schema vocabulary validation: only valid JSON, `@type` and `@context` presence.
- No rendered or JavaScript DOM: only the static HTML as built.

Later S6 parts add bounded live checks, store ingestion and the S4b technical rules.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | no finding at or above `--fail-on` (default `error`; `warning` also fails on warnings; `never` always passes) |
| 1 | at least one finding at or above `--fail-on` |
| 2 | usage error: missing build directory or a `--base-url` that is not http(s) with a host |

## JSON shape

Same input gives byte-identical output. Findings are sorted by check, page, target.

```json
{
  "base_url": "https://example.com/",
  "pages": 12,
  "excluded": 0,
  "external_links": 7,
  "counts_by_check": {"robots_txt_missing": 1},
  "findings": [
    {"check": "robots_txt_missing", "severity": "warning", "page": "robots.txt",
     "target": null, "detail": "no robots.txt at the build root"}
  ]
}
```

Text output is one line per finding, `severity check page [target] detail`, then a counts line.
