# Waypost

**Unified Social Media SEO Management System**

A Python library for generating, validating, tracking, and auto-optimizing SEO metadata (titles, descriptions, tags) across multiple platforms (YouTube today; other platforms are planned).

## Features

- 🎯 **Multi-Platform Support**: YouTube (Instagram, TikTok and LinkedIn are planned)
- 🔍 **Smart Keyword Extraction**: deterministic, offline TF-based keyword analysis
- ✅ **SEO Validation**: Platform-specific rules and best practices
- ✅ **Upload gate**: `YouTubeUploader.upload_video` runs `PlatformValidator.validate` on the exact `videos.insert` snippet it is about to send (dry runs included), against YouTube's API limits (title 100, description 5000), and raises `ValueError` before any API call if it fails
- ✅ **Final-text review**: `analytics.final_text_review.review_final_text` puts the approved on-screen, thumbnail and Shorts text, the final title, and the location/date evidence behind each into one fingerprinted record; `verify_review` refuses an upload that differs from it, and nothing is ever auto-approved. See [docs/final-text-review.md](docs/final-text-review.md).
- 📊 **Analytics Tracking**: Performance metrics, CTR, engagement rates
- 🏢 **Multi-Site Management**: Isolated data for multiple websites/channels
- 📝 **Event Sourcing**: Complete audit trail (append-only; no general rollback, only the cadence rollback in [docs/cadence-decisions.md](docs/cadence-decisions.md))

## Installation

Python 3.12 or later is required. When upgrading from 0.1.0, review the
[0.2 migration guide](docs/migrating-0.2.md) for removed APIs and changed CLI
outcomes. The distribution remains `seo-manager`; Python imports remain
`seo_manager`.

### Development Mode (Editable Install)

```bash
# From the root of this repository

# Install in editable mode
pip install -e .

# Install with development dependencies
pip install -e ".[dev]"
```

### Pinned installs (CI)

`requirements.lock` pins every transitive dependency the package resolved to.
CI installs it as a `pip --constraint` (editable `-e` lines stripped first)
alongside `.[dev]`, so a new PyPI release cannot silently change what CI runs
against. Bump pins deliberately by regenerating the lock in a fresh venv, not by
hand-editing versions.

## Quick Start

```python
from pathlib import Path

from seo_manager import SEOManager
from seo_manager.platforms import YouTubeAdapter
from seo_manager.core.metadata import ContentType

# Initialize manager for YouTube
manager = SEOManager(
    site_id="my_youtube_channel",
    platform=YouTubeAdapter(),
    storage_path=Path("./data/seo")
)

# Generate SEO metadata
metadata = manager.generate(
    content_type=ContentType.VIDEO,
    topic="Understanding AI Safety",
    transcript=[
        {"speaker": "Host", "text": "Today we're discussing AI safety..."},
        {"speaker": "Guest", "text": "It's a critical topic..."}
    ],
    episode_number=42
)

# Metadata includes:
# - Optimized title (at most 70 chars: a mobile-display style cap; the YouTube API limit is 100)
# - SEO-friendly description with timestamps
# - Relevant tags extracted from content
# - Quality scores and readability metrics
# - Auto-applied fixes (if needed)

print(f"Title: {metadata.title}")
print(f"Tags: {', '.join(metadata.tags)}")
print(f"Quality Score: {metadata.quality_score}")

# Save metadata (tracks analytics automatically)
event_id = manager.save(metadata)

# Later: Update with performance data
manager.analytics.track_performance(
    content_id=metadata.content_id,
    views=1250,
    clicks=87,
    impressions=5000
)

# Get analytics summary
analytics = manager.analytics.get_summary(
    start_date="2026-01-01",
    end_date="2026-01-31"
)
print(f"Average CTR: {analytics['performance_metrics']['avg_ctr']}")
```

## Platform Adapters

### YouTube
```python
from seo_manager.platforms import YouTubeAdapter

manager = SEOManager(
    site_id="my_channel",
    platform=YouTubeAdapter()
)
# Optimizations: 70-char titles, 5000-char descriptions, 15 tags
```

## Configuration

`SEOManager` takes `site_id`, `platform`, `storage_path` and `config` directly;
the package does not read a sites file. For several sites, keep your own
configuration file (for example `config/sites.yaml`, see
`config/sites.yaml.example` for a layout) and have your code load it and
construct one `SEOManager` per site.

