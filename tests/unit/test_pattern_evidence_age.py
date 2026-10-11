"""A ranked pattern must say how old the evidence behind it is.

`_perf_score` is the ranking key, and it is multiplied by
`_age_decay_weight(last_updated)` before it is returned -- a pattern resting on
18-month-old samples is handed to the caller already discounted to roughly a
quarter of its blend. `PerformancePattern` carried no timestamp, no age and no
weight, so that discount was invisible: a strong-but-stale pattern and a
genuinely weak fresh one arrived as the same small number in the same sorted
list, with nothing to tell them apart.

This is the same shape as the `avg_ctr` fix -- a number whose meaning depended
on a denominator the reader could not see -- so it takes the same answer:
report the quantity, report how many samples it rests on, and report the
unknown as `None` rather than inventing a value.
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


def _iso(days_ago=0):
    return (datetime.now() - timedelta(days=days_ago)).isoformat()


def _analyze(top, meta):
    opt = SEOOptimizer(_FakeManager(top, meta))
    return opt.analyze_patterns(min_views=100)


class TestEvidenceAgeIsReported:
    def test_stale_evidence_reports_its_age_in_days(self):
        """Two half-lives of staleness must be visible on the pattern."""
        top = [{"content_id": "a", "views": 5000, "last_updated": _iso(days_ago=360)}]
        meta = {"a": _FakeMetadata("Title A", ["stale"])}

        pattern = _analyze(top, meta)["title_lengths"][0]

        assert pattern.evidence_age_days == pytest.approx(360, abs=1)
        assert pattern.dated_sample_size == 1

    def test_the_discount_applied_to_the_score_is_reported(self):
        """The pattern states the recency weight, not just the discounted score."""
        top = [{"content_id": "a", "views": 5000, "last_updated": _iso(days_ago=360)}]
        meta = {"a": _FakeMetadata("Title A", ["stale"])}

        pattern = _analyze(top, meta)["title_lengths"][0]

        # 360 days = two half-lives of _DECAY_HALF_LIFE_DAYS.
        assert pattern.age_decay_weight == pytest.approx(0.25, rel=0.02)

    def test_reported_weight_is_the_weight_actually_applied(self):
        """The reported weight must be the one the ranking used, not a re-guess.

        A single performer is its own normalization base, so the blend is
        exactly 1.0 and `perf_score` reduces to the decay weight alone. If the
        two ever drift, the field is decoration rather than an explanation.
        """
        top = [{"content_id": "a", "views": 5000, "last_updated": _iso(days_ago=90)}]
        meta = {"a": _FakeMetadata("Title A", ["tag"])}

        pattern = _analyze(top, meta)["title_lengths"][0]

        assert pattern.perf_score == pytest.approx(pattern.age_decay_weight)


class TestUndatedEvidence:
    def test_undated_evidence_is_none_not_today(self):
        """No timestamp means unknown age -- never a fabricated zero."""
        top = [{"content_id": "a", "views": 5000}]
        meta = {"a": _FakeMetadata("Title A", ["undated"])}

        pattern = _analyze(top, meta)["title_lengths"][0]

        assert pattern.evidence_age_days is None
        assert pattern.dated_sample_size == 0

    def test_undated_evidence_still_reports_the_neutral_weight(self):
        """`_age_decay_weight` returns 1.0 for an absent timestamp; so does this.

        1.0 is what the ranking really did, so it is what the pattern reports.
        `evidence_age_days is None` is what separates this from fresh evidence.
        """
        top = [{"content_id": "a", "views": 5000}]
        meta = {"a": _FakeMetadata("Title A", ["undated"])}

        pattern = _analyze(top, meta)["title_lengths"][0]

        assert pattern.age_decay_weight == pytest.approx(1.0)

    def test_unparseable_timestamp_is_unknown_age(self):
        """The decay ignores a timestamp it cannot read; the age must too."""
        top = [{"content_id": "a", "views": 5000, "last_updated": "not-a-date"}]
        meta = {"a": _FakeMetadata("Title A", ["garbled"])}

        pattern = _analyze(top, meta)["title_lengths"][0]

        assert pattern.evidence_age_days is None
        assert pattern.dated_sample_size == 0
        assert pattern.age_decay_weight == pytest.approx(1.0)


class TestSamplesOfDifferentAges:
    def test_tag_age_averages_only_the_dated_samples(self):
        """An undated sample is not a sample aged zero -- it is out of the mean.

        Averaging it in as "today" would report a tag resting on one ancient
        video as half-fresh, which is the `avg_ctr`-substitutes-0 mistake with
        a different unit.
        """
        top = [
            {"content_id": "a", "views": 5000, "last_updated": _iso(days_ago=360)},
            {"content_id": "b", "views": 5000},
        ]
        meta = {
            "a": _FakeMetadata("Title A", ["shared"]),
            "b": _FakeMetadata("Title B", ["shared"]),
        }

        tag = _analyze(top, meta)["tags"][0]

        assert tag.value == "shared"
        assert tag.sample_size == 2
        assert tag.dated_sample_size == 1
        assert tag.evidence_age_days == pytest.approx(360, abs=1)

    def test_tag_weight_averages_every_sample(self):
        """Every sample was weighted, so every sample is in the weight mean."""
        top = [
            {"content_id": "a", "views": 5000, "last_updated": _iso(days_ago=360)},
            {"content_id": "b", "views": 5000, "last_updated": _iso(days_ago=0)},
        ]
        meta = {
            "a": _FakeMetadata("Title A", ["shared"]),
            "b": _FakeMetadata("Title B", ["shared"]),
        }

        tag = _analyze(top, meta)["tags"][0]

        # (0.25 + 1.0) / 2
        assert tag.age_decay_weight == pytest.approx(0.625, rel=0.02)
        assert tag.dated_sample_size == 2
        assert tag.evidence_age_days == pytest.approx(180, abs=1)
