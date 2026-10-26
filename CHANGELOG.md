# Changelog

All notable changes to Waypost are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Each entry is taken
from a merged pull request title; the number in parentheses is that pull request.

Releases are git tags named `v` plus the `version` in `pyproject.toml`; see
[Releasing](README.md#releasing) in the README. Work merged to `main` and not yet
tagged is listed under Unreleased.

## [Unreleased]

### Fixed

- Analytics keep unreported metrics unknown instead of zero: summaries, the optimizer, the theme rotator and the Metricool import no longer turn a missing count into 0, and repeated lifetime snapshots of one post are no longer summed

## [0.2.1] - 2026-10-25

### Added

- Installed and module CLI version reporting without configuration (#53)

### Fixed

- OAuth setup saves credentials privately and atomically, preserves existing
  content on failure, and replaces destination symlinks without following them
  (#52). Existing credential files change only when setup is rerun.

### Changed

- Architecture guide reflects alpha status, the complete module inventory, and
  separate release and consumer-deployment acceptance (#54)

## [0.2.0] - 2026-10-25

### Added

- Installed `seo-manager` command, equivalent to `python -m seo_manager.cli` (#48).
- Scheduling claim misses report a reason and, outside the lead window, the
  next slot; `claim_or_reason` exposes the same information to Python callers
  while `claim` keeps its existing `Claim | None` contract (#48).
- Shared scheduling settings work before or after the action, preserving
  explicit settings over environment defaults (#48).
- A drive backlog row may name a whole-day GPX track (`day_gpx`) and the camera clock's
  offset from UTC (`camera_utc_offset_hours`), for footage with no GPS of its own; the two
  are required together, and `backlog-next` prints both (#46)

### Changed

- Supported Python is 3.12 and later: `requires-python`, the classifiers, the mypy
  `python_version` and the ruff `target-version` now say what CI tests (it ran 3.12
  only; the old `>=3.8` was false) (#45)
- README is titled Waypost and the final-text review description moved to its own
  document, `docs/final-text-review.md` (#27)
- README links the four docs that were missing from its Documentation index (#24)
- Duplicate event store tests are merged and a guard checks test file basenames (#26)
- `requirements.txt` and the dead pytest config are dropped (#25)

### Fixed

- Credential brand identifiers reject path escapes and existing symlinks before key access (#49)
- Batch uploads without a video ID count as failures and honor continue-or-stop policy (#50)
- State files are written atomically; quota recovery tolerates a corrupt file (#40)
- Report commands return exit 2 for bad inputs and explain empty joins (#42)
- Quota manager keeps other managers' usage and reports both warning tiers (#31)
- Title casing keeps acronyms and apostrophes (#30)
- Metricool import usage shows the required flags without brackets (#29)
- Analytics package exports are imported lazily, so `python -m` runs stay quiet (#28)

### Removed

- The no-op `sync` placeholder; it now exits 2 as an unknown command (#48).
- Unused autofix package (#32)
- Unused YouTube theme sync (#33)
- Unused thumbnail design system module (#34)
- Unused production modules: error recovery and the package init (#35), structured
  logger and rate limiter (#36), health check and issue tracker (#37), podcast
  quality tracker (#38)
- Documentation mentions of the removed autofix, theme sync and thumbnail design
  system modules (#39)
- Unused blog metadata generator and its tests (#43)
- Unused blog and Twitter platform adapters and their `Platform` enum members (#44)

### Removed API inventory

These modules have no compatibility shim in 0.2:

| Removed path under `seo_manager` | Consumer action |
| --- | --- |
| `autofix` | Remove imports and calls to the retired autofix engine. |
| `analytics.youtube_theme_sync` | Remove calls to the retired theme synchronization module. |
| `youtube.thumbnail_design_system` | Remove imports of this module; the separate thumbnail generator remains. |
| `production` | Remove dependencies on error recovery, structured logging, rate limiting, health checks, issue tracking, and podcast quality tracking from this retired package. |
| `generation.blog_generator` | Remove use of the retired blog generator and its package export. |
| `platforms.blog`, `platforms.twitter` | Remove use of the retired adapters and their package exports. |

## [0.1.0] - 2026-10-21

### Added

- MIT license matching the package metadata (#1)
- `select_title`, a deterministic title decision record (#3), with single-place
  Short facts and a template (#4)
- Final-text review of approved on-screen text against the final title (#7)
- Final-text record v2, which refuses dropped route places, unproven regions and
  stale reviews (#8)
- `upload_video` requires a final-text review checked on the sent title (#9)
- Review of description, chapters and tags as typed surfaces (#11)
- Slot-typed param templates and the drive layout review (#14)
- Typed drive-text generator with a synthetic golden (#15)
- Drive text review covering every GMS shape, typed and ordered (#16), and a
  generator that emits every GMS drive shape, blank lines too (#17)
- Pinned bounded region proofs accepted by the review (#18)
- Verified region-only long-form title decisions (#19)
- Viewer-facing locality naming rule (#21)
- Private input files resolved from `SEO_INPUTS_DIR` (#22)

### Changed

- Retired the learned-config applier (#5, breaking)
- README describes tag-based releases (#23)

### Fixed

- Report window ends today, not 2100-01-01 (#2)
- Final upload payload is validated before it is sent (#6)
- A visible name that no claim backs is refused (#10)
- Upload binds the sent description and tags to the review (#12)
- Upload refuses a body with no description; tag order is tested (#13)
- Bounded-proof gap lengths compare at millisecond precision (#20)

[Unreleased]: https://github.com/Back-Road-Creative/waypost/compare/v0.2.1...main
[0.2.1]: https://github.com/Back-Road-Creative/waypost/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Back-Road-Creative/waypost/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Back-Road-Creative/waypost/releases/tag/v0.1.0
