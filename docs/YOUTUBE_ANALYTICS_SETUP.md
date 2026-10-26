# YouTube Analytics API Setup Guide

**Purpose:** Enable automatic analytics sync from YouTube to SEO Manager
**Time Required:** 15-30 minutes (one-time setup)
**Benefit:** Automatic tracking of views, CTR, engagement without manual data entry

---

## Prerequisites

1. YouTube channel with videos
2. Google Cloud account (free tier is sufficient)
3. Python with SEO Manager installed

---

## Step 1: Install SEO Manager

The Google API libraries are declared dependencies of this package, so a normal
install brings them:

```bash
# from the root of this repository, inside a virtual environment
pip install -e .
```

There is no analytics-free mode to fall back to. If `google-api-python-client`,
`google-auth`, `google-auth-oauthlib` or `google-auth-httplib2` is missing,
importing `seo_manager.analytics.youtube_sync` raises `ImportError` at import
time rather than degrading quietly — re-run the install above.

---

## Step 2: Create Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Click "Create Project"
3. Name: "SEO Manager YouTube Analytics"
4. Click "Create"

---

## Step 3: Enable YouTube Data API v3

1. In Google Cloud Console, go to "APIs & Services" → "Library"
2. Search for "YouTube Data API v3"
3. Click "Enable"
4. Also enable "YouTube Analytics API"

---

## Step 4: Create OAuth 2.0 Credentials

1. Go to "APIs & Services" → "Credentials"
2. Click "Create Credentials" → "OAuth client ID"
3. Configure consent screen if prompted:
   - User Type: External
   - App name: "SEO Manager"
   - Add your email
   - Skip optional fields
   - Add scopes: `youtube.readonly`, `yt-analytics.readonly`
4. Application type: **Desktop app**
5. Name: "SEO Manager Desktop"
6. Click "Create"
7. **Download JSON** → Save as `client_secret.json`

---

## Step 5: Generate User Credentials (One-Time)

```python
from seo_manager.analytics.youtube_sync import YouTubeAnalyticsSync

# Run once to authenticate
credentials_path = YouTubeAnalyticsSync.create_credentials_from_flow(
    client_secrets_path="client_secret.json",  # pragma: allowlist secret
    output_path="youtube_credentials.json"
)

# This will:
# 1. Open browser
# 2. Ask you to sign in to Google
# 3. Grant permissions to access YouTube Analytics
# 4. Save credentials to youtube_credentials.json

print(f"✅ Credentials saved to: {credentials_path}")
```

**Security:** Keep `youtube_credentials.json` secret (add to .gitignore)

The setup helper saves credentials using an owner-only temporary file (mode
`0600` on POSIX) and replaces the destination only after the complete contents
have been flushed. Re-running setup also replaces an older, more permissive
file with an owner-only file. A serialization or save failure leaves the old
credentials intact and cleans up the temporary file when the process can
handle the failure. An existing destination symlink is replaced, not followed;
its target remains unchanged. Use a trusted output directory that already
exists. This does not change permissions on files until setup is rerun, and
it does not protect against another process modifying that directory.

---

## Step 6: Use YouTube Analytics Sync

### Basic Usage

```python
from pathlib import Path

from seo_manager import SEOManager, ContentType
from seo_manager.platforms import YouTubeAdapter
from seo_manager.analytics.youtube_sync import YouTubeAnalyticsSync

# Initialize SEO Manager
manager = SEOManager(
    site_id="my_youtube_channel",
    platform=YouTubeAdapter(),
    storage_path=Path("./data/seo")
)

# Initialize YouTube sync
sync = YouTubeAnalyticsSync(credentials_path="youtube_credentials.json")

# Sync analytics for one video
sync.sync_video_analytics(
    manager=manager,
    video_id="dQw4w9WgXcQ",  # YouTube video ID
    content_id="episode_042"  # Your content ID in SEO Manager
)

# Check the data
performance = manager.analytics.get_content_performance("episode_042")
print(f"Views: {performance['total_views']}")
ctr = performance["ctr"]
print(f"CTR: {'unmeasured' if ctr is None else f'{ctr:.2%}'} ({performance['ctr_source']})")
```

---

### Batch Sync (Recommended)

```python
# Sync analytics for all your videos at once
video_mapping = {
    "YouTube_Video_ID_1": "episode_001",
    "YouTube_Video_ID_2": "episode_002",
    "YouTube_Video_ID_3": "episode_003",
    # ... add all your videos
}

results = sync.batch_sync(manager, video_mapping)

# Check results
for result in results:
    if result['success']:
        print(f"✅ Synced {result['content_id']}")
    else:
        print(f"❌ Failed {result['content_id']}: {result.get('error')}")
```

---

### Automated Daily Sync (Cron Job)

Create `sync_youtube_analytics.py`:

```python
#!/usr/bin/env python3
"""
Daily YouTube analytics sync script.

Run via cron: 0 6 * * * /path/to/sync_youtube_analytics.py
"""

from seo_manager import SEOManager
from seo_manager.platforms import YouTubeAdapter
from seo_manager.analytics.youtube_sync import YouTubeAnalyticsSync
from pathlib import Path

# Initialize
manager = SEOManager(
    site_id="my_podcast_youtube",
    platform=YouTubeAdapter(),
    storage_path=Path("./data/seo")
)

sync = YouTubeAnalyticsSync(credentials_path="youtube_credentials.json")

# Video mapping (update this with your videos)
VIDEO_MAPPING = {
    "abc123": "episode_001",
    "def456": "episode_002",
    # Add more as you publish
}

# Sync
results = sync.batch_sync(manager, VIDEO_MAPPING)

# Report
successful = sum(1 for r in results if r['success'])
print(f"✅ Synced {successful}/{len(results)} videos")

# Get summary
summary = manager.analytics.get_summary()
print(f"Total views: {summary['performance_metrics']['total_views']}")
avg_ctr = summary['performance_metrics']['avg_ctr']
print(f"Avg CTR: {avg_ctr:.2%}" if avg_ctr is not None else "Avg CTR: unmeasured")
# performance_metrics also carries total_content_with_performance (how many
# distinct videos have any performance data) and top_performers_cap (the size
# of the top_performers slice below) -- read these instead of assuming
# len(summary['top_performers']) is the whole population; it is only the cap.
```

