"""Tests for the YouTube Analytics CTR/impressions fetch path.

`sync_video_analytics` historically requested only the Data API
`part="statistics"` (views/likes/comments) and never read the
`youtubeAnalytics` v2 client, so `clicks`/`impressions` were never tracked
and the optimizer's `performer.get("ctr", 0)` was structurally always 0.

These tests pin the NEW behavior: a `.reports().query()` call against
`youtubeAnalytics` that requests the click/engagement metrics the API
actually exposes (the true thumbnail-impression CTR is Studio-only and NOT
available in the API — see ANALYTICS_CTR_LIMITATION), and an explicit
degrade-not-zero path when no analytics row comes back.

Google API libs are mocked so the suite runs without google-api-python-client.
"""

import json
import sys
from unittest.mock import MagicMock, patch

import pytest

from seo_manager.analytics import metric_source


@pytest.fixture(autouse=True)
def mock_google_apis():
    """Mock Google API deps + capture the built clients."""
    mock_credentials = MagicMock()

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
        "google.auth.transport.requests": MagicMock(),
        "google.oauth2": MagicMock(),
        "google.oauth2.credentials": mock_credentials,
        "google_auth_oauthlib": MagicMock(),
        "google_auth_oauthlib.flow": MagicMock(),
        "googleapiclient": MagicMock(),
        "googleapiclient.discovery": MagicMock(),
        "googleapiclient.errors": MagicMock(),
    }
    with patch.dict(sys.modules, modules):
        if "seo_manager.analytics.youtube_sync" in sys.modules:
            del sys.modules["seo_manager.analytics.youtube_sync"]
        yield


@pytest.fixture
def token_file(tmp_path):
    token_data = {
        "token": "fake",
        "refresh_token": "fake",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "fake.apps.googleusercontent.com",
        "client_secret": "fake",  # pragma: allowlist secret
        "scopes": [
            "https://www.googleapis.com/auth/youtube.readonly",
            "https://www.googleapis.com/auth/yt-analytics.readonly",
        ],
    }
    p = tmp_path / "youtube_credentials.json"
    p.write_text(json.dumps(token_data))
    return str(p)


def _make_sync(token_file):
    from seo_manager.analytics.youtube_sync import YouTubeAnalyticsSync

    return YouTubeAnalyticsSync(token_file)


def _wire_analytics_response(sync, rows, column_headers):
    """Wire sync.youtube_analytics.reports().query().execute() -> response."""
    response = {
        "columnHeaders": [{"name": n} for n in column_headers],
        "rows": rows,
    }
    query = MagicMock()
    query.execute.return_value = response
    reports = MagicMock()
    reports.query.return_value = query
    sync.youtube_analytics.reports.return_value = reports
    return query


def _wire_stats(sync):
    """Wire the Data-API statistics response."""
    stats = {"viewCount": "1000", "likeCount": "50", "commentCount": "10"}
    sync.youtube_data.videos().list().execute.return_value = {
        "items": [{"statistics": stats}]
    }


class TestAnalyticsMetricsFetch:
    def test_fetch_returns_parsed_engagement_metrics(self, token_file):
        sync = _make_sync(token_file)
        # cardClickRate is the closest real click signal the API exposes.
        headers = ["views", "cardImpressions", "cardClicks", "cardClickRate"]
        _wire_analytics_response(sync, [[1000, 800, 40, 0.05]], headers)

        metrics = sync.fetch_video_analytics("vid123")

        assert metrics is not None
        assert metrics["views"] == 1000
        assert metrics["cardImpressions"] == 800
        assert metrics["cardClicks"] == 40
        assert metrics["cardClickRate"] == pytest.approx(0.05)

    def test_no_rows_returns_none_not_zero(self, token_file):
        """An empty analytics result is degraded (None), never a fake 0 CTR."""
        sync = _make_sync(token_file)
        _wire_analytics_response(sync, [], ["views", "cardClickRate"])

        assert sync.fetch_video_analytics("vid123") is None

    def test_query_targets_youtube_analytics_client(self, token_file):
        """The fetch must use youtubeAnalytics.reports, not the Data API."""
        sync = _make_sync(token_file)
        _wire_analytics_response(sync, [[1]], ["views"])

        sync.fetch_video_analytics("vidABC")

        sync.youtube_analytics.reports.assert_called()
        _, kwargs = sync.youtube_analytics.reports().query.call_args
        assert kwargs.get("ids") == "channel==MINE"
        assert "vidABC" in kwargs.get("filters", "")
        # Must request the click-rate metric we map to ctr.
        assert "cardClickRate" in kwargs.get("metrics", "")


