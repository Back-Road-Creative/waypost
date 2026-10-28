"""Offline site audit as a collection source: ``collect run`` ingests it."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from seo_manager import cli
from seo_manager.analytics.continuous_optimization_daemon import SyncOutcome
from seo_manager.collection.contracts import (
    MAX_IDENTIFIER_LEN,
    MAX_PROVENANCE_LEN,
    Checkpoint,
)
from seo_manager.collection.cycle import run_cycle
from seo_manager.collection.site_audit import SiteAuditAdapter, SiteBuildError
from seo_manager.collection.sources import build_adapters
from seo_manager.collection.store import CollectionStore
from seo_manager.site_audit.audit import SEVERITY

BASE = "https://example.com/"
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def html(body: str = "", head: str = "") -> str:
    return f"<html><head>{head}</head><body>{body}</body></html>"


@pytest.fixture
def build(tmp_path: Path) -> Path:
    """One page with one broken link and no canonical; sitemap and robots present."""
    root = tmp_path / "public"
    write(root, "index.html", html('<a href="/gone/">x</a>'))
    write(
        root,
        "sitemap.xml",
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<url><loc>{BASE}</loc></url></urlset>",
    )
    write(root, "robots.txt", f"User-agent: *\nSitemap: {BASE}sitemap.xml\n")
    return root


def collect(build: Path, checkpoint: Checkpoint | None = None, **kw):
    adapter = SiteAuditAdapter(build, BASE, **kw)
    return adapter.collect("gms", checkpoint, NOW)


def test_identity():
    a = SiteAuditAdapter(Path("x"), BASE)
    assert (a.source, a.stream) == ("site_audit", "build")


def test_zero_counts_are_present_for_every_check(build: Path):
    obs, _, outcome = collect(build)
    assert outcome is SyncOutcome.FRESH
    counts = {o.metric: o.value for o in obs if o.subject == "site"}
    for check in SEVERITY:
        assert counts[f"findings_{check}"] == (
            1 if check in ("broken_internal_link", "canonical_missing") else 0
        ), check
    assert counts["pages"] == 1
    assert counts["pages_excluded"] == 0
    assert counts["external_links"] == 0


def test_one_row_per_finding_with_severity_in_provenance(build: Path):
    obs, _, _ = collect(build)
    rows = {o.metric: o for o in obs if o.subject != "site"}
    assert set(rows) == {"broken_internal_link", "canonical_missing"}
    broken = rows["broken_internal_link"]
    assert broken.value == 1
    assert broken.subject == "page:index.html|/gone/"
    assert "severity=error" in (broken.provenance or "")
    assert "severity=warning" in (rows["canonical_missing"].provenance or "")
    assert all(o.window_start == NOW and o.window_end > NOW for o in obs)
    assert all(o.source == "site_audit" and o.reported_at == NOW for o in obs)


def test_long_subject_and_detail_stay_within_limits(build: Path):
    long_name = "a" * 150
    write(build, f"{long_name}/{long_name}/index.html", html("<p>x</p>"))
    obs, _, _ = collect(build)
    page_rows = [o for o in obs if o.subject != "site"]
    assert page_rows
    assert all(len(o.subject) <= MAX_IDENTIFIER_LEN for o in page_rows)
    assert any(o.subject.startswith("page:sha256:") for o in page_rows)
    assert all(len(o.provenance or "") <= MAX_PROVENANCE_LEN for o in obs)

    def fake_audit(*a, **k):
        from seo_manager.site_audit.audit import AuditReport, Finding

        f = Finding("noindex", "warning", "p.html", "d" * 2000)
        return AuditReport(BASE, 1, 0, 0, (f,))

    obs, _, _ = collect(build, audit=fake_audit)
    row = next(o for o in obs if o.subject != "site")
    assert len(row.provenance or "") <= MAX_PROVENANCE_LEN
    assert (row.provenance or "").startswith("severity=warning")


def test_unchanged_build_is_empty_and_keeps_the_checkpoint(build: Path):
    _, cp, _ = collect(build)
    assert cp is not None and len(cp.cursor) <= 2000
    obs, cp2, outcome = collect(build, cp)
    assert (list(obs), outcome) == ([], SyncOutcome.EMPTY)
    assert cp2 == cp


def test_changed_build_is_fresh_with_a_new_cursor(build: Path):
    _, cp, _ = collect(build)
    write(build, "about/index.html", html("<p>new</p>"))
    obs, cp2, outcome = collect(build, cp)
    assert outcome is SyncOutcome.FRESH and list(obs)
    assert cp2 is not None and cp2.cursor != cp.cursor


def test_exclude_change_is_a_change(build: Path):
    _, cp, _ = collect(build)
    _, _, outcome = collect(build, cp, exclude=("gone/",))
    assert outcome is SyncOutcome.FRESH


def test_missing_build_dir_raises_a_typed_error(tmp_path: Path):
    with pytest.raises(SiteBuildError):
        collect(tmp_path / "nope")


def test_cycle_records_a_missing_build_as_a_source_failure(tmp_path: Path):
    store = CollectionStore(tmp_path / "c.db")
    store.init()
    runs = run_cycle(store, "gms", [SiteAuditAdapter(tmp_path / "nope", BASE)], now=NOW)
    assert runs[0].status == "failed" and runs[0].error_class == "invalid_request"
    assert store.checkpoint("gms", "site_audit", "build") is None


def test_cycle_stores_then_skips_an_unchanged_build(build: Path, tmp_path: Path):
    store = CollectionStore(tmp_path / "c.db")
    store.init()
    adapter = SiteAuditAdapter(build, BASE)
    first = run_cycle(store, "gms", [adapter], now=NOW)[0]
    assert first.status == "collected" and first.added > len(SEVERITY)
    second = run_cycle(store, "gms", [adapter], now=NOW + timedelta(days=1))[0]
    assert second.outcome is SyncOutcome.EMPTY and second.added == 0


def test_build_adapters_skip_reasons(build: Path):
    def reasons(**kw):
        _, skipped = build_adapters(
            youtube_credentials=None,
            video_mapping=None,
            search_console_site=None,
            **kw,
        )
        return {n.source: n.reason for n in skipped}

    assert reasons(site_build_dir=None, site_base_url=None)["site_audit"] == (
        "not_configured:site_build_dir"
    )
    assert reasons(site_build_dir=build, site_base_url=None)["site_audit"] == (
        "not_configured:site_base_url"
    )
    adapters, skipped = build_adapters(
        youtube_credentials=None,
        video_mapping=None,
        search_console_site=None,
        site_build_dir=build,
        site_base_url=BASE,
        site_exclude=("drafts/",),
    )
    assert [a.source for a in adapters] == ["site_audit"]
    assert "site_audit" not in {n.source for n in skipped}


def test_bad_base_url_is_build_failed_not_a_crash(build: Path):
    _, skipped = build_adapters(
        youtube_credentials=None,
        video_mapping=None,
        search_console_site=None,
        site_build_dir=build,
        site_base_url="not-a-url",
    )
    assert {n.source: n.reason for n in skipped}["site_audit"] == (
        "client_build_failed:ValueError"
    )


def test_cli_runs_only_the_site_source(build: Path, tmp_path: Path, capsys):
    db = tmp_path / "c.db"
    rc = cli.main(
        [
            "collect", "run", "--tenant", "gms", "--db", str(db), "--json",
            "--site-build-dir", str(build), "--site-base-url", BASE,
            "--site-exclude", "drafts/",
        ]
    )  # fmt: skip
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    by_source = {r["source"]: r for r in out}
    assert by_source["site_audit"]["status"] == "collected"
    assert by_source["site_audit"]["added"] > len(SEVERITY)
    for s in ("youtube_analytics_search_terms", "youtube_reach", "search_console"):
        assert by_source[s]["status"] == "not_run"
    assert CollectionStore(db).health("gms", "site_audit") is not None
