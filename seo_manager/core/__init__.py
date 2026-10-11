"""
Core SEO Manager functionality.

Provides:
- Metadata structures
- Content detection
"""

from .content_detector import ContentDetector
from .metadata import ContentType, Platform, SEOMetadata

__all__ = [
    "ContentDetector",
    "ContentType",
    "Platform",
    "SEOMetadata",
]
