"""Tests for performance-weighted SEO ranking + the title-correspondence trap.

Part 1 — `analyze_patterns` / `get_optimal_title_length` must rank on the
now-populated `ctr` (blended with `avg_views`), not views alone.

Part 2 — historical analytics key to OLD title strings; a later title rewrite
changes them, so a new title must not be tuned against the CTR of a
string that no longer exists. Strategy (also documented in seo_optimizer.py):
performance is keyed by content_id (video), NOT title-string, and each sample's
contribution is age-decayed so stale title-length samples fade out.

These exercise the optimizer against a hand-built fake manager so they need no
storage backend or Google APIs.
"""

from datetime import datetime, timedelta

import pytest

from seo_manager.analytics.seo_optimizer import SEOOptimizer


class _FakeMetadata:
    def __init__(self, title, tags, quality_score=0.8):
        self.title = title
        self.tags = tags
        self.quality_score = quality_score


class _FakeAnalytics:
    def __init__(self, top_performers):
        self._top = top_performers

    def get_summary(self):
        return {"top_performers": self._top}


class _FakeManager:
    def __init__(self, top_performers, metadata_by_id):
        self.analytics = _FakeAnalytics(top_performers)
        self._meta = metadata_by_id

    def get_metadata(self, content_id):
        return self._meta.get(content_id)


def _now_iso(days_ago=0):
    return (datetime.now() - timedelta(days=days_ago)).isoformat()


class TestCtrWeightedTagRanking:
    def test_high_ctr_tag_outranks_high_views_low_ctr(self):
        """A tag on a high-CTR video must outrank a tag on a high-views/low-CTR one."""
        top = [
            {
                "content_id": "a",
                "views": 10000,
                "ctr": 0.01,
                "last_updated": _now_iso(),
            },
            {"content_id": "b", "views": 2000, "ctr": 0.20, "last_updated": _now_iso()},
        ]
        meta = {
            "a": _FakeMetadata("Title A", ["lowctr"]),
            "b": _FakeMetadata("Title B", ["highctr"]),
        }
        opt = SEOOptimizer(_FakeManager(top, meta))
        opt.analyze_patterns(min_views=100)
        ranked = opt.get_top_performing_tags(limit=2)
        assert ranked.index("highctr") < ranked.index("lowctr")


class TestCtrWeightedTitleLength:
    def test_optimal_length_follows_high_ctr_titles(self):
        """get_optimal_title_length must favor lengths of high-CTR videos."""
        # 30-char titles have huge views but terrible CTR; 60-char titles
        # have modest views but great CTR. The CTR weighting should pull the
        # optimal range toward 60, not 30.
        top = [
            {
                "content_id": "v1",
                "views": 50000,
                "ctr": 0.005,
                "last_updated": _now_iso(),
            },
            {
                "content_id": "v2",
                "views": 3000,
                "ctr": 0.18,
                "last_updated": _now_iso(),
            },
            {
                "content_id": "v3",
                "views": 2500,
                "ctr": 0.16,
                "last_updated": _now_iso(),
            },
        ]
        meta = {
            "v1": _FakeMetadata("x" * 30, ["t"]),
            "v2": _FakeMetadata("y" * 60, ["t"]),
            "v3": _FakeMetadata("z" * 62, ["t"]),
        }
        opt = SEOOptimizer(_FakeManager(top, meta))
        lo, hi = opt.get_optimal_title_length()
        assert lo <= 60 <= hi


class TestTitleCorrespondenceDecay:
    def test_stale_sample_decays_below_fresh(self):
        """An old high-CTR title-length sample must weigh less than a fresh one."""
        # Old sample: length 40, fresh sample: length 70. Both same raw CTR.
        # With age-decay the fresh 70 should dominate the optimal range.
        top = [
            {
                "content_id": "old",
                "views": 5000,
                "ctr": 0.15,
                "last_updated": _now_iso(days_ago=900),
            },
            {
                "content_id": "new",
                "views": 5000,
                "ctr": 0.15,
                "last_updated": _now_iso(days_ago=1),
            },
        ]
        meta = {
            "old": _FakeMetadata("a" * 40, ["t"]),
            "new": _FakeMetadata("b" * 70, ["t"]),
        }
        opt = SEOOptimizer(_FakeManager(top, meta))
        _lo, hi = opt.get_optimal_title_length()
        # Fresh 70-char sample dominates; range centers near 70, not 40.
        assert hi > 60

    def test_missing_timestamp_does_not_crash(self):
        """A performer without last_updated is treated as no-decay (weight 1)."""
        top = [{"content_id": "a", "views": 1000, "ctr": 0.1}]
        meta = {"a": _FakeMetadata("t" * 55, ["tag"])}
        opt = SEOOptimizer(_FakeManager(top, meta))
        opt.analyze_patterns(min_views=100)
        assert opt.get_top_performing_tags() == ["tag"]


