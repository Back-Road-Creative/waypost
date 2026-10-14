"""
Analytics module for SEO Manager.

Provides performance tracking, analytics, optimization, and continuous improvement.

Components:
- AnalyticsTracker: Track generation and performance events
- SEOOptimizer: Learn patterns and generate recommendations
- ContinuousOptimizationDaemon: Auto-sync and re-learn periodically
- ThumbnailABTracker: Track thumbnail style performance
- PerformanceValidator: Statistical validation of improvements
"""

from .continuous_optimization_daemon import (
    ContinuousOptimizationDaemon,
    DaemonState,
    OptimizationCycleType,
    OptimizationResult,
)
from .metricool_import import MetricoolAnalyticsImporter
from .metricool_pdf_import import MetricoolPDFImporter
from .performance_validator import (
    PerformanceValidator,
    SignificanceLevel,
    TrendAnalysis,
    ValidationResult,
)
from .seo_optimizer import PerformancePattern, SEOOptimizer, SEORecommendation
from .theme_rotator import (
    ContentTheme,
    ThemeAnalysis,
    ThemePerformance,
    ThemeRotator,
    ThemeTemplate,
    get_rotated_theme,
)
from .thumbnail_ab_tracker import (
    StyleAnalysis,
    ThumbnailABTracker,
    ThumbnailPerformance,
    ThumbnailStyle,
)
from .tracker import AnalyticsTracker

__all__ = [
    "AnalyticsTracker",
    "ContentTheme",
    "ContinuousOptimizationDaemon",
    "DaemonState",
    "MetricoolAnalyticsImporter",
    "MetricoolPDFImporter",
    "OptimizationCycleType",
    "OptimizationResult",
    "PerformancePattern",
    "PerformanceValidator",
    "SEOOptimizer",
    "SEORecommendation",
    "SignificanceLevel",
    "StyleAnalysis",
    "ThemeAnalysis",
    "ThemePerformance",
    "ThemeRotator",
    "ThemeTemplate",
    "ThumbnailABTracker",
    "ThumbnailPerformance",
    "ThumbnailStyle",
    "TrendAnalysis",
    "ValidationResult",
    "get_rotated_theme",
]
