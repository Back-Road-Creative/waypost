"""Tests for Metricool PDF analytics import."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from seo_manager.analytics.metricool_pdf_import import (
    ACCOUNT_ROLES,
    ContentMatcher,
    ExtractedPost,
    MetricoolPDFImporter,
    MetricoolPDFParser,
    PDFTextExtractor,
    _parse_metric_value,
    load_accounts,
    main,
    validate_accounts,
)
from seo_manager.core.events import EventType

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
SAMPLE_TEXT = FIXTURES / "metricool_sample_text.txt"

# Display names printed in the sample report, mapped to their network role.
ACCOUNTS = {"Sample Account": "tiktok", "SampleBrand": "facebook_youtube"}


# --- _parse_metric_value ---


class TestParseMetricValue:
    def test_plain_int(self):
        assert _parse_metric_value("1234") == 1234

    def test_k_suffix_lower(self):
        assert _parse_metric_value("98.76k") == 98760

    def test_k_suffix_upper(self):
        assert _parse_metric_value("87.65K") == 87650

    def test_m_suffix(self):
        assert _parse_metric_value("1.2m") == 1200000

    def test_commas(self):
        assert _parse_metric_value("25,432") == 25432

    def test_dash(self):
        assert _parse_metric_value("-") == 0

    def test_empty(self):
        assert _parse_metric_value("") == 0

    def test_duration_string(self):
        assert _parse_metric_value("15s") == "15s"

    def test_duration_full(self):
        assert _parse_metric_value("2h 6m 0s") == "2h 6m 0s"

    def test_percentage(self):
        result = _parse_metric_value("1.89%")
        assert abs(result - 1.89) < 0.01

    def test_float(self):
        result = _parse_metric_value("1.89")
        assert abs(result - 1.89) < 0.01


# --- PDFTextExtractor ---


class TestPDFTextExtractor:
    def test_extract_calls_subprocess(self, tmp_path):
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"fake pdf")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="extracted text")
            extractor = PDFTextExtractor()
            result = extractor.extract(str(pdf))

        assert result == "extracted text"
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "pdftotext"
        assert "-layout" in cmd

    def test_extract_no_layout(self, tmp_path):
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"fake pdf")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="text")
            extractor = PDFTextExtractor()
            extractor.extract(str(pdf), layout=False)

        cmd = mock_run.call_args[0][0]
        assert "-layout" not in cmd

    def test_extract_file_not_found(self):
        extractor = PDFTextExtractor()
        with pytest.raises(FileNotFoundError):
            extractor.extract("/nonexistent.pdf")

    def test_cache_hit(self, tmp_path):
        pdf = tmp_path / "report.pdf"
        pdf.write_bytes(b"fake pdf")
        cache = tmp_path / "report.extracted.txt"
        cache.write_text("cached text", encoding="utf-8")

        extractor = PDFTextExtractor()
        with patch.object(extractor, "extract") as mock_extract:
            result = extractor.extract_cached(str(pdf))

        assert result == "cached text"
        mock_extract.assert_not_called()

    def test_cache_refresh_when_pdf_newer(self, tmp_path):
        cache = tmp_path / "report.extracted.txt"
        cache.write_text("stale text", encoding="utf-8")

        import time

        time.sleep(0.05)

        pdf = tmp_path / "report.pdf"
        pdf.write_bytes(b"new pdf")

        extractor = PDFTextExtractor()
        with patch.object(extractor, "extract", return_value="fresh text"):
            result = extractor.extract_cached(str(pdf))

        assert result == "fresh text"
        assert cache.read_text() == "fresh text"


# --- MetricoolPDFParser ---


class TestMetricoolPDFParser:
    @pytest.fixture
    def sample_text(self):
        return SAMPLE_TEXT.read_text(encoding="utf-8")

    def test_detects_tiktok_platform(self, sample_text):
        parser = MetricoolPDFParser(ACCOUNTS)
        result = parser.parse(sample_text)
        platforms = {p.platform for p in result["posts"]}
        assert "tiktok" in platforms

    def test_detects_youtube_platform(self, sample_text):
        parser = MetricoolPDFParser(ACCOUNTS)
        result = parser.parse(sample_text)
        platforms = {p.platform for p in result["posts"]}
        assert "youtube" in platforms

    def test_extracts_post_date(self, sample_text):
        parser = MetricoolPDFParser(ACCOUNTS)
        result = parser.parse(sample_text)
        dates = {p.published_date for p in result["posts"]}
        assert "2026-01-06" in dates

    def test_extracts_caption(self, sample_text):
        parser = MetricoolPDFParser(ACCOUNTS)
        result = parser.parse(sample_text)
        tiktok_posts = [p for p in result["posts"] if p.platform == "tiktok"]
        assert tiktok_posts
        assert "Guess the lookout" in tiktok_posts[0].caption_snippet

    def test_extracts_metrics_with_k_suffix(self, sample_text):
        parser = MetricoolPDFParser(ACCOUNTS)
        result = parser.parse(sample_text)
        tiktok_posts = [p for p in result["posts"] if p.platform == "tiktok"]
        assert tiktok_posts
        assert tiktok_posts[0].metrics.get("views") == 88400

    def test_parses_column_headers_dynamically(self):
        header = "                Published             Type   Text                                Views    Likes   Comments   Shares   Reach   Duration   Engagement"
        parser = MetricoolPDFParser(ACCOUNTS)
        headers = parser._parse_column_headers(header)
        names = [h[0] for h in headers]
        assert "Views" in names
        assert "Likes" in names
        assert "Engagement" in names

    def test_multi_line_caption(self, sample_text):
        parser = MetricoolPDFParser(ACCOUNTS)
        result = parser.parse(sample_text)
        tiktok_posts = [p for p in result["posts"] if p.platform == "tiktok"]
        # Second post has "Weekend walks" caption
        if len(tiktok_posts) >= 2:
            assert "Weekend walks" in tiktok_posts[1].caption_snippet

    def test_platform_disambiguation_youtube(self, sample_text):
        parser = MetricoolPDFParser(ACCOUNTS)
        result = parser.parse(sample_text)
        yt_posts = [p for p in result["posts"] if p.platform == "youtube"]
        assert len(yt_posts) >= 1

    def test_community_growth_tiktok(self, sample_text):
        parser = MetricoolPDFParser(ACCOUNTS)
        result = parser.parse(sample_text)
        assert "tiktok" in result["community"]
        assert result["community"]["tiktok"]["followers"] == 9300

    def test_community_growth_youtube(self, sample_text):
        parser = MetricoolPDFParser(ACCOUNTS)
        result = parser.parse(sample_text)
        assert "youtube" in result["community"]
        assert result["community"]["youtube"]["followers"] == 6400

    def test_three_tiktok_posts(self, sample_text):
        parser = MetricoolPDFParser(ACCOUNTS)
        result = parser.parse(sample_text)
        tiktok_posts = [p for p in result["posts"] if p.platform == "tiktok"]
        assert len(tiktok_posts) == 3


# --- ContentMatcher ---


class TestContentMatcher:
    def test_exact_match(self):
        known = {
            "post-123": {
                "date": "2026-01-06",
                "caption": "Guess the lookout if you know it",
            }
        }
        matcher = ContentMatcher(known)
        post = ExtractedPost(
            platform="tiktok",
            published_date="2026-01-06",
            published_time="16:47",
            caption_snippet="Guess the lookout if you know it A calm ridge",
        )
        assert matcher.match(post) == "post-123"

    def test_fuzzy_match(self):
        known = {
            "post-456": {
                "date": "2026-01-13",
                "caption": "Weekend walks along the river trail are the best",
            }
        }
        matcher = ContentMatcher(known)
        post = ExtractedPost(
            platform="tiktok",
            published_date="2026-01-13",
            published_time="11:20",
            caption_snippet="Weekend walks along the river trail are always",
        )
        result = matcher.match(post)
        assert result == "post-456"

    def test_no_match_returns_none(self):
        known = {
            "post-789": {
                "date": "2026-01-01",
                "caption": "Completely different content",
            }
        }
        matcher = ContentMatcher(known)
        post = ExtractedPost(
            platform="tiktok",
            published_date="2026-01-06",
            published_time="16:47",
            caption_snippet="Guess the lookout if you know it",
        )
        assert matcher.match(post) is None

    def test_normalize_strips_emoji(self):
        result = ContentMatcher.normalize_caption("Guess the lookout 👇 if you know")
        assert result == "guess the lookout  if you know"


# --- MetricoolPDFImporter ---


class TestMetricoolPDFImporter:
    @pytest.fixture
    def mock_store(self):
        store = MagicMock()
        store.append_event = MagicMock(return_value="test-event-id")
        store.query_by_site = MagicMock(return_value=[])
        return store

    @pytest.fixture
    def importer_with_fixture(self, mock_store):
        """Importer with mocked extractor that returns fixture text."""
        importer = MetricoolPDFImporter(event_store=mock_store, accounts=ACCOUNTS)
        sample = SAMPLE_TEXT.read_text(encoding="utf-8")
        importer.extractor = MagicMock()
        importer.extractor.extract_cached = MagicMock(return_value=sample)
        return importer

    def test_creates_performance_events(
        self, importer_with_fixture, mock_store, tmp_path
    ):
        pdf = tmp_path / "report.pdf"
        pdf.write_bytes(b"fake")
        counts = importer_with_fixture.import_pdf(str(pdf))
        assert counts["posts"] > 0
        # Check events were appended
        calls = mock_store.append_event.call_args_list
        perf_events = [
            c for c in calls if c[0][0].event_type == EventType.PERFORMANCE_UPDATED
        ]
        assert len(perf_events) >= 3  # At least 3 TikTok + 2 YouTube

    def test_appends_platform_to_site_id(
        self, importer_with_fixture, mock_store, tmp_path
    ):
        pdf = tmp_path / "report.pdf"
        pdf.write_bytes(b"fake")
        importer_with_fixture.import_pdf(str(pdf), site_id="brand")
        calls = mock_store.append_event.call_args_list
        site_ids = {c[0][0].site_id for c in calls}
        assert any("brand_tiktok" in s for s in site_ids)

    def test_dry_run_skips_store(self, tmp_path):
        pdf = tmp_path / "report.pdf"
        pdf.write_bytes(b"fake")
        importer = MetricoolPDFImporter(accounts=ACCOUNTS)
        sample = SAMPLE_TEXT.read_text(encoding="utf-8")
        importer.extractor = MagicMock()
        importer.extractor.extract_cached = MagicMock(return_value=sample)
        counts = importer.import_pdf(str(pdf), dry_run=True)
        assert counts["posts"] > 0

    def test_file_not_found(self):
        importer = MetricoolPDFImporter()
        with pytest.raises(FileNotFoundError):
            importer.import_pdf("/nonexistent.pdf")

    def test_deduplication(self, importer_with_fixture, mock_store, tmp_path):
        """Import twice — second run should skip already-imported posts."""
        pdf = tmp_path / "report.pdf"
        pdf.write_bytes(b"fake")

        # First import
        _counts1 = importer_with_fixture.import_pdf(str(pdf))

        # Simulate existing events in store for second import
        existing = []
        for call in mock_store.append_event.call_args_list:
            existing.append(call[0][0])
        mock_store.query_by_site.return_value = existing

        # Fresh parser for second import
        importer2 = MetricoolPDFImporter(event_store=mock_store, accounts=ACCOUNTS)
        sample = SAMPLE_TEXT.read_text(encoding="utf-8")
        importer2.extractor = MagicMock()
        importer2.extractor.extract_cached = MagicMock(return_value=sample)

        mock_store.append_event.reset_mock()
        counts2 = importer2.import_pdf(str(pdf))
        assert counts2["skipped"] > 0

    def test_community_events_created(
        self, importer_with_fixture, mock_store, tmp_path
    ):
        pdf = tmp_path / "report.pdf"
        pdf.write_bytes(b"fake")
        counts = importer_with_fixture.import_pdf(str(pdf))
        assert counts["community"] >= 1
        calls = mock_store.append_event.call_args_list
        comm_events = [
            c for c in calls if c[0][0].event_type == EventType.COMMUNITY_UPDATED
        ]
        assert len(comm_events) >= 1


# --- account configuration ---


class TestAccounts:
    def test_roles_are_the_documented_set(self):
        assert set(ACCOUNT_ROLES) == {
            "facebook_youtube",
            "instagram_threads",
            "twitter",
            "tiktok",
            "pinterest",
        }

    def test_validate_accounts_rejects_unknown_role(self):
        with pytest.raises(ValueError, match="unknown account role"):
            validate_accounts({"Sample Account": "myspace"})

    def test_validate_accounts_returns_plain_dict(self):
        assert validate_accounts({"A": "tiktok"}) == {"A": "tiktok"}

    def test_load_accounts_reads_yaml(self, tmp_path):
        path = tmp_path / "accounts.yaml"
        path.write_text(
            "Sample Account: tiktok\nSampleBrand: facebook_youtube\n", encoding="utf-8"
        )
        assert load_accounts(path) == ACCOUNTS

    def test_load_accounts_reads_json(self, tmp_path):
        path = tmp_path / "accounts.json"
        path.write_text('{"Sample Account": "tiktok"}', encoding="utf-8")
        assert load_accounts(path) == {"Sample Account": "tiktok"}

    def test_load_accounts_rejects_a_non_mapping(self, tmp_path):
        path = tmp_path / "accounts.yaml"
        path.write_text("- Sample Account\n", encoding="utf-8")
        with pytest.raises(ValueError, match="mapping"):
            load_accounts(path)

    def test_parser_without_accounts_attributes_nothing(self):
        text = SAMPLE_TEXT.read_text(encoding="utf-8")
        result = MetricoolPDFParser().parse(text)
        assert result["posts"] == []
        assert result["community"] == {}

    def test_import_without_accounts_is_refused(self, tmp_path):
        pdf = tmp_path / "report.pdf"
        pdf.write_bytes(b"fake")
        importer = MetricoolPDFImporter()
        importer.extractor = MagicMock()
        importer.extractor.extract_cached = MagicMock(return_value="")
        with pytest.raises(ValueError, match="no accounts configured"):
            importer.import_pdf(str(pdf), dry_run=True)


# --- command line ---


class TestMain:
    def _run(self, monkeypatch, *argv):
        monkeypatch.setattr("sys.argv", ["metricool_pdf_import", *argv])
        main()

    def test_site_id_is_required(self, monkeypatch, tmp_path):
        with pytest.raises(SystemExit) as exc:
            self._run(monkeypatch, str(tmp_path / "r.pdf"), "--dry-run")
        assert exc.value.code == 2

    def test_accounts_file_is_required(self, monkeypatch, tmp_path, capsys):
        with pytest.raises(SystemExit) as exc:
            self._run(
                monkeypatch, str(tmp_path / "r.pdf"), "--site-id", "brand", "--dry-run"
            )
        assert exc.value.code == 2
        assert "--accounts is required" in capsys.readouterr().err

    def test_dry_run_with_accounts_reports_counts(self, monkeypatch, tmp_path, capsys):
        report = tmp_path / "report.extracted.txt"
        report.write_text(SAMPLE_TEXT.read_text(encoding="utf-8"), encoding="utf-8")
        accounts = tmp_path / "accounts.yaml"
        accounts.write_text(
            "Sample Account: tiktok\nSampleBrand: facebook_youtube\n", encoding="utf-8"
        )
        self._run(
            monkeypatch,
            str(report),
            "--site-id",
            "brand",
            "--accounts",
            str(accounts),
            "--dry-run",
        )
        assert "5 posts" in capsys.readouterr().out

    def test_default_store_is_under_the_data_home(self, monkeypatch, tmp_path):
        home = tmp_path / "data-home"
        monkeypatch.setenv("SEO_MANAGER_DATA_HOME", str(home))
        report = tmp_path / "report.extracted.txt"
        report.write_text(SAMPLE_TEXT.read_text(encoding="utf-8"), encoding="utf-8")
        accounts = tmp_path / "accounts.yaml"
        accounts.write_text("Sample Account: tiktok\n", encoding="utf-8")
        self._run(
            monkeypatch,
            str(report),
            "--site-id",
            "brand",
            "--accounts",
            str(accounts),
        )
        assert (home / "seo_events").is_dir()

    def test_store_path_overrides_the_default(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SEO_MANAGER_DATA_HOME", str(tmp_path / "unused"))
        report = tmp_path / "report.extracted.txt"
        report.write_text(SAMPLE_TEXT.read_text(encoding="utf-8"), encoding="utf-8")
        accounts = tmp_path / "accounts.yaml"
        accounts.write_text("Sample Account: tiktok\n", encoding="utf-8")
        store = tmp_path / "chosen"
        self._run(
            monkeypatch,
            str(report),
            "--site-id",
            "brand",
            "--accounts",
            str(accounts),
            "--store-path",
            str(store),
        )
        assert store.is_dir()
        assert not (tmp_path / "unused").exists()
