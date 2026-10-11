"""
Unit tests for RecommendationAutoApplier dispatch and accounting.

Tests cover:
- Handler registry gating (a config flag alone never green-lights a type)
- No recommendation vanishes from the results accounting
- The five supported types apply, refuse and roll back
"""

import json
import logging
from dataclasses import fields

import pytest

from seo_manager.analytics.recommendation_auto_applier import (
    AppliedRecommendation,
    AutoApplierConfig,
    RecommendationAutoApplier,
    RecommendationType,
)


def _reasons(entries):
    """Collect the reason strings from a results bucket."""
    return [e.get("reason") for e in entries if isinstance(e, dict)]


def _total(results):
    return (
        len(results["applied"]) + len(results["suggested"]) + len(results["rejected"])
    )


class TestHandlerRegistry:
    """The registry is the single source of truth for 'can this be applied'."""

    @pytest.fixture
    def applier(self, tmp_path):
        config = AutoApplierConfig(allow_posting_time=True)
        return RecommendationAutoApplier(
            config_dir=tmp_path / "config",
            state_dir=tmp_path / "state",
            config=config,
        )

    def test_registry_covers_exactly_the_implemented_handlers(self, applier):
        assert set(applier._handlers) == {
            RecommendationType.TITLE_LENGTH,
            RecommendationType.TAG_RECOMMENDATION,
            RecommendationType.THUMBNAIL_STYLE,
            RecommendationType.DESCRIPTION_TEMPLATE,
            RecommendationType.POSTING_TIME,
        }

    def test_flag_alone_cannot_green_light_a_type_with_no_handler(self, applier):
        # KEYWORD_PRIORITY has neither a handler nor a config flag; even if a
        # flag were added, the registry check would still refuse it.
        assert applier._can_auto_apply(RecommendationType.KEYWORD_PRIORITY) is False

    def test_supported_type_still_needs_its_flag(self, tmp_path):
        applier = RecommendationAutoApplier(
            config_dir=tmp_path / "config",
            state_dir=tmp_path / "state",
            config=AutoApplierConfig(allow_tag_changes=False),
        )
        assert applier._can_auto_apply(RecommendationType.TAG_RECOMMENDATION) is False
        assert applier._can_auto_apply(RecommendationType.TITLE_LENGTH) is True


class TestNoRecommendationVanishes:
    """Every recognised recommendation lands in exactly one bucket."""

    @pytest.fixture
    def applier(self, tmp_path):
        (tmp_path / "config").mkdir()
        return RecommendationAutoApplier(
            config_dir=tmp_path / "config",
            state_dir=tmp_path / "state",
            config=AutoApplierConfig(allow_posting_time=True),
        )

    def test_type_with_no_handler_is_rejected_not_dropped(self, applier):
        # Drift guard: _determine_rec_type and _handlers are two registries,
        # and a category that resolves to a type with no handler must be
        # reported, not dropped.
        del applier._handlers[RecommendationType.POSTING_TIME]
        rec = {
            "category": "posting",
            "posting_time": "09:00",
            "confidence": 0.95,
        }

        results = applier.process_recommendations([rec])

        assert results["applied"] == []
        assert _total(results) == 1
        assert _reasons(results["rejected"]) == ["unsupported_type"]
        assert results["rejected"][0]["recommendation"] is rec

    def test_handler_returning_none_is_rejected_as_apply_failed(self, applier):
        # No "50-70"-style range in the suggestion, so the title handler
        # returns None rather than applying anything.
        rec = {
            "category": "title",
            "suggestion": "Make titles punchier",
            "confidence": 0.95,
        }

        results = applier.process_recommendations([rec])

        assert results["applied"] == []
        assert _total(results) == 1
        assert _reasons(results["rejected"]) == ["apply_failed"]

    def test_accounting_invariant_holds_for_a_mixed_batch(self, applier):
        recs = [
            {"category": "title", "suggestion": "Use 50-70 chars", "confidence": 0.95},
            {
                "category": "tags",
                "suggestion": "Consider adding: hiking, gravel",
                "confidence": 0.95,
            },
            {
                "category": "thumbnail",
                "suggestion": "Try a bold style",
                "confidence": 0.95,
            },
            {"category": "posting", "posting_time": "09:00", "confidence": 0.95},
            {"category": "title", "suggestion": "No range here", "confidence": 0.95},
            {"category": "tags", "suggestion": "Vague advice", "confidence": 0.65},
            {"category": "title", "suggestion": "Use 50-70 chars", "confidence": 0.10},
        ]

        results = applier.process_recommendations(recs)

        assert _total(results) == len(recs)
        assert "max_changes_reached" in _reasons(results["suggested"])
        assert "low_confidence" in _reasons(results["rejected"])


