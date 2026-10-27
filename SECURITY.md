# Security Policy

## Supported versions

Only the latest release receives security fixes. Releases are `v*` tags on this
repository (see [Releasing](README.md#releasing) in the README); `main` is
unreleased work and is not supported for production use.

## Reporting a vulnerability

Please report suspected vulnerabilities privately. Do **not** open a public issue
for a security report.

Open a private advisory with GitHub's Private Vulnerability Reporting:
<https://github.com/Back-Road-Creative/waypost/security/advisories/new>
(the **Security** tab, then **Report a vulnerability**).

Please include:

- the affected version or commit,
- a description of the issue and its impact,
- steps to reproduce it,
- any suggested fix.

## What Waypost holds that matters

Waypost publishes to platforms such as YouTube, so a deployment holds secrets:
platform OAuth tokens and OAuth client secrets (`client_secret.json`).

- **Where they live:** outside this repository. `BrandCredentials` keeps each
  brand's tokens and client secret in its own directory under the credentials
  directory, encrypted with Fernet and written with `600` permissions
  (`seo_manager/security/brand_credentials.py`). Brand names are checked so a
  name cannot point outside that directory; see
  [docs/credential-brand-paths.md](docs/credential-brand-paths.md).
- **How they stay out of the repo:** `.gitignore` excludes `client_secret.json`,
  `youtube_credentials.json`, the social publishing credential files and the
  whole `credentials/` directory, and the public-tree check in CI
  (`tools/check_public_tree.py`) inspects every pull request. Setup steps and
  the advice to keep these files ignored are in
  [docs/YOUTUBE_ANALYTICS_SETUP.md](docs/YOUTUBE_ANALYTICS_SETUP.md).

The credentials directory must stay under the operator's control. Waypost does
not defend against another process changing files or links in it at the same time.

## Please don't include

Do not put real tokens, `client_secret.json` or other live credentials in a
report, an issue or a pull request. Redact them or use made-up values. If you
find a live secret exposed, say where, and do not paste it.
