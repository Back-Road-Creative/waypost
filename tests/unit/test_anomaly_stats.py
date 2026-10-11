"""
A dip or a spike is a rank against real references, not a guess.

`anomaly_stats` is the qualification kernel for post-level anomaly work: a
robust deviation (median/MAD), an empirical tail-rank p-value, and a
policy-feasibility check. Nothing calls it yet, and it carries no operating
thresholds — every number it refuses to invent is refused by *type*, not by a
comment asking the next caller to be careful.

The three refusals it can return are the point of the module:

* `NO_BASELINE_VARIATION` — the reference has zero spread, so a deviation has
  no unit. There is no epsilon and no weaker fallback method; the returned
  value has nowhere to put a deviation number.
* `INSUFFICIENT_RESOLUTION` — with `n` references the smallest two-sided p the
  rank method can ever produce is `2 / (n + 1)`. If that cannot meet the
  caller's allocated alpha, no amount of data in hand answers the question,
  and a small-looking p would be an artifact of the method's floor.
* `POLICY_INFEASIBLE` — the error budget, the count of registered tests and
  the reference cap contradict each other before any outcome is inspected.

Every expected number below is hand-computed in a comment beside it.
"""

import math

import pytest

from seo_manager.analytics.anomaly_stats import (
    IDENTITY,
    INSUFFICIENT_RESOLUTION,
    LOG1P,
    NO_BASELINE_VARIATION,
    POLICY_INFEASIBLE,
    Feasible,
    Refusal,
    RobustDeviation,
    TailRank,
    feasibility,
    min_references_for,
    robust_deviation,
    tail_rank_p,
)

# 1.4826 makes the MAD a consistent estimator of sigma for normal data. It is
# the only constant in the module and it is a property of the estimator, not a
# tunable threshold.
SCALE = 1.4826


class TestRobustDeviationIsHandCheckable:
    def test_a_known_small_reference(self):
        # [1, 2, 3, 4, 100] -> m = 3; |x - 3| = [2, 1, 0, 1, 97] -> MAD = 1;
        # s = 1.4826 * 1 = 1.4826; deviation = (100 - 3) / 1.4826 = 65.4256...
        result = robust_deviation([1, 2, 3, 4, 100], 100, transform=IDENTITY)

        assert isinstance(result, RobustDeviation)
        assert result.median == 3
        assert result.scaled_mad == pytest.approx(1.4826)
        assert result.deviation == pytest.approx(97 / 1.4826)
        # relative_effect = (100 - 3) / 3 = 32.333...
        assert result.relative_effect == pytest.approx(97 / 3)
        assert result.n == 5
        assert result.transform == IDENTITY

    def test_counts_use_log1p_and_rates_use_identity(self):
        counts = [0, 1, 3, 7, 15]
        # log1p -> [0, ln2, ln4, ln8, ln16]; m = ln4; |x - ln4| =
        # [ln4, ln2, 0, ln2, ln4] -> MAD = ln2; s = 1.4826 * ln2.
        # target 63 -> ln64; deviation = (ln64 - ln4) / (1.4826 * ln2)
        #            = 4*ln2 / (1.4826*ln2) = 4 / 1.4826 = 2.69796...
        logged = robust_deviation(counts, 63, transform=LOG1P)

        assert isinstance(logged, RobustDeviation)
        assert logged.median == pytest.approx(math.log(4))
        assert logged.scaled_mad == pytest.approx(SCALE * math.log(2))
        assert logged.deviation == pytest.approx(4 / 1.4826)
        assert logged.transform == LOG1P
        # relative_effect is always raw: (63 - 3) / 3 = 20.0
        assert logged.relative_effect == pytest.approx(20.0)

        # Same data read as a rate: m = 3; |x - 3| = [3, 2, 0, 4, 12] ->
        # MAD = 3; s = 4.4478; deviation = (63 - 3) / 4.4478 = 13.4898...
        plain = robust_deviation(counts, 63, transform=IDENTITY)

        assert isinstance(plain, RobustDeviation)
        assert plain.scaled_mad == pytest.approx(3 * 1.4826)
        assert plain.deviation == pytest.approx(60 / (3 * 1.4826))
        # The transform is not cosmetic: the two disagree by ~5x.
        assert plain.deviation != pytest.approx(logged.deviation)

    def test_input_order_cannot_change_the_answer(self):
        forward = robust_deviation([1, 2, 3, 4, 100], 100, transform=IDENTITY)
        shuffled = robust_deviation([100, 3, 1, 4, 2], 100, transform=IDENTITY)

        assert forward == shuffled