class TestSyncWiresClicksAndCtr:
    def test_sync_passes_ctr_and_clicks_to_tracker(self, token_file):
        """sync_video_analytics threads the analytics-derived ctr into the tracker."""
        sync = _make_sync(token_file)
        _wire_stats(sync)
        headers = ["views", "cardImpressions", "cardClicks", "cardClickRate"]
        _wire_analytics_response(sync, [[1000, 800, 40, 0.05]], headers)

        manager = MagicMock()
        manager.analytics.track_performance.return_value = "evt-1"

        event_id = sync.sync_video_analytics(manager, "vid123", "episode_001")

        assert event_id == "evt-1"
        _, kwargs = manager.analytics.track_performance.call_args
        assert kwargs["ctr"] == pytest.approx(0.05)
        assert kwargs["clicks"] == 40
        assert kwargs["impressions"] == 800

    def test_sync_degrades_ctr_to_none_when_no_analytics(self, token_file):
        """No analytics row -> ctr threaded as None (not a fake 0)."""
        sync = _make_sync(token_file)
        _wire_stats(sync)
        _wire_analytics_response(sync, [], ["views", "cardClickRate"])

        manager = MagicMock()
        manager.analytics.track_performance.return_value = "evt-2"

        sync.sync_video_analytics(manager, "vid123", "episode_001")

        _, kwargs = manager.analytics.track_performance.call_args
        assert kwargs["ctr"] is None


class TestCardProvenanceTravelsWithTheValue:
    """The card rate must arrive downstream labelled, not as a bare `ctr`."""

    def test_sync_tags_the_rate_as_card_sourced(self, token_file):
        sync = _make_sync(token_file)
        _wire_stats(sync)
        headers = ["views", "cardImpressions", "cardClicks", "cardClickRate"]
        _wire_analytics_response(sync, [[1000, 800, 40, 0.05]], headers)

        manager = MagicMock()
        sync.sync_video_analytics(manager, "vid123", "episode_001")

        _, kwargs = manager.analytics.track_performance.call_args
        # A consumer can tell this is not thumbnail data without reading a comment.
        assert kwargs["ctr_source"] == metric_source.YOUTUBE_CARD_CLICK_RATE
        assert not metric_source.is_thumbnail_click_through(kwargs["ctr_source"])

    def test_no_analytics_row_tags_the_source_unknown(self, token_file):
        sync = _make_sync(token_file)
        _wire_stats(sync)
        _wire_analytics_response(sync, [], ["views", "cardClickRate"])

        manager = MagicMock()
        sync.sync_video_analytics(manager, "vid123", "episode_001")

        _, kwargs = manager.analytics.track_performance.call_args
        assert kwargs["ctr"] is None
        assert kwargs["ctr_source"] == metric_source.UNKNOWN


class TestScopesConstantRemoved:
    def test_no_misleading_scopes_constant(self, token_file):
        """The stale hardcoded SCOPES advertising is dropped (token drives scopes)."""
        from seo_manager.analytics import youtube_sync

        assert not hasattr(youtube_sync.YouTubeAnalyticsSync, "SCOPES")


class TestAnalyticsReportWindow:
    """The Analytics API rejects a far-future end date with HTTP 400
    ("Invalid value (2100-01-01) given in field parameters.end-date.").
    The lifetime window must end on today's date in YouTube's reporting
    zone (Pacific)."""

    def test_window_ends_today_pacific(self, token_file):
        from datetime import datetime, timezone

        from seo_manager.analytics.youtube_sync import analytics_report_window

        # 02:00 UTC on 2030-01-02 is still 2030-01-01 in Los Angeles.
        start, end = analytics_report_window(
            now=datetime(2030, 1, 2, 2, 0, tzinfo=timezone.utc)
        )
        assert start == "2005-01-01"
        assert end == "2030-01-01"

    def test_fetch_never_requests_a_future_end_date(self, token_file, monkeypatch):
        import importlib
        from datetime import datetime, timezone

        # The autouse fixture drops the module from sys.modules, so patch the
        # copy a fresh import resolves to, not the package attribute.
        youtube_sync = importlib.import_module("seo_manager.analytics.youtube_sync")
        fixed = datetime(2030, 1, 2, 2, 0, tzinfo=timezone.utc)

        class _FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed.astimezone(tz) if tz else fixed

        monkeypatch.setattr(youtube_sync, "datetime", _FrozenDatetime)
        sync = _make_sync(token_file)
        _wire_analytics_response(sync, [[1]], ["views"])

        sync.fetch_video_analytics("vidABC")

        _, kwargs = sync.youtube_analytics.reports().query.call_args
        assert kwargs["startDate"] == "2005-01-01"
        assert kwargs["endDate"] == "2030-01-01"
