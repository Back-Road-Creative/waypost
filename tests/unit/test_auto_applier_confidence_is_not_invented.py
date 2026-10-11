"""
The auto-applier's audit record carries no invented confidence.

`apply_learned_config` passed `confidence=0.8` (title), `0.75` (tags) and
`0.7` (thumbnails) — three literals nothing computed — and every handler
passed `rec.get("confidence", 0.7)`, defaulting an absent number to another
literal. All of it landed in `AppliedRecommendation.confidence`, which no
code in this repo ever read back.

These tests pin two things at once:

1. The *structural* claim — there is no field and no parameter left that can
   carry a made-up confidence into an audit record. Same tier as the
   `expected_impact` deletion: the type cannot express the fake value.
2. The *behavioural* claim — what `apply_learned_config` applies is decided
   by the per-type config flags alone. No confidence number participates,
   which is why deleting the literals changes nothing an operator sees.

The confidence `process_recommendations` compares against
`min_confidence_for_auto` is a *caller-supplied* number on the incoming
recommendation, not one this module invents, so it stays.
"""

import json
import logging
from dataclasses import fields
from inspect import signature

from seo_manager.analytics.recommendation_auto_applier import (
    AppliedRecommendation,
    AutoApplierConfig,
    RecommendationAutoApplier,
    RecommendationType,
)

_LEARNED = {
    "generated_at": "2026-09-11T02:00:00",
    "title": {"optimal_length_min": 55, "optimal_length_max": 65},
    "tags": {"recommended": ["gravel"]},
    "thumbnails": {"style_performance": {"bold": 1.0, "minimal": 0.2}},
}


def _make_applier(tmp_path, **flags):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    return RecommendationAutoApplier(
        config_dir=tmp_path / "config",
        state_dir=tmp_path / "state",
        config=AutoApplierConfig(**flags),
    )


class TestNoFieldCanCarryAnInventedConfidence:
    """Robust by construction: the value has nowhere to live."""

    def test_the_audit_record_has_no_confidence_field(self):
        names = {f.name for f in fields(AppliedRecommendation)}

        assert "confidence" not in names
        # The field that does answer "what justified this?" survives.
        assert "evidence" in names

    def test_apply_config_change_takes_no_confidence_argument(self):
        params = signature(RecommendationAutoApplier._apply_config_change).parameters

        assert "confidence" not in params

    def test_no_confidence_literal_is_left_in_the_learned_config_path(self):
        # Read the compiled body, not the source: the docstring still
        # describes the deleted numbers on purpose, and a prose mention is
        # not a literal anybody applies.
        code = RecommendationAutoApplier.apply_learned_config.__code__

        floats = set()
        names = set(code.co_names) | set(code.co_varnames)
        pending = [code]
        while pending:
            current = pending.pop()
            for const in current.co_consts:
                if isinstance(const, float):
                    floats.add(const)
                elif hasattr(const, "co_consts"):
                    pending.append(const)
                    names |= set(const.co_names) | set(const.co_varnames)

        assert floats == set()
        assert "confidence" not in names


class TestTheNightlyDecisionIsFlagsOnly:
    """Behavioural pin: flags decide, not a number.

    Deleting the literals is safe precisely because `_apply_config_change`
    never compared them to anything. These tests fail if anyone reintroduces
    a confidence gate on this path and quietly changes which sections a
    nightly run applies.
    """

    def test_all_three_sections_apply_under_default_flags(self, tmp_path):
        applier = _make_applier(tmp_path)

        applied = applier.apply_learned_config(_LEARNED)

        assert {rec.recommendation_type for rec in applied} == {
            RecommendationType.TITLE_LENGTH,
            RecommendationType.TAG_RECOMMENDATION,
            RecommendationType.THUMBNAIL_STYLE,
        }
        assert applier._get_config_value("seo.title.optimal_length") == {
            "min": 55,
            "max": 65,
        }
        assert applier._get_config_value("seo.tags.recommended") == ["gravel"]
        assert applier._get_config_value("thumbnails.default_style") == "bold"

    def test_an_impossible_confidence_threshold_changes_nothing(self, tmp_path):
        # The gate `process_recommendations` uses does not reach this path.
        # Raising it above any achievable value must not silence a section.
        strict = _make_applier(
            tmp_path / "strict",
            min_confidence_for_auto=1.01,
            min_confidence_for_suggest=1.01,
        )
        relaxed = _make_applier(tmp_path / "relaxed", min_confidence_for_auto=0.0)

        strict_types = {
            r.recommendation_type for r in strict.apply_learned_config(_LEARNED)
        }
        relaxed_types = {
            r.recommendation_type for r in relaxed.apply_learned_config(_LEARNED)
        }

        assert strict_types == relaxed_types
        assert len(strict_types) == 3

    def test_a_section_is_refused_by_its_flag_and_says_which(self, tmp_path, caplog):
        applier = _make_applier(
            tmp_path, allow_tag_changes=False, allow_thumbnail_style=False
        )

        with caplog.at_level(logging.INFO):
            applied = applier.apply_learned_config(_LEARNED)

        assert {rec.recommendation_type for rec in applied} == {
            RecommendationType.TITLE_LENGTH
        }
        assert applier._get_config_value("seo.tags.recommended") is None
        assert applier._get_config_value("thumbnails.default_style") is None
        assert "not applying learned 'tags': allow_tag_changes is off" in caplog.text
        assert (
            "not applying learned 'thumbnails': allow_thumbnail_style is off"
            in caplog.text
        )


