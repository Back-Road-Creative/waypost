"""Recommendations separate API limits, editorial style and measured data.

Before this change `predict_performance` reported `confidence = score/factors`,
a number built from fixed weights that is nonzero with no history at all, and
scaled the observed views/click rate by it. `generate_recommendations` told an
operator with no history to "expand title to 50-70 chars for better CTR". Three
tag targets (8-12, 10-15, 15) and two title caps (70 / 100) disagreed.

The contract pinned here: every prediction names the evidence it rests on
(state, sample size, source); no field is called a confidence or a
probability; nothing is promised without data; one module owns the numbers.
"""

import inspect
import shutil
import tempfile
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from seo_manager import SEOManager
from seo_manager.analytics import metric_source, title_selection
from seo_manager.analytics.seo_optimizer import (
    MIXED_DENOMINATORS,
    EvidenceState,
    SEOOptimizer,
)
from seo_manager.core.metadata import ContentType
from seo_manager.platforms import YouTubeAdapter
from seo_manager.platforms import youtube as yt
from seo_manager.validation.youtube_best_practices import (
    YouTubeBestPracticesValidator,
)

CARD = metric_source.YOUTUBE_CARD_CLICK_RATE


def _performer(i, *, views=1000, ctr=None, source=CARD, age_days=1):
    performer = {
        "content_id": f"v{i}",
        "views": views,
        "published_at": (datetime.now() - timedelta(days=age_days)).isoformat(),
        "last_updated": (datetime.now() - timedelta(days=age_days)).isoformat(),
    }
    if ctr is not None:
        performer["ctr"] = ctr
        performer["ctr_source"] = source
    return performer


class _Manager:
    def __init__(self, performers, titles=None, tags=("travel",)):
        self._meta = {
            p["content_id"]: SimpleNamespace(
                title=(titles or {}).get(p["content_id"], "T" * 60),
                tags=list(tags),
                keywords=[],
                quality_score=0.8,
            )
            for p in performers
        }
        self.analytics = SimpleNamespace(
            get_summary=lambda: {"top_performers": list(performers)}
        )

    def get_metadata(self, content_id):
        return self._meta.get(content_id)


def _optimizer(performers, **kw):
    opt = SEOOptimizer(_Manager(performers, **kw))
    opt.analyze_patterns(min_views=1)
    return opt


def _candidate(title="A" * 45):
    return SimpleNamespace(title=title, tags=["travel"], keywords=[], quality_score=0.8)


# --------------------------------------------------------------- prediction


def test_empty_history_is_no_history_with_no_numbers_dressed_as_scores():
    opt = _optimizer([])

    p = opt.predict_performance(_candidate("A" * 45))

    assert p["evidence_state"] == EvidenceState.NO_HISTORY == "no_history"
    assert p["evidence_sample_size"] == 0
    assert p["estimated_views"] == 0.0
    assert p["estimated_ctr"] == 0.0
    # a 45-char title with no data used to score 0.125
    assert p["heuristic_score"] is None


def test_populated_history_is_still_not_called_a_confidence():
    opt = _optimizer([_performer(i, ctr=0.04) for i in range(12)])

    p = opt.predict_performance(_candidate())

    assert p["evidence_state"] == EvidenceState.MEASURED
    assert p["evidence_sample_size"] == 12
    assert p["evidence_source"] == CARD
    assert isinstance(p["heuristic_score"], float)
    for key in p:
        assert "confidence" not in key and "probab" not in key


def test_observed_means_are_not_scaled_by_an_invented_factor():
    opt = _optimizer([_performer(i, views=500, ctr=0.04) for i in range(12)])

    p = opt.predict_performance(_candidate())

    # the observed mean of the top patterns, not mean * heuristic score
    assert p["estimated_views"] == pytest.approx(500)
    assert p["estimated_ctr"] == pytest.approx(0.04)


def test_sparse_history_is_labelled_sparse_with_its_n():
    opt = _optimizer([_performer(i) for i in range(3)])

    p = opt.predict_performance(_candidate())

    assert p["evidence_state"] == EvidenceState.SPARSE == "sparse"
    assert p["evidence_sample_size"] == 3


