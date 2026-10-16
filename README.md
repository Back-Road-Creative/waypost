# SEO Manager

**Unified Social Media SEO Management System**

A Python library for generating, validating, tracking, and auto-optimizing SEO metadata (titles, descriptions, tags) across multiple platforms (YouTube, blogs, Twitter, etc.).

## Features

- 🎯 **Multi-Platform Support**: YouTube, blogs, Twitter (Instagram, TikTok and LinkedIn are planned)
- 🔍 **Smart Keyword Extraction**: deterministic, offline TF-based keyword analysis
- ✅ **SEO Validation**: Platform-specific rules and best practices
- ✅ **Upload gate**: `YouTubeUploader.upload_video` runs `PlatformValidator.validate` on the exact `videos.insert` snippet it is about to send (dry runs included), against YouTube's API limits (title 100, description 5000), and raises `ValueError` before any API call if it fails
- ✅ **Final-text review**: `analytics.final_text_review.review_final_text` puts the approved on-screen, thumbnail and Shorts text, the final `select_title` title, and the location/date evidence behind each into one fingerprinted record; it refuses on missing or ambiguous evidence, reports `disagree` when the title changed after approval, and never approves (`human_gate_required` is always true). `verify_review` refuses an upload whose title or text differs from what was reviewed Record v2 carries the route: each place the retained footage holds for at least `FinalTextPolicy.min_place_seconds` must be named on every whole-video surface and the long-form title (a Short's text and title name its own segment), or a region may stand in only when its `region_claims` item contains every such place with full GPS coverage, no gaps and no samples outside (`place_dropped`, `route_reduced`, `region_partial`, `region_coverage`, `region_gaps`, `region_outside`, each also reported as `region_not_contained`). Every name on a surface needs a claim: once the cited names are taken out, any word not in `FinalTextPolicy.filler_words` is refused as `name_unbacked:<surface>:<word>`, and a cited location must be on the retained route (`place_not_on_route`) or, on a Short, in its own segment (`place_not_in_segment`); roads are cited as `road` evidence. Review v4 (record v2) types every visible surface (`analytics.final_text_surfaces`), so a description, its chapters and its tags are reviewed without any free prose: a `place_line` is checked as above, a `template` passes only when its `template_id` is registered in `TEMPLATES` and the SHA-256 of its exact bytes matches (`template_unknown`, `template_changed`), a `chapter` is a segment surface over its own `segment_places` (`chapter_without_window`), and a `tag` is backed by evidence or comes from a registered `tag.*` template; any other kind is refused (`surface_kind`). Review v5 adds a fifth kind, `param_template` (`analytics.final_text_params`, `analytics.drive_templates`): a registered skeleton with typed slots (`number`, `unit`, `duration`, `enum`, `place`; no free-string slot can be registered). The hash covers the skeleton with its slot markers and the slot specs, not the filled text, so one changed byte is refused (`template_unknown`, `template_changed`); each slot value must be legal for its type (`param_slot`), a place slot cites an evidence id that must back like a `place_line` (`param_place`, `name_unbacked`, `place_not_on_route`), and the text must equal the skeleton filled (`param_text`). A record's `description_layout` (`gms-drive-1`) makes the description the ordered registered blocks only (`layout_unknown`, `layout_free_line`, `layout_order`, `layout_missing`) and keeps tags in the registered order (`tag_order`); non-place tags come only from the registered `tag.*` set. `REVIEW_VERSION` is `final-text-review-5` and the text policy is `final-text-policy-3`, so a record written under another template set is refused. `build_visible` emits these entries and takes template text only from the registry. A hash binds a template's bytes, not their meaning, so templates change only in a reviewed PR. `verify_review` also refuses a missing or empty review, an edited one, and a changed source, plan, title policy or text policy (`FinalTextReviewRefused.code` / `.reasons`). `YouTubeUploader.upload_video` requires `final_text_review` and `final_text_context` and checks them on the request body before any API call, dry runs included. The check also binds the description and tags actually sent: `verify_final_text` always passes `body["snippet"]["description"]` and `tags` to `verify_review`, which refuses them unless they equal `compose_description` / `compose_tags` of the reviewed surfaces (`stale:description`, `stale:tags`). Description lines (`description` / `description.*` place lines and templates) and chapters, joined by newlines, make the description; `tag` surfaces make the tags; on-screen surfaces (`ost`, `thumbnail`, `shorts_ost`) are not part of either, so callers must build the description and tags they send with those two functions.
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
| `SEO_BRAND_YAML` | `--brand-yaml` | the tenant's `brand.yaml` holding `seo.cadence` |
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

### Locations with no default ("not configured")

Locations that belong to one deployment have no default; unset means "not
configured" and the code says which variable or option to set:

| Location | Set with | When unset |
|---|---|---|
| event ledger for `report` | `--ledger` | exits 2 naming `--ledger` |
| `videos.yaml` for `report` | `--videos-yaml` | exits 2 naming `--videos-yaml` |
| tenant `brand.yaml` | `SEO_BRAND_YAML` / `--brand-yaml` | `schedule` fails naming it; `volume_decide` then decides only the volume knobs |
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
- **Continuous optimization daemon**: [docs/continuous-optimization-daemon.md](docs/continuous-optimization-daemon.md)
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

## License

The package declares the MIT license in `pyproject.toml`.

## Contributing

Report bugs and propose changes through this repository's issues and pull
requests. Include a minimal reproduction and use synthetic examples; keep
account data and credentials outside the repository.

---

**Version**: 0.1.0
**Status**: Alpha - Implementation in progress