class TestZeroSpreadRefusesInsteadOfDividing:
    def test_all_references_equal_refuses(self):
        # MAD of [5, 5, 5, 5] is 0, so s = 0 and the deviation has no unit.
        result = robust_deviation([5, 5, 5, 5], 9, transform=IDENTITY)

        assert isinstance(result, Refusal)
        assert result.reason == NO_BASELINE_VARIATION

    def test_the_refusal_carries_no_deviation_number(self):
        # Structural: there is no epsilon branch because the returned type has
        # nowhere to put the number an epsilon would have produced.
        result = robust_deviation([5, 5, 5, 5], 9, transform=IDENTITY)

        assert not hasattr(result, "deviation")
        assert not hasattr(result, "scaled_mad")
        assert not hasattr(result, "relative_effect")


class TestRelativeEffectIsUnavailableNotZero:
    def test_a_zero_raw_median_yields_none(self):
        # [-2, -1, 0, 1, 2] -> raw median 0; |x - 0| = [2, 1, 0, 1, 2] ->
        # MAD = 1; s = 1.4826. The deviation is fine; the ratio is not,
        # because its denominator is 0.
        result = robust_deviation([-2, -1, 0, 1, 2], 3, transform=IDENTITY)

        assert isinstance(result, RobustDeviation)
        assert result.deviation == pytest.approx(3 / 1.4826)
        assert result.relative_effect is None
        assert result.relative_effect != 0


class TestTailRankCountsTiesIntoBothTails:
    def test_a_target_above_every_reference(self):
        reference = [1, 2, 3, 4, 5, 6, 7, 8, 9]  # n = 9
        # count(ref >= 10) = 0 -> p_upper = (1 + 0) / 10 = 0.1
        # count(ref <= 10) = 9 -> p_lower = (1 + 9) / 10 = 1.0
        # p_two = min(1, 2 * 0.1) = 0.2; floor = 2 / 10 = 0.2
        result = tail_rank_p(reference, 10, allocated_alpha=0.25)

        assert isinstance(result, TailRank)
        assert result.p_upper == pytest.approx(0.1)
        assert result.p_lower == pytest.approx(1.0)
        assert result.p_two_sided == pytest.approx(0.2)
        assert result.n == 9
        assert result.min_attainable_p == pytest.approx(0.2)
        assert result.ties == 0

    def test_ties_count_toward_both_tails(self):
        reference = [1, 2, 2, 2, 3]  # n = 5, three references tie the target
        # count(ref >= 2) = 4 -> p_upper = 5 / 6
        # count(ref <= 2) = 4 -> p_lower = 5 / 6
        # p_two = min(1, 2 * 5/6) = min(1, 1.666...) = 1.0
        result = tail_rank_p(reference, 2, allocated_alpha=0.5)

        assert isinstance(result, TailRank)
        assert result.p_upper == pytest.approx(5 / 6)
        assert result.p_lower == pytest.approx(5 / 6)
        assert result.p_two_sided == 1.0
        assert result.ties == 3

    def test_input_order_cannot_change_the_answer(self):
        forward = tail_rank_p([1, 2, 2, 2, 3], 2, allocated_alpha=0.5)
        shuffled = tail_rank_p([3, 2, 1, 2, 2], 2, allocated_alpha=0.5)

        assert forward == shuffled