class TestSupportedTypesStillApply:
    """Regression: the original three handlers behave as before."""

    @pytest.fixture
    def applier(self, tmp_path):
        (tmp_path / "config").mkdir()
        return RecommendationAutoApplier(
            config_dir=tmp_path / "config",
            state_dir=tmp_path / "state",
            config=AutoApplierConfig(),
        )

    def test_title_tags_and_thumbnail_all_apply(self, applier):
        recs = [
            {"category": "title", "suggestion": "Use 50-70 chars", "confidence": 0.95},
            {
                "category": "tags",
                "suggestion": "Consider adding: hiking, gravel",
                "confidence": 0.95,
            },
            {
                "category": "thumbnail",
                "suggestion": "Try a bold style",
                "confidence": 0.95,
            },
        ]

        results = applier.process_recommendations(recs)

        assert len(results["applied"]) == 3
        assert _total(results) == 3

        applied_types = {r.recommendation_type for r in results["applied"]}
        assert applied_types == {
            RecommendationType.TITLE_LENGTH,
            RecommendationType.TAG_RECOMMENDATION,
            RecommendationType.THUMBNAIL_STYLE,
        }
        assert applier._get_config_value("seo.title.optimal_length") == {
            "min": 50,
            "max": 70,
        }
        assert applier._get_config_value("thumbnails.default_style") == "bold"

    def test_disallowed_supported_type_is_rejected_as_type_not_allowed(self, tmp_path):
        (tmp_path / "config").mkdir()
        applier = RecommendationAutoApplier(
            config_dir=tmp_path / "config",
            state_dir=tmp_path / "state",
            config=AutoApplierConfig(allow_tag_changes=False),
        )

        results = applier.process_recommendations(
            [
                {
                    "category": "tags",
                    "suggestion": "Consider adding: hiking",
                    "confidence": 0.95,
                }
            ]
        )

        assert _reasons(results["rejected"]) == ["type_not_allowed"]


_LEARNED = {
    "title": {"optimal_length_min": 55, "optimal_length_max": 65},
    "tags": {"recommended": ["gravel"]},
    "thumbnails": {"style_performance": {"bold": 1.0}},
}


def _make_applier(tmp_path, **flags):
    (tmp_path / "config").mkdir(exist_ok=True)
    return RecommendationAutoApplier(
        config_dir=tmp_path / "config",
        state_dir=tmp_path / "state",
        config=AutoApplierConfig(**flags),
    )


class TestDescriptionTemplateHandler:
    """DESCRIPTION_TEMPLATE applies, refuses and rolls back like its siblings."""

    KEY = "descriptions.default_template"
    TEMPLATE = "{title}\n\nFilmed on the {route} route.\n\n#gravel"

    def _rec(self, **over):
        rec = {
            "category": "description",
            "template": self.TEMPLATE,
            "confidence": 0.95,
        }
        rec.update(over)
        return rec

    def test_applied_when_flag_enabled(self, tmp_path):
        applier = _make_applier(tmp_path, allow_description_template=True)

        results = applier.process_recommendations([self._rec()])

        assert len(results["applied"]) == 1
        applied = results["applied"][0]
        assert applied.recommendation_type == RecommendationType.DESCRIPTION_TEMPLATE
        assert applied.target_config == self.KEY
        assert applier._get_config_value(self.KEY) == self.TEMPLATE

    def test_not_permitted_when_flag_disabled(self, tmp_path):
        applier = _make_applier(tmp_path)  # default: False

        results = applier.process_recommendations([self._rec()])

        assert results["applied"] == []
        assert _reasons(results["rejected"]) == ["type_not_allowed"]
        assert applier._get_config_value(self.KEY) is None

    def test_missing_template_is_refused_not_defaulted(self, tmp_path):
        applier = _make_applier(tmp_path, allow_description_template=True)

        results = applier.process_recommendations([self._rec(template=None)])

        assert results["applied"] == []
        assert _reasons(results["rejected"]) == ["apply_failed"]
        assert applier._get_config_value(self.KEY) is None

    def test_blank_template_is_refused(self, tmp_path):
        applier = _make_applier(tmp_path, allow_description_template=True)

        results = applier.process_recommendations([self._rec(template="   ")])

        assert _reasons(results["rejected"]) == ["apply_failed"]
        assert applier._get_config_value(self.KEY) is None

    def test_rollback_restores_the_previous_template(self, tmp_path):
        applier = _make_applier(tmp_path, allow_description_template=True)
        applier._apply_config_value(self.KEY, "old template")

        applied = applier.process_recommendations([self._rec()])["applied"][0]
        assert applier._get_config_value(self.KEY) == self.TEMPLATE

        assert applier.rollback_recommendation(applied.id) is True
        assert applier._get_config_value(self.KEY) == "old template"


