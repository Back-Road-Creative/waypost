# Upgrading Waypost from 0.1 to 0.2

The 0.2 release requires Python 3.12 or later and removes previously exported
APIs. Review consumers before changing their dependency pin. The distribution
name remains `seo-manager`, and the Python package remains `seo_manager`.
The package is still alpha; a release tag is not production acceptance.

## Runtime and installation

Recreate environments with Python 3.12 or later. Install the package with its
declared dependencies; the old `requirements.txt` was removed. Development and
CI constraints remain in `requirements.lock`. There are no dependency version
changes in the release metadata PR itself.

The installed `seo-manager` command and `python -m seo_manager.cli` expose the
same interface. CLI help is available without credentials. The former `sync`
command did nothing and now fails as an unknown command with exit status 2;
remove invocations that treated its success as proof of synchronization.

## Removed Python APIs

The [0.2 removed API inventory](../CHANGELOG.md#removed-api-inventory) lists
retired module paths and consumer actions. These removals have no compatibility
shim; remove or replace affected imports before upgrading.

`Platform.BLOG` and `Platform.TWITTER` are also removed. Stored metadata using
those platform values needs an explicit consumer migration before loading it
with 0.2; the release does not rewrite stored data. Content-type names and
metadata fields are separate contracts, so a retained `BLOG_POST` content type
does not restore the removed platform adapter.

## Scheduling and upload outcomes

- Scheduling options can appear before or after the action. Explicit flags
  take precedence over environment defaults.
- A scheduling claim miss still contains `"claim": null`, with an additional
  `reason` and, outside the lead window, `next_slot`. Consumers that validate
  an exact JSON shape need to allow these fields. Successful claim output is
  unchanged. Python `claim()` still returns `Claim | None`; `claim_or_reason()`
  is the opt-in API for an explained miss. See [scheduling](scheduling.md).
- Backlog rows may include a day GPX and a camera UTC offset together. Existing
  rows without either field continue to work.
- A batch upload without a nonempty video ID is a failure, even if the uploader
  returned without raising. Check per-video outcomes and totals; do not infer
  success from lack of an exception. See [batch outcomes](batch-upload-results.md).
- Brand identifiers must be directory names, not paths. Invalid names and
  existing brand-directory symlinks fail before key creation. Valid names and
  encrypted formats remain compatible. See [credential paths](credential-brand-paths.md).

## Consumer acceptance

Validate a built, noneditable package outside the source checkout, including
the installed CLI, imports and the consumer's own tests. Import compatibility
alone does not prove rendering, scheduling or live publication works. Keep the
previous consumer pin and matching environment available for rollback, and
coordinate deployment with any work already using that environment.

See the [changelog](../CHANGELOG.md) for fixes included since 0.1.0. Version
metadata and release notes can land before the tag exists; check the published
tag before using a tag-based dependency pin.
