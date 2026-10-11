"""Tests for IntelDB wiring in the SEOManager.

Runs against a small stand-in ``intel_db.py`` written into a temp directory and
loaded by file path the way production loads it. The stand-in implements the
slice of the IntelDB API the manager uses on top of sqlite. Nothing touches
os.environ, sys.path or sys.modules at import time.
"""

import pytest

from seo_manager.core import manager
from seo_manager.core.manager import _get_intel_db, _log_seo_to_intel_db

_STAND_IN_SOURCE = """\
import sqlite3


class IntelDB:
    def __init__(self, db_path):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            \"\"\"
            CREATE TABLE IF NOT EXISTS scrape_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT, source_name TEXT, status TEXT,
                items_found INTEGER, items_imported INTEGER
            );
            CREATE TABLE IF NOT EXISTS news_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT, summary TEXT, article_links TEXT, source_count INTEGER
            );
            \"\"\"
        )

    def start_scrape_run(self, platform, source_name=None):
        cur = self.conn.execute(
            "INSERT INTO scrape_runs (platform, source_name, status) "
            "VALUES (?, ?, 'running')",
            (platform, source_name),
        )
        self.conn.commit()
        return cur.lastrowid

    def finish_scrape_run(self, run_id, items_found=0, items_imported=0):
        self.conn.execute(
            "UPDATE scrape_runs SET status='success', items_found=?, "
            "items_imported=? WHERE id=?",
            (items_found, items_imported, run_id),
        )
        self.conn.commit()

    def upsert_news_item(self, date, summary, article_links, source_count):
        self.conn.execute(
            "INSERT INTO news_items (date, summary, article_links, source_count) "
            "VALUES (?, ?, ?, ?)",
            (date, summary, ",".join(article_links), source_count),
        )
        self.conn.commit()

    def get_recent_news(self, days):
        return self.conn.execute("SELECT * FROM news_items ORDER BY id").fetchall()

    def close(self):
        self.conn.close()
"""

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def stand_in_intel_db_dir(monkeypatch, tmp_path):
    """Point the loader at the stand-in module; INTEL_DB_PATH unset by default."""
    scripts = tmp_path / "intel_scripts"
    scripts.mkdir()
    (scripts / "intel_db.py").write_text(_STAND_IN_SOURCE, encoding="utf-8")
    monkeypatch.setattr(manager, "_INTEL_SCRIPTS_DIR", str(scripts))
    monkeypatch.delenv("INTEL_DB_PATH", raising=False)


@pytest.fixture()
def intel_db(tmp_path):
    db = _get_intel_db(str(tmp_path / "intel.db"))
    assert db is not None
    yield db
    db.close()


# ---------------------------------------------------------------------------
# _get_intel_db
# ---------------------------------------------------------------------------


def test_get_intel_db_valid_path(tmp_path):
    db = _get_intel_db(str(tmp_path / "test.db"))
    assert db is not None
    db.close()


def test_get_intel_db_bad_path_returns_none():
    result = _get_intel_db("/nonexistent/dir/intel.db")
    assert result is None


# ---------------------------------------------------------------------------
# _log_seo_to_intel_db — directly
# ---------------------------------------------------------------------------


def test_log_seo_writes_scrape_run(intel_db, monkeypatch):
    monkeypatch.setenv("INTEL_DB_PATH", intel_db.db_path)

    _log_seo_to_intel_db(
        content_id="ep-ai-safety-youtube",
        platform="youtube",
        title="Understanding AI Safety in 2026",
        tag_count=12,
        generated_at="2026-02-26T15:00:00+00:00",
    )

    rows = intel_db.conn.execute(
        "SELECT * FROM scrape_runs WHERE platform='seo-youtube'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "success"
    assert rows[0]["items_imported"] == 1
    assert rows[0]["source_name"] == "ep-ai-safety-youtube"


def test_log_seo_writes_news_item(intel_db, monkeypatch):
    monkeypatch.setenv("INTEL_DB_PATH", intel_db.db_path)

    _log_seo_to_intel_db(
        content_id="blog-gpt5-review",
        platform="blog",
        title="GPT-5 Review: What Changed?",
        tag_count=8,
        generated_at="2026-02-26T10:00:00+00:00",
    )

    news = intel_db.get_recent_news(7)
    assert len(news) == 1
    assert "GPT-5 Review" in news[0]["summary"]
    assert "blog" in news[0]["summary"]
    assert news[0]["source_count"] == 8
    assert news[0]["date"] == "2026-02-26"


def test_log_seo_no_db_path_is_noop():
    """When INTEL_DB_PATH is not set, nothing is written — no error."""
    _log_seo_to_intel_db(
        content_id="noop-content",
        platform="twitter",
        title="Should not be written",
        tag_count=3,
        generated_at="2026-02-26T00:00:00+00:00",
    )


def test_log_seo_platform_key_includes_platform(intel_db, monkeypatch):
    """Platform key is 'seo-{platform}' so YouTube and Blog are separate."""
    monkeypatch.setenv("INTEL_DB_PATH", intel_db.db_path)

    _log_seo_to_intel_db(
        content_id="content-a",
        platform="twitter",
        title="Tweet about AI",
        tag_count=5,
        generated_at="2026-02-26T00:00:00+00:00",
    )

    rows = intel_db.conn.execute("SELECT platform FROM scrape_runs").fetchall()
    assert rows[0]["platform"] == "seo-twitter"


def test_log_seo_date_extracted_from_generated_at(intel_db, monkeypatch):
    monkeypatch.setenv("INTEL_DB_PATH", intel_db.db_path)

    _log_seo_to_intel_db(
        content_id="date-test",
        platform="blog",
        title="Date extraction test",
        tag_count=2,
        generated_at="2026-01-15T08:30:00+00:00",
    )

    news = intel_db.get_recent_news(365)
    assert news[0]["date"] == "2026-01-15"