class TestFiniteReferencesLimitResolution:
    def test_a_floor_above_the_allocated_alpha_refuses(self):
        # n = 9 -> the smallest two-sided p attainable is 2 / 10 = 0.2, which
        # cannot meet 0.05 however extreme the target is.
        result = tail_rank_p([1, 2, 3, 4, 5, 6, 7, 8, 9], 99, allocated_alpha=0.05)

        assert isinstance(result, Refusal)
        assert result.reason == INSUFFICIENT_RESOLUTION
        assert result.allocated_alpha == pytest.approx(0.05)
        # ceil(2 / 0.05) - 1 = 40 - 1 = 39 references would be needed.
        assert result.required_references == 39

    def test_exactly_enough_references_is_not_a_refusal(self):
        # n = 39 -> floor = 2 / 40 = 0.05, which meets alpha 0.05 exactly.
        reference = list(range(39))
        result = tail_rank_p(reference, 1000, allocated_alpha=0.05)

        assert isinstance(result, TailRank)
        assert result.min_attainable_p == pytest.approx(0.05)
        assert result.p_two_sided == pytest.approx(0.05)


class TestPolicyFeasibility:
    def test_min_references_for_matches_the_ceiling_formula(self):
        assert min_references_for(0.005) == 399  # ceil(2 / 0.005) - 1 = 399
        assert min_references_for(0.05) == 39  # ceil(2 / 0.05)  - 1 = 39
        assert min_references_for(0.5) == 3  # ceil(2 / 0.5)   - 1 = 3

    def test_ten_tests_at_total_alpha_is_infeasible(self):
        # Ten equally budgeted tests at total alpha 0.05 -> 0.005 each ->
        # 399 references each, against a 200-reference cap.
        result = feasibility(0.05, 10, 200)

        assert isinstance(result, Refusal)
        assert result.reason == POLICY_INFEASIBLE
        assert result.allocated_alpha == pytest.approx(0.005)
        assert result.required_references == 399
        # The caller can print WHY without re-deriving anything.
        assert "399" in result.detail

    def test_a_single_registered_test_fits_the_same_cap(self):
        # One test at total alpha 0.05 -> 0.05 allocated -> 39 references.
        result = feasibility(0.05, 1, 200)

        assert isinstance(result, Feasible)
        assert result.allocated_alpha == pytest.approx(0.05)
        assert result.required_references == 39
        assert result.registered_tests == 1
        assert result.reference_cap == 200


class TestCallerBugsRaiseRatherThanRefuse:
    """A malformed input is not a statistical refusal — it is a bug upstream."""

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_reference_values(self, bad):
        with pytest.raises(ValueError):
            robust_deviation([1, 2, bad], 3, transform=IDENTITY)

    @pytest.mark.parametrize("bad", [float("nan"), float("inf")])
    def test_non_finite_target(self, bad):
        with pytest.raises(ValueError):
            robust_deviation([1, 2, 3], bad, transform=IDENTITY)
        with pytest.raises(ValueError):
            tail_rank_p([1, 2, 3], bad, allocated_alpha=0.5)

    def test_an_empty_reference(self):
        with pytest.raises(ValueError):
            robust_deviation([], 3, transform=IDENTITY)
        with pytest.raises(ValueError):
            tail_rank_p([], 3, allocated_alpha=0.5)

    def test_log1p_on_a_value_outside_its_domain(self):
        with pytest.raises(ValueError):
            robust_deviation([-1, 0, 1], 2, transform=LOG1P)

    def test_an_unknown_transform(self):
        with pytest.raises(ValueError):
            robust_deviation([1, 2, 3], 4, transform="sqrt")

    @pytest.mark.parametrize("bad_alpha", [0, -0.1, 1.5, float("nan")])
    def test_an_alpha_outside_zero_to_one(self, bad_alpha):
        with pytest.raises(ValueError):
            min_references_for(bad_alpha)

    def test_a_policy_with_no_registered_tests(self):
        with pytest.raises(ValueError):
            feasibility(0.05, 0, 200)
