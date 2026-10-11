"""
YouTube-specific features module.

Provides:
- Thumbnail generation and design system
- Video uploader with metadata
- Batch uploader for multiple videos
- Video SEO generation for journey content
"""

from .batch_uploader import (
    BatchQuotaError,
    BatchUploadConfig,
    BatchUploader,
    BatchUploadResult,
    VideoConfig,
    VideoUploadResult,
)
from .thumbnail_generator import ThumbnailStyle, YouTubeThumbnailGenerator
from .uploader import PrivacyStatus, UploadConfig, UploadResult, YouTubeUploader
from .video_seo_generator import VideoSEOGenerator

__all__ = [
    "BatchQuotaError",
    "BatchUploadConfig",
    "BatchUploadResult",
    "BatchUploader",
    "PrivacyStatus",
    "ThumbnailStyle",
    "UploadConfig",
    "UploadResult",
    "VideoConfig",
    "VideoSEOGenerator",
    "VideoUploadResult",
    "YouTubeThumbnailGenerator",
    "YouTubeUploader",
]