def test_old_evidence_is_labelled_stale():
    opt = _optimizer([_performer(i, age_days=900) for i in range(12)])

    p = opt.predict_performance(_candidate())

    assert p["evidence_state"] == EvidenceState.STALE == "stale"
    assert p["evidence_sample_size"] == 12


def test_mixed_denominators_are_reported_not_averaged():
    performers = [
        _performer(i, ctr=0.04, source=CARD if i % 2 else metric_source.UNKNOWN)
        for i in range(12)
    ]
    titles = {f"v{i}": "T" * (40 + i) for i in range(12)}
    opt = _optimizer(performers, titles=titles)

    p = opt.predict_performance(_candidate())

    assert p["evidence_source"] == MIXED_DENOMINATORS


def test_comparison_declines_to_pick_a_winner_without_measured_evidence():
    opt = _optimizer([_performer(i) for i in range(3)])

    result = opt.compare_metadata(_candidate("A" * 45), _candidate("B" * 65))

    assert result["recommendation"] is None
    assert result["evidence_state"] == "sparse"
    assert "confidence_delta" not in result


def test_comparison_with_measured_evidence_uses_the_heuristic_score():
    opt = _optimizer([_performer(i, ctr=0.04) for i in range(12)])

    result = opt.compare_metadata(_candidate("A" * 60), _candidate("B" * 5))

    assert result["recommendation"] == "variant_a"
    assert result["heuristic_score_delta"] > 0


# ----------------------------------------------------------- recommendations


@pytest.fixture
def manager():
    temp_dir = tempfile.mkdtemp()
    yield SEOManager(
        site_id="honest",
        platform=YouTubeAdapter(),
        storage_path=Path(temp_dir),
    )
    shutil.rmtree(temp_dir)


def _saved(manager, title, tags=()):
    md = manager.generate(
        content_type=ContentType.VIDEO,
        topic="Topic",
        body="A body for the rules to fire against.",
        existing_tags=[],
    )
    md = replace(md, title=title, tags=list(tags))
    manager.save(md)
    return md.content_id


def test_no_history_title_advice_is_style_not_a_ctr_claim(manager):
    cid = _saved(manager, "Tiny")

    recs = SEOOptimizer(manager).generate_recommendations(cid)

    title = [r for r in recs if r.category == "title"]
    assert title and title[0].basis == "style"
    text = (title[0].issue + title[0].suggestion).lower()
    assert "ctr" not in text and "better" not in text
    assert "style" in text and "not measured" in text
    assert title[0].evidence_n == 0


def test_measured_title_advice_cites_its_sample_size(manager):
    cid = _saved(manager, "Tiny")
    opt = SEOOptimizer(manager)
    opt.patterns = {
        "tags": [],
        "title_lengths": [
            SimpleNamespace(
                value=60,
                perf_score=1.0,
                sample_size=1,
                evidence_age_days=1.0,
                dated_sample_size=1,
                ctr_sample_size=0,
                ctr_source=metric_source.UNKNOWN,
            )
            for _ in range(12)
        ],
        "keywords": [],
        "quality_scores": [],
    }
    opt._analyzed = True

    recs = opt.generate_recommendations(cid)

    title = next(r for r in recs if r.category == "title")
    assert title.basis == "measured"
    assert title.evidence_n == 12
    assert "n=12" in title.suggestion
    assert "association" in title.suggestion.lower()
    assert "better ctr" not in title.suggestion.lower()


def test_sparse_measured_title_advice_is_not_presented_as_measured(manager):
    cid = _saved(manager, "Tiny")
    opt = SEOOptimizer(manager)
    opt.patterns = {
        "tags": [],
        "title_lengths": [
            SimpleNamespace(
                value=60,
                perf_score=1.0,
                sample_size=1,
                evidence_age_days=1.0,
                dated_sample_size=1,
                ctr_sample_size=0,
                ctr_source=metric_source.UNKNOWN,
            )
            for _ in range(2)
        ],
        "keywords": [],
        "quality_scores": [],
    }
    opt._analyzed = True

    title = next(r for r in opt.generate_recommendations(cid) if r.category == "title")

    assert title.basis == "style"


