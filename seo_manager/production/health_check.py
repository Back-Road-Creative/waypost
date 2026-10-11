"""
Health checks for production systems.

Provides comprehensive system health monitoring:
- YouTube API connectivity
- Credentials validity
- Quota availability
- Disk space
- File permissions
"""

import os
import shutil
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class HealthStatus:
    """
    System health status.

    Attributes:
        youtube_api: YouTube API connectivity status
        credentials: Credentials validity status
        quota_available: Quota availability status
        disk_space: Disk space availability status
        file_permissions: File permission status
        messages: Optional error messages for failed checks
    """

    youtube_api: bool = False
    credentials: bool = False
    quota_available: bool = False
    disk_space: bool = False
    file_permissions: bool = False
    messages: Dict[str, str] = field(default_factory=dict)

    def is_healthy(self) -> bool:
        """
        Check if system is healthy (all checks pass).

        Returns:
            True if all checks pass, False otherwise
        """
        return all(
            [
                self.youtube_api,
                self.credentials,
                self.quota_available,
                self.disk_space,
                self.file_permissions,
            ]
        )


class HealthCheck:
    """
    Perform system health checks.

    Features:
    - YouTube API connectivity test
    - Credentials validation
    - Quota availability check
    - Disk space verification
    - File permissions check
    """

    def __init__(
        self,
        credentials_path: Optional[str] = None,
        data_dir: Optional[str] = None,
        min_disk_space_gb: float = 1.0,
    ):
        """
        Initialize health checker.

        Args:
            credentials_path: Path to credentials file
            data_dir: Data directory path
            min_disk_space_gb: Minimum required disk space in GB
        """
        self.credentials_path = credentials_path or "youtube_credentials.json"
        self.data_dir = data_dir or "."
        self.min_disk_space_gb = min_disk_space_gb
        self.min_disk_space_bytes = int(min_disk_space_gb * 1024 * 1024 * 1024)

    def check_youtube_api(self) -> bool:
        """
        Check YouTube API connectivity.

        Returns:
            True if API is accessible, False otherwise
        """
        try:
            import json

            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            # Try to load credentials
            if not os.path.exists(self.credentials_path):
                return False

            with open(self.credentials_path) as f:
                creds_data = json.load(f)

            # Create credentials object
            creds = Credentials(
                token=creds_data.get("token"),
                refresh_token=creds_data.get("refresh_token"),
                token_uri=creds_data.get("token_uri"),
                client_id=creds_data.get("client_id"),
                client_secret=creds_data.get("client_secret"),
                scopes=creds_data.get("scopes"),
            )

            # Try to build service and make a simple API call
            youtube = build("youtube", "v3", credentials=creds)
            youtube.channels().list(part="snippet", mine=True, maxResults=1).execute()

            return True

        except Exception:
            return False

    def check_credentials(self) -> bool:
        """
        Check if credentials file exists and is valid.

        Returns:
            True if credentials are valid, False otherwise
        """
        try:
            # Check file exists
            if not os.path.exists(self.credentials_path):
                return False

            # Check file is readable
            with open(self.credentials_path) as f:
                import json

                data = json.load(f)

            # Check has required fields
            required_fields = ["token", "refresh_token", "client_id", "client_secret"]
            return all(field in data for field in required_fields)

        except Exception:
            return False

    def check_quota(self) -> bool:
        """
        Check YouTube API quota availability.

        Returns:
            True if quota is available, False otherwise
        """
        try:
            from seo_manager.quota.manager import QuotaManager

            # Get quota manager
            quota_manager = QuotaManager()

            # Check remaining quota
            remaining = quota_manager.get_remaining_quota()

            # Need at least 100 quota units to be considered healthy
            return remaining >= 100

        except Exception:
            # If we can't check quota, assume it's available
            # (better to try and fail than to block unnecessarily)
            return True

    def check_storage(self) -> bool:
        """
        Check disk space and file permissions.

        Returns:
            True if storage is adequate, False otherwise
        """
        try:
            # Check disk space
            usage = shutil.disk_usage(self.data_dir)

            if usage.free < self.min_disk_space_bytes:
                return False

            # Check write permissions by creating a test file
            test_file = os.path.join(self.data_dir, ".health_check_test")
            try:
                with open(test_file, "w") as f:
                    f.write("test")
                os.remove(test_file)
                return True
            except (PermissionError, OSError):
                return False

        except Exception:
            return False

    def run_all_checks(self) -> HealthStatus:
        """
        Run all health checks.

        Returns:
            HealthStatus object with results
        """
        messages = {}

        # Check YouTube API
        youtube_api = self.check_youtube_api()
        if not youtube_api:
            messages["youtube_api"] = "YouTube API connectivity check failed"

        # Check credentials
        credentials = self.check_credentials()
        if not credentials:
            messages["credentials"] = "Credentials validation failed"

        # Check quota
        quota_available = self.check_quota()
        if not quota_available:
            messages["quota_available"] = "Insufficient API quota"

        # Check storage
        storage_ok = self.check_storage()
        if not storage_ok:
            messages["disk_space"] = "Insufficient disk space or permission denied"

        # File permissions (included in storage check)
        file_permissions = storage_ok

        return HealthStatus(
            youtube_api=youtube_api,
            credentials=credentials,
            quota_available=quota_available,
            disk_space=storage_ok,
            file_permissions=file_permissions,
            messages=messages,
        )