class TestPostingTimeHandler:
    """POSTING_TIME: the case allow_posting_time silently did nothing for."""

    KEY = "posting.default_time"

    def _rec(self, **over):
        rec = {"category": "posting", "posting_time": "09:30", "confidence": 0.95}
        rec.update(over)
        return rec

    def test_applied_when_allow_posting_time_enabled(self, tmp_path):
        applier = _make_applier(tmp_path, allow_posting_time=True)

        results = applier.process_recommendations([self._rec()])

        assert len(results["applied"]) == 1
        applied = results["applied"][0]
        assert applied.recommendation_type == RecommendationType.POSTING_TIME
        assert applied.target_config == self.KEY
        assert applier._get_config_value(self.KEY) == "09:30"

    def test_not_permitted_when_flag_disabled(self, tmp_path):
        applier = _make_applier(tmp_path)  # allow_posting_time defaults False

        results = applier.process_recommendations([self._rec()])

        assert results["applied"] == []
        assert _reasons(results["rejected"]) == ["type_not_allowed"]
        assert applier._get_config_value(self.KEY) is None

    @pytest.mark.parametrize(
        "bad", [None, "", "9am", "25:00", "09:60", "09:30 on Tuesdays", 930]
    )
    def test_malformed_posting_time_is_refused_not_defaulted(self, tmp_path, bad):
        applier = _make_applier(tmp_path, allow_posting_time=True)

        results = applier.process_recommendations([self._rec(posting_time=bad)])

        assert results["applied"] == []
        assert _reasons(results["rejected"]) == ["apply_failed"]
        assert applier._get_config_value(self.KEY) is None

    def test_rollback_restores_the_previous_time(self, tmp_path):
        applier = _make_applier(tmp_path, allow_posting_time=True)
        applier._apply_config_value(self.KEY, "17:00")

        applied = applier.process_recommendations([self._rec()])["applied"][0]
        assert applier._get_config_value(self.KEY) == "09:30"

        assert applier.rollback_recommendation(applied.id) is True
        assert applier._get_config_value(self.KEY) == "17:00"


class TestLearnedConfigGatesItself:
    """`apply_learned_config` is a second entry point, and the flags gate it too.

    It reaches `_apply_config_change` directly, so nothing but the method's own
    check keeps a scheduled apply inside the gates an operator set. The daemon
    used to filter the sections before handing them over; that protected the
    nightly cron and no other caller.
    """

    def test_a_section_whose_flag_is_off_is_not_applied(self, tmp_path):
        applier = _make_applier(tmp_path, allow_tag_changes=False)

        applied = applier.apply_learned_config(_LEARNED)

        types = {rec.recommendation_type for rec in applied}
        assert RecommendationType.TAG_RECOMMENDATION not in types
        assert applier._get_config_value("seo.tags.recommended") is None
        # The permitted sections still go through.
        assert RecommendationType.TITLE_LENGTH in types
        assert RecommendationType.THUMBNAIL_STYLE in types

    def test_every_section_off_applies_nothing(self, tmp_path):
        applier = _make_applier(
            tmp_path,
            allow_title_length=False,
            allow_tag_changes=False,
            allow_thumbnail_style=False,
        )

        assert applier.apply_learned_config(_LEARNED) == []
        assert applier._get_config_value("seo.title.optimal_length") is None
        assert applier._get_config_value("thumbnails.default_style") is None

    def test_a_skipped_section_is_logged_for_the_operator(self, tmp_path, caplog):
        applier = _make_applier(tmp_path, allow_thumbnail_style=False)

        with caplog.at_level(logging.INFO):
            applier.apply_learned_config(_LEARNED)

        assert "not applying learned 'thumbnails': allow_thumbnail_style is off" in (
            caplog.text
        )


