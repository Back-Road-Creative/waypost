"""A recommendation states a priority the rule chose, never a measurement.

``SEORecommendation`` used to carry both an ``impact`` ("high"/"medium") and a
``confidence`` (0.85, 0.75, 0.70, 0.65). Every one of those is a literal typed
into ``generate_recommendations`` when the rule was written -- nothing measures
either against any outcome, and no caller acts on them. The demo script printed
the confidence as ``Confidence: 85%``, which reads as a statistic to the
operator evaluating this product.

A ranking a human chose per rule is legitimate and useful; calling it an impact
and pairing it with a percentage is the part that is not.
"""

import shutil
import tempfile
from dataclasses import fields, replace
from pathlib import Path

import pytest

from seo_manager import SEOManager
from seo_manager.analytics.seo_optimizer import SEOOptimizer, SEORecommendation
from seo_manager.core.metadata import ContentType
from seo_manager.platforms import YouTubeAdapter


@pytest.fixture
def manager():
    temp_dir = tempfile.mkdtemp()
    yield SEOManager(
        site_id="recommendation_honesty",
        platform=YouTubeAdapter(),
        storage_path=Path(temp_dir),
    )
    shutil.rmtree(temp_dir)


def _field_names():
    return {f.name for f in fields(SEORecommendation)}


def test_a_recommendation_carries_no_confidence_it_never_measured():
    """The value was a per-rule literal rendered to the operator as a
    percentage. Nothing computed it and nothing read it back, so the honest
    record is no field at all rather than a number that invites belief."""
    assert "confidence" not in _field_names()


def test_the_ranking_is_named_for_what_it_is():
    """``impact`` claims a measured effect on a metric. The value is a
    priority the rule's author chose, so the field says priority."""
    names = _field_names()
    assert "priority" in names
    assert "impact" not in names


def test_a_short_title_is_ranked_high_by_its_own_rule(manager):
    """The ranking still arrives, and still varies by rule -- this change
    removes a false claim, not the ordering an operator triages by."""
    metadata = manager.generate(
        content_type=ContentType.VIDEO,
        topic="Short",
        body="A body for the short-title rule to fire against.",
        existing_tags=[],
    )
    metadata = replace(metadata, title="Tiny")
    manager.save(metadata)

    recommendations = SEOOptimizer(manager).generate_recommendations(
        metadata.content_id
    )

    title_recs = [r for r in recommendations if r.category == "title"]
    assert title_recs, "the short-title rule should have fired"
    assert title_recs[0].priority == "high"