class TestAgeNormalisedViews:
    """Score on views/day-since-publish, not raw lifetime views."""

    def test_young_low_view_video_outranks_old_high_view_video(self):
        """1,000 views over 400 days must lose to 100 views over 10 days."""
        top = [
            {
                "content_id": "old",
                "views": 1000,
                "ctr": None,
                "published_at": _now_iso(days_ago=400),
                "last_updated": _now_iso(),
            },
            {
                "content_id": "new",
                "views": 100,
                "ctr": None,
                "published_at": _now_iso(days_ago=10),
                "last_updated": _now_iso(),
            },
        ]
        meta = {
            "old": _FakeMetadata("Old title", ["oldtag"]),
            "new": _FakeMetadata("New title", ["newtag"]),
        }
        opt = SEOOptimizer(_FakeManager(top, meta))
        patterns = opt.analyze_patterns(min_views=10)
        by_views = {p.avg_views: p.perf_score for p in patterns["title_lengths"]}
        assert by_views[100] > by_views[1000]


class TestCtrNullRenormalisesWeights:
    def test_missing_ctr_scores_on_views_alone(self):
        """No CTR field -> score is the pure views/day ratio, not 0.4x it."""
        top = [
            {
                "content_id": "solo",
                "views": 500,
                "published_at": _now_iso(days_ago=5),
                "last_updated": _now_iso(),
                # no "ctr" key at all
            }
        ]
        meta = {"solo": _FakeMetadata("t" * 55, ["tag"])}
        opt = SEOOptimizer(_FakeManager(top, meta))
        patterns = opt.analyze_patterns(min_views=10)
        score = patterns["title_lengths"][0].perf_score
        # Sole eligible performer -> views/day ratio to itself is 1.0, decay
        # ~1.0 (last_updated is "now"). If CTR-null were naively treated as
        # ctr=0 in the old blend, this would be 0.4, not ~1.0.
        assert score == pytest.approx(1.0, abs=0.01)


class TestCtrPresentKeepsBlend:
    def test_ctr_present_uses_weighted_blend(self):
        """When CTR is present and non-null, the old 0.6/0.4 blend still applies
        on top of the (now age-normalised) views term."""
        top = [
            {
                "content_id": "a",
                "views": 1000,
                "ctr": 0.05,
                "published_at": _now_iso(days_ago=10),
                "last_updated": _now_iso(),
            },
            {
                "content_id": "b",
                "views": 500,
                "ctr": 0.20,
                "published_at": _now_iso(days_ago=10),
                "last_updated": _now_iso(),
            },
        ]
        meta = {
            "a": _FakeMetadata("Title A", ["t"]),
            "b": _FakeMetadata("Title B", ["t"]),
        }
        opt = SEOOptimizer(_FakeManager(top, meta))
        patterns = opt.analyze_patterns(min_views=10)
        by_views = {p.avg_views: p.perf_score for p in patterns["title_lengths"]}
        # Both published 10 days ago -> vpd(a)=100, vpd(b)=50, max_vpd=100.
        # score(a) = 0.4*(100/100) + 0.6*(0.05/0.20) = 0.55
        # score(b) = 0.4*(50/100)  + 0.6*(0.20/0.20) = 0.80
        assert by_views[500] > by_views[1000]
        assert by_views[500] == pytest.approx(0.80, abs=0.01)
        assert by_views[1000] == pytest.approx(0.55, abs=0.01)


class TestTrackerSurfacesTimestamp:
    def test_top_performers_carry_last_updated(self, tmp_path):
        """Tracker must surface a last_updated per top performer for decay."""
        from seo_manager import SEOManager
        from seo_manager.platforms import YouTubeAdapter

        mgr = SEOManager(site_id="t", platform=YouTubeAdapter(), storage_path=tmp_path)
        mgr.analytics.track_performance(
            content_id="ep1", views=500, clicks=25, impressions=500
        )
        summary = mgr.analytics.get_summary()
        perf = summary["top_performers"][0]
        assert "last_updated" in perf
        assert perf["last_updated"]
