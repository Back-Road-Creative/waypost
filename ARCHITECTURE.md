# SEO Manager - System Architecture

**Version:** 1.0.0
**Status:** Production Ready

---

## System Overview

**SEO Manager** is a complete social media SEO management library providing:
- Multi-platform SEO metadata generation (YouTube, Blog, Twitter)
- NLP-powered keyword extraction and optimization
- Performance analytics tracking with YouTube API integration
- Thumbnail generation with three A/B style variations
- Event sourcing with complete audit trails
- Site isolation for multi-site management

---

## Architecture Layers

```
┌─────────────────────────────────────────────────────────────┐
│                    SEO MANAGER LIBRARY                      │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              PRESENTATION LAYER                      │  │
│  │  - SEOManager (main API)                            │  │
│  │  - Platform adapters (YouTube, Blog, Twitter)       │  │
│  │  - Thumbnail generators (validated A/B testing)     │  │
│  └────────────────┬─────────────────────────────────────┘  │
│                   │                                         │
│  ┌────────────────┴─────────────────────────────────────┐  │
│  │           BUSINESS LOGIC LAYER                       │  │
│  │  - SEO Generation (NLP, keyword extraction)         │  │
│  │  - Validation (platform rules)                      │  │
│  │  - Analytics (tracking, aggregation, reporting)     │  │
│  │  - YouTube Features (chapters, citations, hooks)    │  │
│  └────────────────┬─────────────────────────────────────┘  │
│                   │                                         │
│  ┌────────────────┴─────────────────────────────────────┐  │
│  │              DATA LAYER                              │  │
│  │  - Event Store (JSONL with CRC32 checksums)         │  │
│  │  - Site isolation (filesystem partitioning)         │  │
│  │  - Indexes (by site, type, date, content)           │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                             │
└─────────────────────────────────────────────────────────────┘

External Integrations:
- YouTube Data API v3 (analytics, statistics)
- YouTube Analytics API v2 (performance metrics)
```

---

## Core Components

### 1. Core Module (`seo_manager/core/`)

**SEOMetadata** - Immutable dataclass
- 6 platforms (YouTube, Blog, Twitter, Instagram, TikTok, LinkedIn)
- 6 content types (Video, Short, Blog Post, Social Post, Podcast, Trail Guide)
- Quality metrics (quality_score, keyword_density, readability_score)
- Platform-specific metadata dicts
- Serialization (to_dict/from_dict)

**SEOEvent** - Event sourcing foundation
- 9 event types (metadata_generated, validation_failed, auto_fix_applied, etc.)
- CRC32 checksum validation
- JSONL serialization
- Corruption detection

**SEOManager** - Main orchestrator
- Platform-specific generator delegation
- Metadata generation, validation, storage
- Analytics integration
- Site-scoped operations

---

### 2. Generation Module (`seo_manager/generation/`)

**BaseSEOGenerator** - Core NLP engine
- Offline keyword extraction with a vendored stopword list (`stopwords.py`)
- TF-based ranking
- Title/description/tag optimization
- Optional learned title-length range (`_optimize_title(target_range=...)`) —
  see *Learned title-length constraint* below
- Quality scoring (0.0-1.0)
- Readability analysis (Flesch Reading Ease)
- Keyword density calculation

**YouTubeMetadataGenerator** - YouTube specialization
- Chapter marker extraction from transcript timestamps
- Automatic chapter detection (transition keywords, time gaps)
- Citation formatting with numbered references [1], [2], [3]
- Hook generation (first 125 chars for above-fold CTR)
- Episode branding (show name + episode number integration)
- Multi-level tag strategy (base + topic + transcript + brands)
- Tech keyword corpus (40+ AI/tech terms)

---

### 3. Storage Module (`seo_manager/storage/`)

**SEOEventStore** - Append-only persistence
- JSONL file format (one event per line)
- FileLock for concurrent safety
- Site-based partitioning (filesystem isolation)
- Query operations (by_site, by_type, by_date_range, by_content_id)
- Index management (by_site, by_type, by_date, by_content)
- Corruption detection and recovery

**Storage Structure:**
```
{storage_path}/
  {site_id}/
    events.jsonl     # Append-only event log
    index.json       # Query indexes
    .lock            # File lock
```

---

### 4. Platforms Module (`seo_manager/platforms/`)

**BasePlatformAdapter** - Abstract interface
- Platform constraints (title/description/tag limits)
- Format conversion (comma-separated, hashtags, slugs)
- Validation methods

**YouTubeAdapter**
- Title: 70 chars (mobile optimized)
- Description: 5,000 chars
- Tags: 15 max, comma-separated, lowercase

**BlogAdapter**
- Title: 60 chars (Google SERP optimized)
- Description: 160 chars (meta description)
- Tags: 10 max, slugified for URLs

**TwitterAdapter**
- Description: 280 chars
- Tags: 3 max, hashtag formatted
- Character limit enforcement

---

### 5. Analytics Module (`seo_manager/analytics/`)