**Crontab entry** (runs daily at 6 AM):
```bash
0 6 * * * cd /path/to/project && /path/to/.venv/bin/python sync_youtube_analytics.py >> logs/youtube_sync.log 2>&1
```

---

## Available Metrics

### From YouTube Data API v3:
- **viewCount** - Total views
- **likeCount** - Total likes
- **commentCount** - Total comments

### Calculated:
- **Engagement Rate** - (likes + comments) / views

### From YouTube Analytics API (Advanced):
- **Card impressions and card click rate** - recorded as the click-through proxy.
  Thumbnail impressions and impression CTR are shown only in YouTube Studio;
  the API does not expose them, so the sync never records them.

### Reach probe (YouTube Reporting API)
The Reporting API's channel reach reports may carry thumbnail impressions and
click rate per video. `seo_manager.analytics.youtube_reach_probe` asks whether a
token can read one, and prints one `REACH_PROBE verdict=...` line naming the
step that stops it (no reach report type, no job, no report yet, columns
missing, or `reach-available` with `has_traffic_source`):

```bash
python -m seo_manager.analytics.youtube_reach_probe --sops-file <dir>/youtube_upload_token.json.enc
```

It is read-only. `--create-job` creates the reach reporting job if none exists;
YouTube then fills it with daily reports, the first within about 48 hours. An
API refusal prints its status and message (`verdict=api-error`), such as a
token without the `yt-analytics.readonly` scope.

Watch time is requested from the API but not stored. The API offers other
metrics (average view duration, traffic sources, and so on) that this package
does not fetch or store.

`YouTubeAnalyticsSync.batch_sync` syncs the videos in the mapping the caller
supplies, one `sync_video_analytics` call each. Any extra fields or sync limits
(such as a cap on how many videos are synced per run) belong to the calling code.

---

## Quota Limits

**Free Tier:**
- 10,000 units per day
- Each video stats request: ~1 unit
- Can sync ~10,000 videos per day

**Quota exceeded?**
- Request quota increase (usually approved)
- Cache results (don't sync every minute)
- Use batch requests

---

## Troubleshooting

### "Credentials not found"
- Ensure `youtube_credentials.json` exists
- Re-run authentication flow if expired
- Check file permissions

### "API quota exceeded"
- Wait 24 hours for reset
- Request quota increase
- Reduce sync frequency

### "Invalid video ID"
- Verify video ID is correct (11 characters)
- Check video is published (not draft/private)
- Ensure your account has access

### "Insufficient permissions"
- Re-run OAuth flow
- Ensure scopes include `youtube.readonly` and `yt-analytics.readonly`
- Check API is enabled in Google Cloud

---

## Security Best Practices

1. **Add to .gitignore:**
```gitignore
youtube_credentials.json
client_secret.json
*.json  # All credential files
```

2. **Environment Variables** (production):
```python
import os
credentials_path = os.getenv('YOUTUBE_CREDS_PATH', 'youtube_credentials.json')
```

3. **Rotate credentials** periodically

4. **Use service account** for server deployments

---

## Example: Podcast Channel Integration

```python
# In a podcast project
from pathlib import Path

from seo_manager import SEOManager
from seo_manager.platforms import YouTubeAdapter
from seo_manager.analytics.youtube_sync import YouTubeAnalyticsSync

# One-time setup
manager = SEOManager(
    site_id="my_podcast_youtube",
    platform=YouTubeAdapter(),
    storage_path=Path("./data/seo")
)

sync = YouTubeAnalyticsSync(credentials_path="../youtube_credentials.json")

# After publishing episode 42
sync.sync_video_analytics(
    manager=manager,
    video_id="YOUR_VIDEO_ID_HERE",
    content_id="episode_042"
)

# Later: Check what's working
summary = manager.analytics.get_summary()
top_episodes = summary['top_performers']

print(f"Best episode: {top_episodes[0]['content_id']} with {top_episodes[0]['views']} views")
```

---

## Next Steps

1. ✅ Complete OAuth setup (Steps 1-5)
2. ✅ Test with one video
3. ✅ Set up daily cron job
4. ✅ Monitor analytics in SEO Manager
5. ✅ Optimize based on data!

---

## FAQ

**Q: Is this safe?**
A: Yes. OAuth only grants read-only access to your analytics. Cannot modify videos.

**Q: Do I need to sync every video?**
A: No. Only sync videos you want to track in SEO Manager.

**Q: How often should I sync?**
A: Daily is recommended. YouTube updates stats hourly but daily is sufficient.

**Q: Can I use with multiple channels?**
A: Yes. Create separate SEOManager instances per channel, one sync can handle all.

**Q: What if I delete the credentials?**
A: Re-run the authentication flow (Step 5). Takes 2 minutes.

---

**Once configured, YouTube analytics sync is completely automatic!** 🚀
