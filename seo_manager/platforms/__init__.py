"""
Platform adapters for SEO Manager.
"""

from .blog import BlogAdapter
from .twitter import TwitterAdapter
from .youtube import YouTubeAdapter

__all__ = [
    "BlogAdapter",
    "TwitterAdapter",
    "YouTubeAdapter",
]