**AnalyticsTracker** - Performance metrics
- Generation event tracking (quality scores, metadata metrics)
- Performance event tracking (views, clicks, impressions, CTR)
- Summary generation with aggregation
- Top performer analysis
- Date range filtering
- Content-specific queries

**YouTubeAnalyticsSync** - API integration
- OAuth 2.0 authentication flow
- Video statistics fetching (YouTube Data API v3)
- Batch synchronization
- Engagement rate calculation
- Error handling for API failures
- Automatic analytics tracking to SEO Manager

---

#### Learned title-length constraint

`SEOOptimizer.get_optimal_title_length()` derives a `(min, max)` range from the
top quarter of performers by CTR-weighted, age-decayed score.
`SEOOptimizer.optimize_metadata` passes it to `SEOManager.generate` as
`title_length_range`, which hands it to `BaseSEOGenerator._optimize_title` as
`target_range`. Every generator inherits that one method, so the constraint is
honoured uniformly — no generator silently ignores it.

The range is **advisory**; the platform's `get_title_limit()` is not. No range
(`None`) reproduces the pre-constraint output byte for byte. A range wider than
the platform cap is clamped to the cap. A too-long title is cut at a word
boundary with `...` appended. A too-short title is left alone — padding it
would fabricate words the caller never supplied. When no word boundary fits the
budget, or the cut would land below the range minimum, the whole title is kept
and stays out of range: a slightly long title reads, a chopped one does not.

A caller that runs `analyze_patterns` and then `optimize_metadata` gets the
learned ranges applied to the titles it generates.

---

### 6. Validation Module (`seo_manager/validation/`)

**PlatformValidator** - SEO rule enforcement
- Platform-specific constraints (title/description/tag limits)
- Error vs warning distinction
- ValidationResult with metadata updates

---

### 7. YouTube Module (`seo_manager/youtube/`) 🆕

**YouTubeThumbnailGenerator** - Thumbnail creation
- 1280x720 YouTube standard
- Multiple style variants (classic, vibrant, dark)
- Episode branding with badges
- Logo placement with shadows
- Mobile readability validation
- Gradient backgrounds

---

## Data Flow

### Metadata Generation Flow

```
1. Input (topic, body, transcript)
   ↓
2. Keyword Extraction (offline, deterministic)
   ↓
3. Platform-Specific Generation
   ↓
4. Optimization (title/description/tag limits, + learned title-length range
   when the caller supplies one)
   ↓
5. Quality Scoring
   ↓
6. Validation
   ↓
7. Event Store Persistence
   ↓
8. Analytics Tracking
```

### YouTube Analytics Sync Flow

```
1. OAuth Authentication
   ↓
2. Fetch Video Statistics (YouTube Data API)
   ↓
3. Calculate Engagement Rate
   ↓
4. Track Performance in SEO Manager
   ↓
5. Update Analytics Summary
   ↓
6. Generate Reports
```

### Thumbnail Generation Flow (A/B Testing)

```
1. Load Background Photo
   ↓
2. Generate Variation: minimal (subtle text at bottom)
   ↓
3. Generate Variation: bold (high-contrast text with border)
   ↓
4. Generate Variation: immersive (very subtle text)
   ↓
5. Output Exactly 3 Files (+ a Test & Compare manifest, uploaded by hand)
```

---

## Technology Stack

| Component | Technology | Version | Purpose |
|-----------|-----------|---------|---------|
| Core Language | Python | 3.8+ | Main implementation |
| Data Structures | dataclasses | stdlib | Type-safe, immutable |
| Persistence | JSONL | N/A | Append-only event store |
| File Locking | filelock | 3.9+ | Concurrent safety |
| Image Processing | Pillow | 10.0+ | Thumbnail generation |
| YouTube API | google-api-python-client | 2.0+ | Analytics & upload |
| OAuth | google-auth-oauthlib | 1.4+ | Authentication |
| Testing | pytest | 7.4+ | Unit & integration tests |

---

## Design Principles

### 1. Event Sourcing
All state changes recorded as immutable events:
- Complete audit trail
- Time-travel queries
- Rollback capability
- Corruption detection (CRC32)

### 2. Site Isolation
Complete data segregation per site_id:
- Filesystem-level partitioning
- Query scoping enforcement
- Zero cross-site leakage

### 3. Platform Adapters
Encapsulate platform-specific logic:
- Extensible (add new platforms easily)
- Testable (mock adapters)
- Maintainable (one file per platform)

### 4. Validation
Safety through refusal, not silent correction:
- `PlatformValidator` separates errors from warnings
- `YouTubeUploader.upload_video` validates the exact snippet it will send and raises before any API call if it fails

### 5. Non-Interfering Design
Library approach, not framework:
- Import and use, don't modify existing code
- Optional adoption for existing projects
- Zero dependencies on external services (except the YouTube API)

### 6. Test-Driven Development
Thorough test coverage:
- Tests written first
- No feature without tests
- Regression prevention

