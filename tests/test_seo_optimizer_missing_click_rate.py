"""A click rate nobody measured is not a click rate of zero.

`_perf_score` already refuses the substitution -- when a performer has no `ctr`
the click-rate term is dropped and the views term takes the full weight,
"rather than silently scoring it as 0 (which would understate performance for
every video without the data)". The same function then wrote
`ctr = performer.get("ctr", 0) or 0` four lines below and averaged those
invented zeros into every `PerformancePattern.avg_ctr`, and from there into
`predict_performance`'s `estimated_ctr`. The rate is "frequently absent (no
analytics data, no youtube_sync run yet)", so the understatement was the normal
case, not the edge one.

A pattern now reports the rate it actually measured, `None` when it measured
none, and the size of the sample that rate came from -- the denominator, so a
reader can tell "0.0 across three videos" from "nobody looked".
"""

from datetime import datetime, timedelta

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


def _optimizer(performers, metadata):
    return SEOOptimizer(_FakeManager(performers, metadata))


def _tag(patterns, name):
    return next(p for p in patterns["tags"] if p.value == name)


def test_a_tag_with_no_measured_rate_reports_none_not_zero():
    top = [{"content_id": "a", "views": 5000, "last_updated": _now_iso()}]
    patterns = _optimizer(
        top, {"a": _FakeMetadata("Title A", ["unmeasured"])}
    ).analyze_patterns(min_views=100)

    pattern = _tag(patterns, "unmeasured")
    assert pattern.avg_ctr is None
    assert pattern.ctr_sample_size == 0
    # The pattern is still real -- the video was watched, it just was not
    # click-rate-measured. Dropping it would lose the views evidence too.
    assert pattern.sample_size == 1


def test_an_unmeasured_video_does_not_drag_a_tags_rate_down():
    top = [
        {"content_id": "a", "views": 5000, "ctr": 0.20, "last_updated": _now_iso()},
        {"content_id": "b", "views": 5000, "last_updated": _now_iso()},
    ]
    meta = {
        "a": _FakeMetadata("Title A", ["shared"]),
        "b": _FakeMetadata("Title B", ["shared"]),
    }

    pattern = _tag(_optimizer(top, meta).analyze_patterns(min_views=100), "shared")

    # The old average was 0.10 -- the measured 0.20 halved by an invented 0.
    assert pattern.avg_ctr == 0.20
    assert pattern.ctr_sample_size == 1
    assert pattern.sample_size == 2


def test_a_measured_zero_is_kept_and_counted():
    top = [
        {"content_id": "a", "views": 5000, "ctr": 0.0, "last_updated": _now_iso()},
        {"content_id": "b", "views": 5000, "ctr": 0.10, "last_updated": _now_iso()},
    ]
    meta = {
        "a": _FakeMetadata("Title A", ["shared"]),
        "b": _FakeMetadata("Title B", ["shared"]),
    }

    pattern = _tag(_optimizer(top, meta).analyze_patterns(min_views=100), "shared")

    # A rate of zero that someone actually measured is evidence, and averages
    # in like any other. The fix is about absence, not about low numbers.
    assert pattern.avg_ctr == 0.05
    assert pattern.ctr_sample_size == 2


def test_title_length_patterns_carry_the_same_distinction():
    top = [
        {"content_id": "a", "views": 5000, "ctr": 0.10, "last_updated": _now_iso()},
        {"content_id": "b", "views": 5000, "last_updated": _now_iso()},
    ]
    meta = {"a": _FakeMetadata("A" * 30, ["t"]), "b": _FakeMetadata("B" * 60, ["t"])}

    patterns = _optimizer(top, meta).analyze_patterns(min_views=100)
    lengths = {p.value: p for p in patterns["title_lengths"]}

    assert lengths[30].avg_ctr == 0.10
    assert lengths[30].ctr_sample_size == 1
    assert lengths[60].avg_ctr is None
    assert lengths[60].ctr_sample_size == 0


def test_the_estimate_is_built_only_from_measured_rates():
    # Four unmeasured videos and one measured at 0.20. Averaging over a hard
    # denominator of five turned a single 0.20 into 0.04.
    top = [
        {"content_id": "a", "views": 5000, "ctr": 0.20, "last_updated": _now_iso()}
    ] + [
        {"content_id": str(i), "views": 5000, "last_updated": _now_iso()}
        for i in range(4)
    ]
    meta = {"a": _FakeMetadata("A" * 30, ["t"])}
    meta.update({str(i): _FakeMetadata("B" * (40 + i), ["t"]) for i in range(4)})
    optimizer = _optimizer(top, meta)

    predictions = optimizer.predict_performance(_FakeMetadata("A" * 30, ["t"]))

    assert predictions["estimated_ctr_sample_size"] == 1
    assert predictions["estimated_ctr"] == 0.20 * predictions["confidence"]


def test_an_estimate_with_nothing_measured_says_so():
    top = [{"content_id": "a", "views": 5000, "last_updated": _now_iso()}]
    optimizer = _optimizer(top, {"a": _FakeMetadata("A" * 30, ["t"])})

    predictions = optimizer.predict_performance(_FakeMetadata("A" * 30, ["t"]))

    # Still a float, because six example scripts format it with `:.2%` and the
    # method's own signature promises floats. The sample size is what says the
    # number is not evidence.
    assert predictions["estimated_ctr"] == 0.0
    assert predictions["estimated_ctr_sample_size"] == 0


def test_the_views_estimate_divides_by_the_patterns_it_has():
    # Two patterns, both at 1000 views/day: the estimate was divided by a hard
    # 5 regardless, so it read 400 * confidence instead of 1000 * it.
    top = [
        {
            "content_id": "a",
            "views": 1000,
            "published_at": _now_iso(1),
            "last_updated": _now_iso(),
        },
        {
            "content_id": "b",
            "views": 1000,
            "published_at": _now_iso(1),
            "last_updated": _now_iso(),
        },
    ]
    meta = {"a": _FakeMetadata("A" * 30, ["t"]), "b": _FakeMetadata("B" * 30, ["t"])}
    optimizer = _optimizer(top, meta)

    predictions = optimizer.predict_performance(_FakeMetadata("A" * 30, ["t"]))

    assert predictions["estimated_views"] == 1000 * predictions["confidence"]
