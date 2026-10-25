"""
SEO Manager - Unified Social Media SEO Management System

A library for generating, validating, and optimizing SEO metadata
across multiple platforms.
"""

__version__ = "0.2.0"

# Core components
from .core.manager import SEOManager
from .core.metadata import ContentType, Platform, SEOMetadata

__all__ = [
    "ContentType",
    "Platform",
    "SEOManager",
    "SEOMetadata",
]