## Architecture

```
seo_manager/
├── core/              # Core data structures (SEOMetadata, events)
├── generation/        # Metadata generators (base + platform-specific)
├── storage/           # Event store, metadata persistence
├── analytics/         # Performance tracking and reporting
├── validation/        # SEO validation rules
└── platforms/         # Platform adapters (YouTube)
```

## Key Concepts

### Site Isolation
Every operation is scoped to a `site_id` to prevent data cross-contamination:
```python
# These are completely isolated
youtube_manager = SEOManager(site_id="my_youtube", ...)
other_manager = SEOManager(site_id="my_other_channel", ...)
```

### Event Sourcing
All metadata changes are stored as immutable events:
```python
# Latest metadata for a piece of content, rebuilt from its events
latest = manager.get_metadata(metadata.content_id)
```

## Analytics

Track performance over time:
```python
# Get summary report
summary = manager.analytics.get_summary(
    start_date="2026-01-01",
    end_date="2026-01-31"
)

# Returns:
{
    "content_metrics": {
        "total_content_generated": 42,
        "avg_quality_score": 0.87,
        "avg_title_length": 62
    },
    "performance_metrics": {
        "total_views": 125000,
        "total_clicks": 8500,
        "avg_ctr": 0.068
    },
    "top_performers": [...]
}
```

## CLI

Use `seo-manager --version` to identify the installed package without loading
configuration or opening a database. See [version reporting](docs/cli-version.md).

Installing Waypost provides `seo-manager`. The equivalent
`python -m seo_manager.cli` invocation remains supported:

```bash
seo-manager report --ledger path/to/events.jsonl \
    --videos-yaml path/to/videos.yaml --out report.md   # LF views by publish month + title class (markdown)
```

The former `sync` placeholder has been removed: it performed no synchronization.
Calling it now exits 2 instead of reporting a successful dry run.

`report` has no default for `--ledger` or `--videos-yaml`: both name files that
belong to a particular deployment, so an unset one exits 2 with a message naming
the option.

`report` joins the latest `performance_updated` event per `data.video_id` to
`videos.yaml` (`id`, `title`, `date`); views/day = views / days between publish
and the last sync.

A missing or unreadable file, a ledger line that is not valid JSON, a
`videos.yaml` without a `videos` list or with an entry lacking `id`, `title` or
`date` makes `report` print one line naming the cause and exit 2. A join that
matches nothing still exits 0, but says why (no videos, no events, no video id
in common, or no views measurement) in the report and on stderr.

`python -m seo_manager.cli schedule …` books long-form slots (see
[docs/scheduling.md](docs/scheduling.md)). Its settings, each a flag or an
environment variable:

Scheduling settings can appear before or after the action, for example
`seo-manager schedule report --brand-yaml brand.yaml --db schedule.sqlite3`.
An explicit value after the action wins over the same setting before it;
explicit flags override environment defaults. A claim with no work keeps
`"claim": null` and adds a `reason`; see the [claim protocol](docs/scheduling.md#claim-protocol).

| Variable | Flag | Meaning |
|---|---|---|
| `SEO_BRAND_YAML` | `--brand-yaml` | the tenant's `brand.yaml` holding `seo.cadence`; when neither is set but `SEO_INPUTS_DIR` is, `brand.yaml` in that directory |
| `SEO_SCHEDULE_DB` | `--db` | the tenant's `schedule.sqlite3`; required, never defaulted |
| `SEO_DECISIONS_DB` | `--decisions-db` | the tenant's `cadence_decisions.sqlite3`; when set, its decided LF weekdays replace the yaml `dow` (the seed) |

### Volume and cadence decisions

`python -m seo_manager.analytics.volume_decide --evidence evidence.json`
reads its decisions database path from `SEO_DECISIONS_DB`. An explicit
`--db cadence_decisions.sqlite3` overrides that environment variable. If neither
is provided (including an empty environment value), it exits 2 with an argument
error before reading evidence or writing state. `--help` describes these options.

### Metricool PDF importer

```bash
python -m seo_manager.analytics.metricool_pdf_import report.pdf \
    --site-id my_site --accounts accounts.yaml [--store-path DIR] [--dry-run]
```

`--site-id` and `--accounts` are required (the option parser exits 2 naming the
missing one). `accounts.yaml` (YAML or JSON) maps each account display name in the
report to one role: `facebook_youtube`, `instagram_threads`, `twitter`, `tiktok` or
`pinterest`. `--store-path` is the event store directory; without it the importer
uses `seo_events` under the data home (below). `--dry-run` parses without storing
and `--dump-text` prints the extracted text and exits.

### Optimization daemon

```bash
python -m seo_manager.analytics.continuous_optimization_daemon \
    [--apply] [--site-id ID] [--state-dir DIR] \
    [--youtube-credentials TOKEN.json --video-mapping MAP.json]
```

Runs one cycle and is a dry run unless `--apply` is given, which persists what the
cycle learned under `--state-dir` and nothing else. `--site-id` names the site
the SEO events are stored under (default `default`); `--state-dir` defaults to
`./data/optimization`. `--youtube-credentials` and
`--video-mapping` have no default and no environment variable: without both, the
cycle collects nothing and reports which input was missing. See
[docs/continuous-optimization-daemon.md](docs/continuous-optimization-daemon.md).

### Where data lives

What seo-manager owns when the caller names no location (the Metricool PDF
importer's event store) goes under
`$SEO_MANAGER_DATA_HOME`, else `$XDG_DATA_HOME/seo-manager`, else
`~/.local/share/seo-manager`. The per-site event store, quota counters and the
optimisation daemon's state take an explicit path and otherwise use a `data/...`
directory relative to the working directory.

### Private inputs directory

A deployment's private input files (the tenant's `brand.yaml`, site configs,
link maps) stay outside this package. `$SEO_INPUTS_DIR` names the directory
holding them; unset, it is `inputs` under the data home. A directory that does
not exist is an error naming the variable and the path, never an empty
default. `seo_manager.inputs` has the helpers: `inputs_dir()` (resolve only),
`require_inputs_dir()` and `input_path(*parts)` (checked), and
`default_brand_yaml()`, which gives `--brand-yaml` the order flag, then
`SEO_BRAND_YAML`, then `brand.yaml` in `SEO_INPUTS_DIR`.

### Locations with no default ("not configured")

Locations that belong to one deployment have no default; unset means "not
configured" and the code says which variable or option to set:

| Location | Set with | When unset |
|---|---|---|
| event ledger for `report` | `--ledger` | exits 2 naming `--ledger` |
| `videos.yaml` for `report` | `--videos-yaml` | exits 2 naming `--videos-yaml` |
| tenant `brand.yaml` | `SEO_BRAND_YAML` / `--brand-yaml` (or `brand.yaml` in `SEO_INPUTS_DIR`) | `schedule` fails naming it; `volume_decide` then decides only the volume knobs |
| `schedule.sqlite3` | `SEO_SCHEDULE_DB` / `--db` (`--schedule-db` for `volume_decide`) | `schedule` fails naming it; `volume_decide` counts ready depth as 0 and says why |
| `cadence_decisions.sqlite3` | `SEO_DECISIONS_DB` / `--decisions-db` (`schedule`); `SEO_DECISIONS_DB` / `--db` (`volume_decide`) | `schedule`: the yaml `dow` seed is used; `volume_decide`: exits 2 when no flag is provided and the environment value is absent or empty |
| directory holding `intel_db.py` | `INTEL_SCRIPTS_DIR` | the IntelDB integration is off |
| IntelDB database file | `INTEL_DB_PATH` | saved metadata is not logged to IntelDB |
| YouTube OAuth token and video map | `--youtube-credentials`, `--video-mapping` | the daemon's sync reports itself unconfigured |
| account-to-role map | `--accounts` (Metricool importer) | exits 2 |

## Documentation

- **SEO opportunity and recovery roadmap**: [docs/seo-improvement-plan.md](docs/seo-improvement-plan.md) (proposal; no runtime changes)

- **Built-site audit** (offline links, noindex, canonical, sitemap, schema): [docs/site-audit.md](docs/site-audit.md)
- **Upgrading from 0.1.0**: [docs/migrating-0.2.md](docs/migrating-0.2.md)
- **Credential directory names**: [docs/credential-brand-paths.md](docs/credential-brand-paths.md)
- **Security policy and reporting**: [SECURITY.md](SECURITY.md)
- **Batch upload results**: [docs/batch-upload-results.md](docs/batch-upload-results.md)

- **Architecture**: [ARCHITECTURE.md](ARCHITECTURE.md)
- **Scheduling and slot booking**: [docs/scheduling.md](docs/scheduling.md)
- **Cadence and volume decisions**: [docs/cadence-decisions.md](docs/cadence-decisions.md)
- **Title selection**: [docs/title-selection.md](docs/title-selection.md)
- **Viewer-facing locality names**: [docs/viewer-locality-names.md](docs/viewer-locality-names.md)
- **Continuous optimization daemon**: [docs/continuous-optimization-daemon.md](docs/continuous-optimization-daemon.md)
- **Anomaly statistics kernel**: [docs/anomaly-statistics-kernel.md](docs/anomaly-statistics-kernel.md)
- **Final-text review**: [docs/final-text-review.md](docs/final-text-review.md)
- **Bounded retained-route region review**: [docs/bounded-region-review.md](docs/bounded-region-review.md)
- **Titles for a verified single region**: [docs/region-only-title-decisions.md](docs/region-only-title-decisions.md)
- **Thumbnail Test & Compare companion**: [docs/thumbnail-test-and-compare.md](docs/thumbnail-test-and-compare.md)
- **YouTube Analytics setup**: [docs/YOUTUBE_ANALYTICS_SETUP.md](docs/YOUTUBE_ANALYTICS_SETUP.md)
- **Meta earnings probe**: [docs/meta-earnings-probe.md](docs/meta-earnings-probe.md)

## Testing

```bash
# Run tests
pytest

# With coverage
pytest --cov=seo_manager --cov-report=html

# Run specific test file
pytest tests/unit/test_metadata.py
```

### Linting

`pyproject.toml` sets the enforced `[tool.ruff]` select (E, F, W, I, UP, B,
SIM, RUF). `ruff.toml` extends that with rule families this service already
passes with zero findings, so those families can never regress silently. Add
a new family to `ruff.toml` only after verifying it is independently clean
(`ruff check . --select <FAMILY>`); if it isn't, fix the findings first or
leave it out — never widen `ignore` to force a family in.

## Integration Examples

### YouTube podcast channel
```python
from seo_manager import SEOManager
from seo_manager.platforms import YouTubeAdapter

manager = SEOManager(
    site_id="my_podcast_youtube",
    platform=YouTubeAdapter()
)

# Replace existing YouTubeMetadataGenerator
metadata = manager.generate(
    content_type=ContentType.VIDEO,
    topic="AI Ethics Debate",
    transcript=episode_transcript,
    episode_number=42,
    citations=references
)
```

## Requirements

- Python 3.12+ on Linux or Windows (CI runs the test suite on both: the `test`
  and `test-windows` jobs in `.github/workflows/ci.yml`)
- The runtime dependencies declared in `pyproject.toml` (filelock, PyYAML, the
  Google API client and auth packages, cryptography, Pillow, structlog); all are
  imported unconditionally, so install the package rather than picking from the list

## Releasing

A release is a git tag on `main`; there is no publish workflow. The tag is
`v` plus the `version` in `pyproject.toml`, so bump that version in a pull
request first, then tag the merge commit once `ci.yml` has passed on it:

```bash
git fetch origin && git tag -a v0.4.0 origin/main -m "seo-manager 0.4.0"
git push origin v0.4.0
```

Once the tag exists, consumers can pin the tag (`seo-manager @ git+https://github.com/Back-Road-Creative/waypost@v0.4.0`)
or use an exact reviewed commit SHA. Updating a consumer pin and deploying it
are separate changes; a Waypost tag does not update running consumers.

Every release also updates [CHANGELOG.md](CHANGELOG.md). In the same pull request
that bumps the version, rename the Unreleased heading to the new version and the
date of the tag, add a fresh empty Unreleased section above it, and update the
link references at the bottom of the file. Entries come from merged pull request
titles (`gh pr list --state merged`), one line each with its number.

## License

The package declares the MIT license in `pyproject.toml`.

## Contributing

Report bugs and propose changes through this repository's issues and pull
requests. Include a minimal reproduction and use synthetic examples; keep
account data and credentials outside the repository.

---

**Version**: 0.4.0
**Status**: Alpha - Implementation in progress
