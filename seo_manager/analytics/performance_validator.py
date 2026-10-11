"""
Performance Validator - Statistical Significance Testing.

Validates that SEO improvements are statistically significant:
1. Chi-squared tests for CTR differences
2. Confidence intervals for predictions
3. Before/after comparison with p-values
4. Trend analysis to detect real improvements vs noise

Prevents false positives from random variation.
"""

import json
import logging
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class SignificanceLevel(Enum):
    """Statistical significance levels."""

    NOT_SIGNIFICANT = "not_significant"  # p > 0.10
    WEAK = "weak"  # 0.05 < p <= 0.10
    MODERATE = "moderate"  # 0.01 < p <= 0.05
    STRONG = "strong"  # p <= 0.01


@dataclass
class ValidationResult:
    """Result of a statistical validation."""

    test_name: str
    hypothesis: str
    sample_size_a: int
    sample_size_b: int
    metric_a: float
    metric_b: float
    difference: float
    difference_percent: float
    p_value: float
    significance: SignificanceLevel
    confidence_interval_lower: float
    confidence_interval_upper: float
    is_improvement: bool
    recommendation: str


@dataclass
class TrendAnalysis:
    """Result of trend analysis over time."""

    metric_name: str
    period_days: int
    data_points: int
    trend_direction: str  # "improving", "declining", "stable"
    trend_strength: float  # 0.0-1.0
    average_value: float
    min_value: float
    max_value: float
    volatility: float  # Standard deviation / mean
    confidence: float


