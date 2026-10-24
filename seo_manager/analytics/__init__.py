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

import importlib
from typing import TYPE_CHECKING, Any

# Submodules load on first attribute access. Importing them all here made
# ``python -m seo_manager.analytics.<module>`` warn that the module was already
# in sys.modules, and slowed every import of the package.
if TYPE_CHECKING:
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

_LAZY_EXPORTS = {
    "AnalyticsTracker": ".tracker",
    "ContentTheme": ".theme_rotator",
    "ContinuousOptimizationDaemon": ".continuous_optimization_daemon",
    "DaemonState": ".continuous_optimization_daemon",
    "MetricoolAnalyticsImporter": ".metricool_import",
    "MetricoolPDFImporter": ".metricool_pdf_import",
    "OptimizationCycleType": ".continuous_optimization_daemon",
    "OptimizationResult": ".continuous_optimization_daemon",
    "PerformancePattern": ".seo_optimizer",
    "PerformanceValidator": ".performance_validator",
    "SEOOptimizer": ".seo_optimizer",
    "SEORecommendation": ".seo_optimizer",
    "SignificanceLevel": ".performance_validator",
    "StyleAnalysis": ".thumbnail_ab_tracker",
    "ThemeAnalysis": ".theme_rotator",
    "ThemePerformance": ".theme_rotator",
    "ThemeRotator": ".theme_rotator",
    "ThemeTemplate": ".theme_rotator",
    "ThumbnailABTracker": ".thumbnail_ab_tracker",
    "ThumbnailPerformance": ".thumbnail_ab_tracker",
    "ThumbnailStyle": ".thumbnail_ab_tracker",
    "TrendAnalysis": ".performance_validator",
    "ValidationResult": ".performance_validator",
    "get_rotated_theme": ".theme_rotator",
}


def __getattr__(name: str) -> Any:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted([*globals(), *_LAZY_EXPORTS])