class TestAppliedRecommendationCitesItsEvidence:
    """The audit record answers 'what justified this?' — or admits it cannot.

    `expected_impact` used to answer it with the literal "medium" on every
    record ever written, and nothing read the field back. A field that states
    a measurement nobody took is worse than no field.
    """

    def test_the_record_carries_evidence_and_no_expected_impact(self):
        names = {f.name for f in fields(AppliedRecommendation)}

        assert "expected_impact" not in names
        assert "evidence" in names

    def test_learned_thumbnail_change_cites_the_style_data_it_chose_from(
        self, tmp_path
    ):
        applier = _make_applier(tmp_path)
        learned = {
            "generated_at": "2026-09-10T02:00:00",
            "thumbnails": {"style_performance": {"bold": 0.9, "minimal": 0.2}},
        }

        applied = applier.apply_learned_config(learned)

        assert len(applied) == 1
        assert applied[0].new_value == "bold"
        evidence = applied[0].evidence
        assert evidence is not None
        assert evidence["section"] == "thumbnails"
        assert evidence["data"]["style_performance"] == {"bold": 0.9, "minimal": 0.2}
        assert evidence["config_generated_at"] == "2026-09-10T02:00:00"

    def test_two_changes_from_different_data_are_distinguishable(self, tmp_path):
        applier = _make_applier(tmp_path)

        first = applier.apply_learned_config(
            {
                "generated_at": "2026-08-01T02:00:00",
                "title": {"optimal_length_min": 50, "optimal_length_max": 70},
            }
        )[0]
        second = applier.apply_learned_config(
            {
                "generated_at": "2026-09-01T02:00:00",
                "title": {"optimal_length_min": 55, "optimal_length_max": 65},
            }
        )[0]

        # `source` is identical — it names the module, not the data.
        assert first.source == second.source
        assert first.evidence != second.evidence
        assert first.evidence["config_generated_at"] == "2026-08-01T02:00:00"
        assert second.evidence["data"]["optimal_length_max"] == 65

    def test_a_handler_cites_the_observation_it_was_computed_from(self, tmp_path):
        applier = _make_applier(tmp_path)

        applied = applier.process_recommendations(
            [
                {
                    "category": "title",
                    "issue": "Title too short (28 chars)",
                    "suggestion": "Expand title to 50-70 chars for better CTR",
                    "confidence": 0.9,
                }
            ]
        )["applied"][0]

        assert applied.evidence is not None
        assert applied.evidence["issue"] == "Title too short (28 chars)"
        assert "50-70" in applied.evidence["suggestion"]

    def test_a_change_with_nothing_to_cite_records_no_evidence(self, tmp_path):
        applier = _make_applier(tmp_path, allow_posting_time=True)

        applied = applier.process_recommendations(
            [{"category": "posting", "posting_time": "09:30", "confidence": 0.95}]
        )["applied"][0]

        # No placeholder, no plausible-looking blob: nobody recorded what
        # justified this, and the record says so.
        assert applied.evidence is None

    def test_evidence_survives_the_state_round_trip(self, tmp_path):
        applier = _make_applier(tmp_path)
        applier.apply_learned_config(
            {
                "generated_at": "2026-09-10T02:00:00",
                "thumbnails": {"style_performance": {"bold": 0.9}},
            }
        )

        reloaded = _make_applier(tmp_path)

        assert len(reloaded.applied_recommendations) == 1
        assert reloaded.applied_recommendations[0].evidence == {
            "kind": "learned_config",
            "section": "thumbnails",
            "data": {"style_performance": {"bold": 0.9}},
            "config_generated_at": "2026-09-10T02:00:00",
        }

    def test_a_record_written_before_this_change_still_loads(self, tmp_path):
        """An audit trail on disk predates both the deletion and the field."""
        state_dir = tmp_path / "state"
        (state_dir / "backups").mkdir(parents=True)
        (state_dir / "auto_applier_state.json").write_text(
            json.dumps(
                {
                    "applied_recommendations": [
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
                        }
                    ]
                }
            )
        )

        applier = _make_applier(tmp_path)

        assert len(applier.applied_recommendations) == 1
        assert applier.applied_recommendations[0].id == "rec_20260101_000000_titl"
        assert applier.applied_recommendations[0].evidence is None