class PerformanceValidator:
    """
    Validates SEO performance improvements with statistical rigor.

    Prevents false positives by requiring:
    - Minimum sample sizes for reliable comparison
    - Statistical significance testing
    - Trend analysis to distinguish signal from noise

    Example:
        validator = PerformanceValidator()

        # Compare two strategies
        result = validator.compare_strategies(
            strategy_a_results=[...],
            strategy_b_results=[...],
            metric='ctr'
        )

        # Check if improvement is significant
        if result.is_improvement and result.significance >= SignificanceLevel.MODERATE:
            print("Improvement is statistically significant!")

        # Validate a recommendation
        is_valid = validator.validate_recommendation(
            before_metrics={'ctr': 0.05, 'views': 1000},
            after_metrics={'ctr': 0.06, 'views': 1500}
        )
    """

    # Statistical constants
    MIN_SAMPLE_SIZE = 30  # Minimum for reliable statistics
    Z_SCORES = {  # noqa: RUF012
        0.90: 1.645,  # 90% confidence
        0.95: 1.96,  # 95% confidence
        0.99: 2.576,  # 99% confidence
    }

    def __init__(
        self,
        min_sample_size: int = MIN_SAMPLE_SIZE,
        default_confidence: float = 0.95,
        state_dir: Optional[Path] = None,
    ):
        """
        Initialize the validator.

        Args:
            min_sample_size: Minimum samples for reliable statistics
            default_confidence: Default confidence level (0.90, 0.95, 0.99)
            state_dir: Directory for validation history
        """
        self.min_sample_size = min_sample_size
        self.default_confidence = default_confidence
        self.state_dir = state_dir or Path("./data/validation")

        self.state_dir.mkdir(parents=True, exist_ok=True)

        # Validation history
        self.validation_history: List[Dict] = []
        self._load_history()

    def compare_strategies(
        self,
        strategy_a_results: List[Dict],
        strategy_b_results: List[Dict],
        metric: str = "ctr",
        hypothesis: str = "Strategy B outperforms Strategy A",
    ) -> ValidationResult:
        """
        Compare two strategies using statistical testing.

        Args:
            strategy_a_results: List of {metric: value} dicts for strategy A
            strategy_b_results: List of {metric: value} dicts for strategy B
            metric: Which metric to compare (ctr, views, engagement)
            hypothesis: Description of what we're testing

        Returns:
            ValidationResult with statistical analysis
        """
        # Extract metric values
        values_a = [r.get(metric, 0) for r in strategy_a_results if metric in r]
        values_b = [r.get(metric, 0) for r in strategy_b_results if metric in r]

        # Check sample sizes
        n_a, n_b = len(values_a), len(values_b)

        if n_a < self.min_sample_size or n_b < self.min_sample_size:
            logger.warning(
                f"Sample sizes too small: A={n_a}, B={n_b} "
                f"(need {self.min_sample_size})"
            )

        # Calculate means
        mean_a = sum(values_a) / n_a if n_a > 0 else 0
        mean_b = sum(values_b) / n_b if n_b > 0 else 0

        # Calculate standard deviations
        std_a = self._calculate_std(values_a, mean_a)
        std_b = self._calculate_std(values_b, mean_b)

        # Two-sample t-test
        _t_stat, p_value = self._two_sample_t_test(
            mean_a, mean_b, std_a, std_b, n_a, n_b
        )

        # Calculate difference
        difference = mean_b - mean_a
        difference_percent = (difference / mean_a * 100) if mean_a > 0 else 0

        # Determine significance
        significance = self._get_significance_level(p_value)

        # Confidence interval for difference
        ci_lower, ci_upper = self._confidence_interval_diff(
            mean_a, mean_b, std_a, std_b, n_a, n_b
        )

        # Is it an improvement?
        is_improvement = difference > 0 and significance in [
            SignificanceLevel.MODERATE,
            SignificanceLevel.STRONG,
        ]

        # Generate recommendation
        recommendation = self._generate_recommendation(
            difference_percent, significance, is_improvement, metric
        )

        result = ValidationResult(
            test_name=f"{metric}_comparison",
            hypothesis=hypothesis,
            sample_size_a=n_a,
            sample_size_b=n_b,
            metric_a=mean_a,
            metric_b=mean_b,
            difference=difference,
            difference_percent=difference_percent,
            p_value=p_value,
            significance=significance,
            confidence_interval_lower=ci_lower,
            confidence_interval_upper=ci_upper,
            is_improvement=is_improvement,
            recommendation=recommendation,
        )

        # Log result
        self._log_validation(result)

        return result

    def compare_ctr(
        self,
        impressions_a: int,
        clicks_a: int,
        impressions_b: int,
        clicks_b: int,
        hypothesis: str = "B has higher CTR than A",
    ) -> ValidationResult:
        """
        Compare click-through rates using proportion test.

        More appropriate for CTR comparison than t-test.

        Args:
            impressions_a: Impressions for variant A
            clicks_a: Clicks for variant A
            impressions_b: Impressions for variant B
            clicks_b: Clicks for variant B
            hypothesis: What we're testing

        Returns:
            ValidationResult with CTR comparison
        """
        # Calculate CTRs
        ctr_a = clicks_a / impressions_a if impressions_a > 0 else 0
        ctr_b = clicks_b / impressions_b if impressions_b > 0 else 0

        # Two-proportion z-test
        p_pooled = (clicks_a + clicks_b) / (impressions_a + impressions_b)
        se = math.sqrt(
            p_pooled * (1 - p_pooled) * (1 / impressions_a + 1 / impressions_b)
        )

        if se > 0:
            z_stat = (ctr_b - ctr_a) / se
            # Two-tailed p-value (approximate using normal distribution)
            p_value = 2 * (1 - self._normal_cdf(abs(z_stat)))
        else:
            z_stat = 0
            p_value = 1.0

        # Calculate difference
        difference = ctr_b - ctr_a
        difference_percent = (difference / ctr_a * 100) if ctr_a > 0 else 0

        # Determine significance
        significance = self._get_significance_level(p_value)

        # Confidence interval
        z_critical = self.Z_SCORES[self.default_confidence]
        margin = z_critical * se
        ci_lower = difference - margin
        ci_upper = difference + margin

        # Is it an improvement?
        is_improvement = difference > 0 and significance in [
            SignificanceLevel.MODERATE,
            SignificanceLevel.STRONG,
        ]

        recommendation = self._generate_recommendation(
            difference_percent, significance, is_improvement, "CTR"
        )

        result = ValidationResult(
            test_name="ctr_proportion_test",
            hypothesis=hypothesis,
            sample_size_a=impressions_a,
            sample_size_b=impressions_b,
            metric_a=ctr_a,
            metric_b=ctr_b,
            difference=difference,
            difference_percent=difference_percent,
            p_value=p_value,
            significance=significance,
            confidence_interval_lower=ci_lower,
            confidence_interval_upper=ci_upper,
            is_improvement=is_improvement,
            recommendation=recommendation,
        )

        self._log_validation(result)
        return result

    def validate_before_after(
        self,
        before_metrics: Dict[str, float],
        after_metrics: Dict[str, float],
        before_sample_size: int,
        after_sample_size: int,
    ) -> Dict[str, ValidationResult]:
        """
        Validate improvements from before/after comparison.

        Args:
            before_metrics: Metrics before change {metric: value}
            after_metrics: Metrics after change {metric: value}
            before_sample_size: Sample size for before period
            after_sample_size: Sample size for after period

        Returns:
            Dict of metric -> ValidationResult
        """
        results = {}

        common_metrics = set(before_metrics.keys()) & set(after_metrics.keys())

        for metric in common_metrics:
            before_val = before_metrics[metric]
            after_val = after_metrics[metric]

            # Create mock result lists for comparison
            # (This is simplified - in production, use actual distributions)
            results[metric] = self.compare_strategies(
                strategy_a_results=[{metric: before_val}] * before_sample_size,
                strategy_b_results=[{metric: after_val}] * after_sample_size,
                metric=metric,
                hypothesis=f"{metric} improved after change",
            )

        return results

    def analyze_trend(
        self, time_series: List[Tuple[datetime, float]], metric_name: str = "metric"
    ) -> TrendAnalysis:
        """
        Analyze trend in time series data.

        Detects if metric is improving, declining, or stable.

        Args:
            time_series: List of (datetime, value) tuples
            metric_name: Name of the metric for reporting

        Returns:
            TrendAnalysis with trend direction and strength
        """
        if len(time_series) < 3:
            return TrendAnalysis(
                metric_name=metric_name,
                period_days=0,
                data_points=len(time_series),
                trend_direction="insufficient_data",
                trend_strength=0.0,
                average_value=0.0,
                min_value=0.0,
                max_value=0.0,
                volatility=0.0,
                confidence=0.0,
            )

        # Sort by date
        sorted_data = sorted(time_series, key=lambda x: x[0])
        values = [v for _, v in sorted_data]

        # Basic statistics
        avg = sum(values) / len(values)
        min_val = min(values)
        max_val = max(values)
        std_dev = self._calculate_std(values, avg)
        volatility = std_dev / avg if avg > 0 else 0

        # Calculate period
        period_days = (sorted_data[-1][0] - sorted_data[0][0]).days

        # Linear regression for trend
        slope, r_squared = self._linear_regression(values)

        # Determine trend direction and strength
        if r_squared < 0.3:
            trend_direction = "stable"
            trend_strength = 0.0
        elif slope > 0:
            trend_direction = "improving"
            trend_strength = min(1.0, r_squared * (1 + abs(slope / avg)))
        else:
            trend_direction = "declining"
            trend_strength = min(1.0, r_squared * (1 + abs(slope / avg)))

        # Confidence based on data quality
        confidence = min(1.0, (len(values) / 30) * r_squared)

        return TrendAnalysis(
            metric_name=metric_name,
            period_days=period_days,
            data_points=len(values),
            trend_direction=trend_direction,
            trend_strength=trend_strength,
            average_value=avg,
            min_value=min_val,
            max_value=max_val,
            volatility=volatility,
            confidence=confidence,
        )

    def calculate_required_sample_size(
        self,
        baseline_ctr: float,
        minimum_detectable_effect: float,
        significance_level: float = 0.05,
        power: float = 0.80,
    ) -> int:
        """
        Calculate required sample size for A/B test.

        Args:
            baseline_ctr: Current CTR (e.g., 0.05 for 5%)
            minimum_detectable_effect: Smallest improvement to detect (e.g., 0.10 for 10%)
            significance_level: Alpha level (default 0.05)
            power: Statistical power (default 0.80)

        Returns:
            Required sample size per variant
        """
        # Z-scores for alpha and beta
        z_alpha = self.Z_SCORES.get(1 - significance_level, 1.96)
        z_beta = 0.84  # for 80% power

        # Expected CTR for variant B
        ctr_b = baseline_ctr * (1 + minimum_detectable_effect)

        # Pooled variance estimate
        p_pooled = (baseline_ctr + ctr_b) / 2
        variance = 2 * p_pooled * (1 - p_pooled)

        # Required sample size formula
        effect_size = abs(ctr_b - baseline_ctr)

        if effect_size == 0:
            return float("inf")

        n = ((z_alpha + z_beta) ** 2 * variance) / (effect_size**2)

        return math.ceil(n)

    def generate_report(self) -> str:
        """
        Generate a validation summary report.

        Returns:
            Formatted report string
        """
        lines = [
            "=" * 60,
            "PERFORMANCE VALIDATION REPORT",
            "=" * 60,
            f"Generated: {datetime.now().isoformat()}",
            f"Total validations: {len(self.validation_history)}",
            "",
        ]

        if not self.validation_history:
            lines.append("No validation history available.")
            return "\n".join(lines)

        # Summarize by significance
        sig_counts = defaultdict(int)
        improvements = 0

        for entry in self.validation_history:
            sig_counts[entry.get("significance", "unknown")] += 1
            if entry.get("is_improvement", False):
                improvements += 1

        lines.extend(
            [
                "SIGNIFICANCE BREAKDOWN:",
                "-" * 40,
                f"  Strong (p<=0.01): {sig_counts.get('strong', 0)}",
                f"  Moderate (p<=0.05): {sig_counts.get('moderate', 0)}",
                f"  Weak (p<=0.10): {sig_counts.get('weak', 0)}",
                f"  Not significant: {sig_counts.get('not_significant', 0)}",
                "",
                f"Confirmed improvements: {improvements}/{len(self.validation_history)}",
                "",
            ]
        )

        # Recent validations
        lines.extend(["RECENT VALIDATIONS:", "-" * 40])

        for entry in self.validation_history[-5:]:
            status = "✓" if entry.get("is_improvement") else "○"
            lines.append(
                f"  {status} {entry.get('test_name', 'unknown')}: "
                f"{entry.get('difference_percent', 0):.1f}% "
                f"({entry.get('significance', 'unknown')})"
            )

        lines.append("=" * 60)

        return "\n".join(lines)

    def _calculate_std(self, values: List[float], mean: float) -> float:
        """Calculate standard deviation."""
        if len(values) < 2:
            return 0.0

        variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
        return math.sqrt(variance)

    def _two_sample_t_test(
        self,
        mean_a: float,
        mean_b: float,
        std_a: float,
        std_b: float,
        n_a: int,
        n_b: int,
    ) -> Tuple[float, float]:
        """Perform two-sample t-test (Welch's)."""
        if std_a == 0 and std_b == 0:
            return 0.0, 1.0

        # Standard error of difference
        se = math.sqrt((std_a**2 / n_a) + (std_b**2 / n_b))

        if se == 0:
            return 0.0, 1.0

        # t-statistic
        t_stat = (mean_b - mean_a) / se

        # Degrees of freedom (Welch-Satterthwaite)
        numerator = ((std_a**2 / n_a) + (std_b**2 / n_b)) ** 2
        denominator = (std_a**4) / (n_a**2 * (n_a - 1)) + (std_b**4) / (
            n_b**2 * (n_b - 1)
        )
        numerator / denominator if denominator > 0 else n_a + n_b - 2

        # Approximate p-value using normal distribution
        # (For large samples, t-distribution ≈ normal)
        p_value = 2 * (1 - self._normal_cdf(abs(t_stat)))

        return t_stat, p_value

    def _normal_cdf(self, x: float) -> float:
        """Approximate standard normal CDF."""
        # Using error function approximation
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))

    def _confidence_interval_diff(
        self,
        mean_a: float,
        mean_b: float,
        std_a: float,
        std_b: float,
        n_a: int,
        n_b: int,
    ) -> Tuple[float, float]:
        """Calculate confidence interval for difference in means."""
        difference = mean_b - mean_a

        se = math.sqrt((std_a**2 / n_a) + (std_b**2 / n_b))
        z = self.Z_SCORES[self.default_confidence]

        margin = z * se

        return (difference - margin, difference + margin)

    def _linear_regression(self, values: List[float]) -> Tuple[float, float]:
        """Simple linear regression returning slope and R-squared."""
        n = len(values)
        if n < 2:
            return 0.0, 0.0

        x = list(range(n))
        y = values

        # Means
        x_mean = sum(x) / n
        y_mean = sum(y) / n

        # Slope
        numerator = sum((x[i] - x_mean) * (y[i] - y_mean) for i in range(n))
        denominator = sum((x[i] - x_mean) ** 2 for i in range(n))

        slope = numerator / denominator if denominator > 0 else 0

        # R-squared
        y_pred = [slope * x[i] + (y_mean - slope * x_mean) for i in range(n)]
        ss_res = sum((y[i] - y_pred[i]) ** 2 for i in range(n))
        ss_tot = sum((y[i] - y_mean) ** 2 for i in range(n))

        r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

        return slope, r_squared

    def _get_significance_level(self, p_value: float) -> SignificanceLevel:
        """Convert p-value to significance level."""
        if p_value <= 0.01:
            return SignificanceLevel.STRONG
        elif p_value <= 0.05:
            return SignificanceLevel.MODERATE
        elif p_value <= 0.10:
            return SignificanceLevel.WEAK
        else:
            return SignificanceLevel.NOT_SIGNIFICANT

    def _generate_recommendation(
        self,
        difference_percent: float,
        significance: SignificanceLevel,
        is_improvement: bool,
        metric: str,
    ) -> str:
        """Generate human-readable recommendation."""
        if is_improvement:
            if significance == SignificanceLevel.STRONG:
                return (
                    f"Strong evidence of {metric} improvement ({difference_percent:+.1f}%). "
                    "Recommend adopting new strategy."
                )
            else:
                return (
                    f"Moderate evidence of {metric} improvement ({difference_percent:+.1f}%). "
                    "Consider adopting with continued monitoring."
                )

        elif significance == SignificanceLevel.NOT_SIGNIFICANT:
            return (
                f"No significant difference in {metric} ({difference_percent:+.1f}%). "
                "Continue testing with more data."
            )

        else:
            return (
                f"Possible {metric} decline ({difference_percent:+.1f}%). "
                "Investigate before proceeding."
            )

    def _log_validation(self, result: ValidationResult):
        """Log validation result to history."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "test_name": result.test_name,
            "hypothesis": result.hypothesis,
            "p_value": result.p_value,
            "significance": result.significance.value,
            "difference_percent": result.difference_percent,
            "is_improvement": result.is_improvement,
            "recommendation": result.recommendation,
        }

        self.validation_history.append(entry)
        self._save_history()

    def _load_history(self):
        """Load validation history from disk."""
        history_file = self.state_dir / "validation_history.json"

        if history_file.exists():
            try:
                with open(history_file) as f:
                    self.validation_history = json.load(f)
            except (OSError, json.JSONDecodeError):
                self.validation_history = []

    def _save_history(self):
        """Save validation history to disk."""
        history_file = self.state_dir / "validation_history.json"

        # Keep only last 100 entries
        if len(self.validation_history) > 100:
            self.validation_history = self.validation_history[-100:]

        with open(history_file, "w") as f:
            json.dump(self.validation_history, f, indent=2)