class TestTheCallerSuppliedGateIsUntouched:
    """`process_recommendations` still filters on the number its caller sent."""

    def test_a_low_confidence_recommendation_is_still_rejected(self, tmp_path):
        applier = _make_applier(tmp_path)

        results = applier.process_recommendations(
            [{"category": "title", "suggestion": "Use 50-70 chars", "confidence": 0.1}]
        )

        assert results["applied"] == []
        assert [e["reason"] for e in results["rejected"]] == ["low_confidence"]

    def test_a_high_confidence_recommendation_still_applies(self, tmp_path):
        applier = _make_applier(tmp_path)

        results = applier.process_recommendations(
            [{"category": "title", "suggestion": "Use 50-70 chars", "confidence": 0.95}]
        )

        assert len(results["applied"]) == 1
        assert not hasattr(results["applied"][0], "confidence")

    def test_a_recommendation_with_no_confidence_is_not_defaulted_into_applying(
        self, tmp_path
    ):
        # `rec.get("confidence", 0)` — an absent number is zero, not 0.7.
        applier = _make_applier(tmp_path)

        results = applier.process_recommendations(
            [{"category": "title", "suggestion": "Use 50-70 chars"}]
        )

        assert results["applied"] == []
        assert [e["reason"] for e in results["rejected"]] == ["low_confidence"]


class TestAnOlderAuditTrailStillLoads:
    """The deletion must not throw away history written before it."""

    def _write_state(self, tmp_path, record):
        state_dir = tmp_path / "state"
        (state_dir / "backups").mkdir(parents=True)
        (state_dir / "auto_applier_state.json").write_text(
            json.dumps({"applied_recommendations": [record]})
        )

    def test_a_record_carrying_confidence_loads_with_the_key_dropped(
        self, tmp_path, caplog
    ):
        self._write_state(
            tmp_path,
            {
                "id": "rec_20260101_000000_titl",
                "recommendation_type": "title_length",
                "applied_at": "2026-01-01T00:00:00",
                "status": "applied",
                "target_config": "seo.title.optimal_length",
                "old_value": None,
                "new_value": {"min": 50, "max": 70},
                "confidence": 0.8,
                "source": "pattern_analysis",
                "expected_impact": "medium",
                "applied_by": "auto_applier",
                "rollback_available": True,
                "rollback_at": None,
                "notes": None,
            },
        )

        with caplog.at_level(logging.INFO):
            applier = _make_applier(tmp_path)

        # The history survives — it is not discarded to preserve a field
        # nobody read (loading old records must not raise a TypeError).
        assert len(applier.applied_recommendations) == 1
        loaded = applier.applied_recommendations[0]
        assert loaded.id == "rec_20260101_000000_titl"
        assert loaded.new_value == {"min": 50, "max": 70}
        assert not hasattr(loaded, "confidence")
        assert "dropping unknown audit fields" in caplog.text
        assert "confidence" in caplog.text

    def test_a_dropped_confidence_record_still_rolls_back(self, tmp_path):
        (tmp_path / "config").mkdir(exist_ok=True)
        self._write_state(
            tmp_path,
            {
                "id": "rec_20260101_000000_thum",
                "recommendation_type": "thumbnail_style",
                "applied_at": "2026-01-01T00:00:00",
                "status": "applied",
                "target_config": "thumbnails.default_style",
                "old_value": "minimal",
                "new_value": "bold",
                "confidence": 0.7,
                "source": "pattern_analysis",
            },
        )
        applier = _make_applier(tmp_path)
        applier._apply_config_value("thumbnails.default_style", "bold")

        assert applier.rollback_recommendation("rec_20260101_000000_thum") is True
        assert applier._get_config_value("thumbnails.default_style") == "minimal"