---

## Extension Points

### Adding New Platforms

```python
# 1. Create adapter
class InstagramAdapter(BasePlatformAdapter):
    def get_title_limit(self) -> int:
        return 2200  # Caption limit

# 2. Register in platforms/__init__.py
# 3. Use immediately
manager = SEOManager(site_id="my_instagram", platform=InstagramAdapter())
```

### Adding New Generators

```python
# 1. Extend BaseSEOGenerator
class BlogMetadataGenerator(BaseSEOGenerator):
    def _generate_title(self, topic, keywords, **kwargs):
        # Blog-specific logic
        pass

# 2. Register in manager._get_platform_generator()
# 3. Auto-selected for blog platform
```

---

## Security Considerations

### OAuth Credentials
- Never commit `youtube_credentials.json`
- Use environment variables in production
- Rotate credentials periodically
- Read-only scopes (youtube.readonly, yt-analytics.readonly)

### Event Store
- Append-only (no deletions)
- CRC32 checksums (corruption detection)
- FileLock (concurrent safety)
- Site isolation (no cross-access)

### Input Validation
- All metadata validated before persistence
- Platform constraints enforced
- Word count limits
- Safe character handling

---

## Performance Characteristics

### Metadata Generation
- **Speed:** <50ms (no corpus download on any path)
- **Memory:** <10MB
- **Disk:** ~1KB per event

### Thumbnail Generation
- **Speed:** 1-3 seconds per variation (3-9 seconds for set of 3)
- **Memory:** ~50MB (Pillow image processing)
- **Disk:** 1.5-2MB per thumbnail PNG

### Analytics Queries
- **Speed:** <100ms for typical queries
- **Scalability:** Linear with event count
- **Index Performance:** O(1) lookups by site/date/type

### YouTube API
- **Rate Limit:** 10,000 units/day (free tier)
- **Upload Cost:** 1,600 units per video
- **Analytics Cost:** 1-10 units per query

---

## Deployment Models

### Local Development
```bash
pip install -e /path/to/seo-manager
python my_script.py
```

### CI/CD Integration
```yaml
# .github/workflows/generate-seo.yml
steps:
  - pip install -e ./seo-manager
  - python scripts/generate_metadata.py
```

### Cron Jobs (Daily Analytics)
```bash
0 6 * * * cd /project && .venv/bin/python sync_youtube.py
```

---

## Monitoring & Observability

### Test Coverage
```bash
pytest tests/ --cov=seo_manager --cov-report=html
# coverage floor is set in .coveragerc
```

### Event Store Health
```python
# Check corruption
events = store.query_by_site(site_id="my_site")
# All events have valid CRC32 checksums

# Check size
import os
size_mb = os.path.getsize("data/seo/my_site/events.jsonl") / 1024 / 1024
```

### Analytics Summary
```python
summary = manager.analytics.get_summary()
# Monitor: total_views, avg_ctr, content_count
```

---

## Future Architecture Considerations

### Scalability
- **Current:** Single-machine, file-based (sufficient for 100K+ events)
- **Future:** Distributed event store (Kafka) if multi-machine needed

### Additional Platforms
- Instagram Reels (15-30 min to add)
- TikTok (15-30 min to add)
- LinkedIn (15-30 min to add)

### External Integrations
- Google Analytics API (blog performance)
- Twitter API (tweet analytics)
- Third-party SEO tools (SEMrush, Ahrefs)

---

## Code Organization

```
seo_manager/
├── core/              # Metadata, events, manager
├── generation/        # Base + YouTube generator
├── storage/           # Event store
├── platforms/         # Adapters
├── analytics/         # Tracker + YouTube sync
├── validation/        # SEO rules
└── youtube/           # Thumbnails, uploads, video SEO
```

---

## Dependencies

**Runtime (all required, see `pyproject.toml`):**
- filelock (concurrency)
- pyyaml (config)
- Pillow (thumbnails)
- google-api-python-client, google-auth, google-auth-httplib2, google-auth-oauthlib (YouTube API and OAuth)
- cryptography (brand credentials)
- structlog (logging)

**Development:**
- pytest (testing)
- ruff (linting and formatting)
- mypy (type checking)

---

## Integration Patterns

### New Project (Recommended)
```python
from seo_manager import SEOManager, ContentType
from seo_manager.platforms import YouTubeAdapter

manager = SEOManager(site_id="my_channel", platform=YouTubeAdapter())
metadata = manager.generate(...)
manager.save(metadata)
```

### Existing Project (Optional)
```python
# Run SEO Manager in parallel
# Keep existing code unchanged
# Use for analytics tracking only
seo_manager.analytics.track_performance(...)
```

---

## Quality Assurance

**Testing Strategy:**
- Unit tests: core functionality
- Integration tests: end-to-end workflows

**Code Quality:**
- Type hints throughout
- Docstrings on all public methods
- Clean architecture (SOLID principles)
- No circular dependencies

---

**System is production-ready and fully documented.**