def test_over_api_cap_title_is_an_api_limit_not_style(manager):
    cid = _saved(manager, "X" * (yt.YOUTUBE_TITLE_API_MAX_CHARS + 1))

    recs = SEOOptimizer(manager).generate_recommendations(cid)

    api = [r for r in recs if r.basis == "api_limit"]
    assert api and api[0].category == "title"
    assert str(yt.YOUTUBE_TITLE_API_MAX_CHARS) in api[0].issue


def test_tag_count_target_is_labelled_style_and_matches_the_shared_range(manager):
    cid = _saved(manager, "A fine title of sensible length for a travel video", ["a"])

    recs = SEOOptimizer(manager).generate_recommendations(cid)

    tag = next(r for r in recs if r.category == "tags")
    lo, hi = yt.YOUTUBE_TAG_COUNT_STYLE_RANGE
    assert tag.basis == "style"
    assert f"{lo}-{hi}" in tag.suggestion
    assert "8-12" not in tag.suggestion


# --------------------------------------------------------- one source of truth


def test_api_limits_are_the_documented_ones():
    assert yt.YOUTUBE_TITLE_API_MAX_CHARS == 100
    assert yt.YOUTUBE_TAGS_API_MAX_CHARS == 500


def test_title_selector_cap_is_the_api_cap_without_importing_the_optimizer():
    assert title_selection.DEFAULT_POLICY.max_title_chars == (
        yt.YOUTUBE_TITLE_API_MAX_CHARS
    )
    source = inspect.getsource(title_selection)
    assert "seo_optimizer" not in source


def test_adapter_exposes_the_api_limits_and_keeps_display_cap_distinct():
    adapter = YouTubeAdapter()
    assert adapter.get_title_api_limit() == yt.YOUTUBE_TITLE_API_MAX_CHARS
    assert adapter.get_tag_char_limit() == yt.YOUTUBE_TAGS_API_MAX_CHARS
    assert adapter.get_title_limit() == yt.YOUTUBE_TITLE_DISPLAY_CHARS
    assert adapter.get_title_limit() <= adapter.get_title_api_limit()
    assert adapter.get_tag_limit() == yt.YOUTUBE_TAG_COUNT_STYLE_RANGE[1]


def test_validator_uses_the_shared_constants():
    v = YouTubeBestPracticesValidator()
    md = SimpleNamespace(
        title="X" * (yt.YOUTUBE_TITLE_API_MAX_CHARS + 1),
        description="d" * 300,
        tags=["t"] * 12,
        quality_score=None,
        keyword_density=None,
    )
    errors = [i for i in v.validate(md) if i.severity == "error"]
    assert any(str(yt.YOUTUBE_TITLE_API_MAX_CHARS) in i.issue for i in errors)

    ok = replace_title(md, "X" * yt.YOUTUBE_TITLE_API_MAX_CHARS)
    assert not [i for i in v.validate(ok) if i.severity == "error"]


def test_validator_labels_the_display_cap_as_style_not_a_rejection():
    v = YouTubeBestPracticesValidator()
    md = SimpleNamespace(
        title="X" * (yt.YOUTUBE_TITLE_DISPLAY_CHARS + 5),
        description="d" * 300,
        tags=["t"] * 12,
        quality_score=None,
        keyword_density=None,
    )
    issues = [i for i in v.validate(md) if i.category == "title"]
    assert issues and all(i.severity != "error" for i in issues)
    assert any("style" in i.best_practice.lower() for i in issues)


def test_validator_flags_tags_over_the_api_character_budget():
    v = YouTubeBestPracticesValidator()
    md = SimpleNamespace(
        title="A sensible title of fifty to seventy characters in length ok",
        description="d" * 300,
        tags=["x" * 50 for _ in range(11)],  # 550 chars
        quality_score=None,
        keyword_density=None,
    )
    errors = [i for i in v.validate(md) if i.severity == "error"]
    assert any(i.category == "tags" and "500" in i.issue for i in errors)


def test_optimizer_tag_cap_is_the_shared_style_ceiling():
    from seo_manager.analytics import seo_optimizer

    assert seo_optimizer.TAG_COUNT_STYLE_RANGE == yt.YOUTUBE_TAG_COUNT_STYLE_RANGE


def replace_title(md, title):
    return SimpleNamespace(**{**vars(md), "title": title})
