# Waypost

**Unified Social Media SEO Management System**

A Python library for generating, validating, tracking, and auto-optimizing SEO metadata (titles, descriptions, tags) across multiple platforms (YouTube, blogs, Twitter, etc.).

## Features

- 🎯 **Multi-Platform Support**: YouTube, blogs, Twitter (Instagram, TikTok and LinkedIn are planned)
- 🔍 **Smart Keyword Extraction**: deterministic, offline TF-based keyword analysis
- ✅ **SEO Validation**: Platform-specific rules and best practices
- ✅ **Upload gate**: `YouTubeUploader.upload_video` runs `PlatformValidator.validate` on the exact `videos.insert` snippet it is about to send (dry runs included), against YouTube's API limits (title 100, description 5000), and raises `ValueError` before any API call if it fails
- ✅ **Final-text review**: `analytics.final_text_review.review_final_text` puts the approved on-screen, thumbnail and Shorts text, the final title, and the location/date evidence behind each into one fingerprinted record; `verify_review` refuses an upload that differs from it, and nothing is ever auto-approved. See [docs/final-text-review.md](docs/final-text-review.md).
- 🤖 **Auto-Fix Engine**: Confidence-based automatic corrections
- 📊 **Analytics Tracking**: Performance metrics, CTR, engagement rates
- 🏢 **Multi-Site Management**: Isolated data for multiple websites/channels
- 📝 **Event Sourcing**: Complete audit trail with rollback capability

## Installation

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
# - Optimized title (50-70 chars for YouTube)
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

### Blog/Hugo
```python
from seo_manager.platforms import BlogAdapter

manager = SEOManager(
    site_id="my_blog",
    platform=BlogAdapter()
)
# Optimizations: 60-char titles, 160-char meta descriptions, 10 tags
```

### Twitter/X
```python
from seo_manager.platforms import TwitterAdapter

manager = SEOManager(
    site_id="my_twitter",
    platform=TwitterAdapter()
)
# Optimizations: 280-char limit, hashtag formatting, 3 tags max
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
├── autofix/           # Auto-fix engine with confidence scoring
└── platforms/         # Platform adapters (YouTube, Blog, Twitter, etc.)
```

## Key Concepts

### Site Isolation
Every operation is scoped to a `site_id` to prevent data cross-contamination:
```python
# These are completely isolated
youtube_manager = SEOManager(site_id="my_youtube", ...)
blog_manager = SEOManager(site_id="my_blog", ...)
```

### Event Sourcing
All metadata changes are stored as immutable events:
```python
# Latest metadata for a piece of content, rebuilt from its events
latest = manager.get_metadata(metadata.content_id)
```

### Auto-Fix Confidence
Fixes are applied based on confidence levels:
- **High (≥0.9)**: Auto-applied (e.g., title truncation to platform limit)
- **Medium (0.6-0.89)**: Flagged for review (e.g., keyword insertion)
- **Low (<0.6)**: Logged only (e.g., readability suggestions)

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

Run the CLI as a module (no console script is declared):

```bash
python -m seo_manager.cli report --ledger path/to/events.jsonl \
    --videos-yaml path/to/videos.yaml --out report.md   # LF views by publish month + title class (markdown)
python -m seo_manager.cli sync --dry-run              # prints the API calls a stats sync would make; not implemented yet
```

`report` has no default for `--ledger` or `--videos-yaml`: both name files that
belong to a particular deployment, so an unset one exits 2 with a message naming
the option.

`report` joins the latest `performance_updated` event per `data.video_id` to
`videos.yaml` (`id`, `title`, `date`); views/day = views / days between publish
and the last sync.

`python -m seo_manager.cli schedule …` books long-form slots (see
[docs/scheduling.md](docs/scheduling.md)). Its settings, each a flag or an
environment variable:

| Variable | Flag | Meaning |
|---|---|---|
| `SEO_BRAND_YAML` | `--brand-yaml` | the tenant's `brand.yaml` holding `seo.cadence`; when neither is set but `SEO_INPUTS_DIR` is, `brand.yaml` in that directory |
| `SEO_SCHEDULE_DB` | `--db` | the tenant's `schedule.sqlite3`; required, never defaulted |
| `SEO_DECISIONS_DB` | `--decisions-db` | the tenant's `cadence_decisions.sqlite3`; when set, its decided LF weekdays replace the yaml `dow` (the seed) |

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
importer's event store, the YouTube theme sync's state directory) goes under
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
| `cadence_decisions.sqlite3` | `SEO_DECISIONS_DB` / `--decisions-db` (`schedule`); `--db` (`volume_decide`) | `schedule`: the yaml `dow` seed is used; `volume_decide`: `--db` is required and argparse exits 2 without it |
| directory holding `intel_db.py` | `INTEL_SCRIPTS_DIR` | the IntelDB integration is off |
| IntelDB database file | `INTEL_DB_PATH` | saved metadata is not logged to IntelDB |
| YouTube OAuth token and video map | `--youtube-credentials`, `--video-mapping` | the daemon's sync reports itself unconfigured |
| account-to-role map | `--accounts` (Metricool importer) | exits 2 |

## Documentation

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

### Hiking blog
```python
from seo_manager import SEOManager
from seo_manager.platforms import BlogAdapter

manager = SEOManager(
    site_id="my_hiking_blog",
    platform=BlogAdapter()
)

metadata = manager.generate(
    content_type=ContentType.BLOG_POST,
    topic="Ridge Trail Review",
    body=blog_content,
    existing_tags=["hiking", "trails"]
)
```

## Requirements

- Python 3.8+
- The runtime dependencies declared in `pyproject.toml` (filelock, PyYAML, the
  Google API client and auth packages, cryptography, Pillow, structlog); all are
  imported unconditionally, so install the package rather than picking from the list

## Releasing

A release is a git tag on `main`; there is no publish workflow. The tag is
`v` plus the `version` in `pyproject.toml`, so bump that version in a pull
request first, then tag the merge commit once `ci.yml` has passed on it:

```bash
git fetch origin && git tag -a v0.1.0 origin/main -m "seo-manager 0.1.0"
git push origin v0.1.0
```

Consumers pin the tag (`seo-manager @ git+https://github.com/Back-Road-Creative/waypost@v0.1.0`)
rather than a commit SHA.

## License

The package declares the MIT license in `pyproject.toml`.

## Contributing

Report bugs and propose changes through this repository's issues and pull
requests. Include a minimal reproduction and use synthetic examples; keep
account data and credentials outside the repository.

---

**Version**: 0.1.0
**Status**: Alpha - Implementation in progress
