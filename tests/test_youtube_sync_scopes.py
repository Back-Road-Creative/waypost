"""Tests for YouTubeAnalyticsSync scope handling.

The token file may have been authorized with MORE scopes than the class needs.
Loading credentials must use the scopes FROM the token, not hardcoded ones,
otherwise Google's auth library rejects the mismatch.

Since google-api-python-client may not be installed in CI, these tests mock
the google auth/API modules.
"""

import json
import sys
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def mock_google_apis():
    """Mock all Google API dependencies so tests work without google-api-python-client."""
    # Create mock modules
    mock_credentials = MagicMock()
    mock_request = MagicMock()
    mock_flow = MagicMock()
    mock_discovery = MagicMock()
    mock_errors = MagicMock()

    # Make Credentials.from_authorized_user_file return a mock with the scopes passed
    def fake_from_authorized_user_file(path, scopes):
        cred = MagicMock()
        cred.scopes = scopes
        return cred

    mock_credentials.Credentials.from_authorized_user_file = (
        fake_from_authorized_user_file
    )

    modules = {
        "google": MagicMock(),
        "google.auth": MagicMock(),
        "google.auth.transport": MagicMock(),
        "google.auth.transport.requests": mock_request,
        "google.oauth2": MagicMock(),
        "google.oauth2.credentials": mock_credentials,
        "google_auth_oauthlib": MagicMock(),
        "google_auth_oauthlib.flow": mock_flow,
        "googleapiclient": MagicMock(),
        "googleapiclient.discovery": mock_discovery,
        "googleapiclient.errors": mock_errors,
    }

    with patch.dict(sys.modules, modules):
        # Force re-import so the module picks up our mocks
        if "seo_manager.analytics.youtube_sync" in sys.modules:
            del sys.modules["seo_manager.analytics.youtube_sync"]
        yield


@pytest.fixture
def superset_token_file(tmp_path):
    """Token authorized with 4 scopes (superset of the 2 the class wants)."""
    token_data = {
        "token": "fake-access-token",
        "refresh_token": "fake-refresh-token",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "fake-client-id.apps.googleusercontent.com",
        "client_secret": "fake-client-secret",  # pragma: allowlist secret
        "scopes": [
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube",
            "https://www.googleapis.com/auth/youtube.readonly",
            "https://www.googleapis.com/auth/yt-analytics.readonly",
        ],
    }
    token_path = tmp_path / "youtube_credentials.json"
    token_path.write_text(json.dumps(token_data))
    return str(token_path)


@pytest.fixture
def minimal_token_file(tmp_path):
    """Token with only the 2 scopes the class originally hardcoded."""
    token_data = {
        "token": "fake-access-token",
        "refresh_token": "fake-refresh-token",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "fake-client-id.apps.googleusercontent.com",
        "client_secret": "fake-client-secret",  # pragma: allowlist secret
        "scopes": [
            "https://www.googleapis.com/auth/youtube.readonly",
            "https://www.googleapis.com/auth/yt-analytics.readonly",
        ],
    }
    token_path = tmp_path / "youtube_credentials.json"
    token_path.write_text(json.dumps(token_data))
    return str(token_path)


@pytest.fixture
def no_scopes_token_file(tmp_path):
    """Token file with no scopes key — should fall back to class SCOPES."""
    token_data = {
        "token": "fake-access-token",
        "refresh_token": "fake-refresh-token",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "fake-client-id.apps.googleusercontent.com",
        "client_secret": "fake-client-secret",  # pragma: allowlist secret
    }
    token_path = tmp_path / "youtube_credentials.json"
    token_path.write_text(json.dumps(token_data))
    return str(token_path)


class TestYouTubeSyncScopeHandling:
    """YouTubeAnalyticsSync must accept tokens with superset scopes."""

    def test_superset_scopes_accepted(self, superset_token_file):
        """Token authorized with 4 scopes must load without error."""
        from seo_manager.analytics.youtube_sync import YouTubeAnalyticsSync

        sync = YouTubeAnalyticsSync(superset_token_file)
        assert sync.credentials is not None
        assert len(sync.credentials.scopes) == 4

    def test_minimal_scopes_accepted(self, minimal_token_file):
        """Token with exactly the required scopes must still work."""
        from seo_manager.analytics.youtube_sync import YouTubeAnalyticsSync

        sync = YouTubeAnalyticsSync(minimal_token_file)
        assert sync.credentials is not None
        assert len(sync.credentials.scopes) == 2

    def test_no_scopes_in_token_uses_default(self, no_scopes_token_file):
        """Token file without scopes key falls back to class SCOPES."""
        from seo_manager.analytics.youtube_sync import YouTubeAnalyticsSync

        sync = YouTubeAnalyticsSync(no_scopes_token_file)
        assert sync.credentials is not None
        assert len(sync.credentials.scopes) == 2
