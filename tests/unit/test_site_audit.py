"""Offline audit of a built site directory: one positive and one negative per check."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from seo_manager.site_audit.audit import audit_build

BASE = "https://example.com/"


def page(body: str = "", head: str = "") -> str:
    return f"<html><head>{head}</head><body>{body}</body></html>"


def canon(url: str) -> str:
    return f'<link rel="canonical" href="{url}">'


def sitemap(*urls: str) -> str:
    locs = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
    return f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{locs}</urlset>'


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def site(tmp_path: Path) -> Path:
    """A clean one-page site: self-canonical, in the sitemap, robots present."""
    write(tmp_path, "index.html", page("<p>home</p>", canon(BASE)))
    write(tmp_path, "sitemap.xml", sitemap(BASE))
    write(tmp_path, "robots.txt", f"User-agent: *\nSitemap: {BASE}sitemap.xml\n")
    return tmp_path


def checks(root: Path, **kw) -> list[str]:
    return [f.check for f in audit_build(root, base_url=BASE, **kw).findings]


def test_clean_site_has_no_findings(site: Path) -> None:
    report = audit_build(site, base_url=BASE)
    assert report.findings == ()
    assert report.pages == 1
    assert report.counts_by_check == {}


@pytest.mark.parametrize("bad", ["ftp://x.com/", "example.com", "https:///", ""])
def test_bad_base_url_raises(site: Path, bad: str) -> None:
    with pytest.raises(ValueError):
        audit_build(site, base_url=bad)


# ---- broken links -------------------------------------------------------


def test_broken_links_flagged(site: Path) -> None:
    write(
        site,
        "a/index.html",
        page(
            '<a href="missing.html">x</a><a href="/nope/">y</a>'
            f'<a href="{BASE}gone">z</a><img src="/i.png">'
            '<script src="/s.js"></script>',
            '<link rel="stylesheet" href="/c.css">' + canon(BASE + "a/"),
        ),
    )
    write(site, "sitemap.xml", sitemap(BASE, BASE + "a/"))
    report = audit_build(site, base_url=BASE)
    broken = [f for f in report.findings if f.check == "broken_internal_link"]
    assert {f.target for f in broken} == {
        "missing.html",
        "/nope/",
        f"{BASE}gone",
        "/i.png",
        "/s.js",
        "/c.css",
    }
    assert all(f.severity == "error" and f.page == "a/index.html" for f in broken)


def test_resolvable_links_not_flagged(site: Path) -> None:
    write(site, "a/index.html", page("", canon(BASE + "a/")))
    write(site, "b/x.html", page("", canon(BASE + "b/x.html")))
    write(site, "about.html", page("", canon(BASE + "about.html")))
    write(site, "img/café pic.png", "x")
    write(site, "s.css", "x")
    write(
        site,
        "c/index.html",
        page(
            '<a href="../a/">dir</a><a href="/b/x.html">file</a><a href="/about">noext</a>'
            f'<a href="{BASE}a/#frag">abs</a><a href="/a/?q=1#f">q</a>'
            '<a href="#top">frag</a><a href="mailto:a@b.c">m</a><a href="tel:1">t</a>'
            '<a href="javascript:void(0)">j</a><img src="data:image/png;base64,AA">'
            '<img src="/img/caf%C3%A9%20pic.png">'
            '<a href="https://other.org/x">ext</a><a href="//cdn.other.org/y">ext2</a>',
            canon(BASE + "c/") + '<link rel="stylesheet" href="/s.css">',
        ),
    )
    report = audit_build(site, base_url=BASE)
    assert "broken_internal_link" not in [f.check for f in report.findings]
    assert report.external_links == 2


# ---- noindex ------------------------------------------------------------


def test_noindex_flagged_and_sitemap_conflict(site: Path) -> None:
    write(
        site,
        "p/index.html",
        page("", '<meta name="ROBOTS" content="Index, NoIndex">' + canon(BASE + "p/")),
    )
    write(site, "sitemap.xml", sitemap(BASE, BASE + "p/"))
    got = checks(site)
    assert got.count("noindex") == 1
    assert got.count("noindex_in_sitemap") == 1
    assert "not_in_sitemap" not in got


def test_googlebot_noindex_and_noindex_outside_sitemap(site: Path) -> None:
    write(
        site,
        "p/index.html",
        page("", '<meta name="googlebot" content="noindex">' + canon(BASE + "p/")),
    )
    report = audit_build(site, base_url=BASE)
    by = {f.check: f for f in report.findings}
    assert by["noindex"].severity == "warning"
    assert "noindex_in_sitemap" not in by
    assert "not_in_sitemap" not in by  # noindex pages are not expected in the sitemap


def test_follow_only_robots_is_clean(site: Path) -> None:
    write(
        site,
        "index.html",
        page("", '<meta name="robots" content="index, follow">' + canon(BASE)),
    )
    assert checks(site) == []


# ---- canonical ----------------------------------------------------------


def canonical_checks(site: Path, head: str, rel: str = "q/index.html") -> list[str]:
    write(site, rel, page("", head))
    write(site, "sitemap.xml", sitemap(BASE, BASE + "q/"))
    return checks(site)


def test_canonical_missing(site: Path) -> None:
    assert canonical_checks(site, "") == ["canonical_missing"]


def test_canonical_multiple(site: Path) -> None:
    got = canonical_checks(site, canon(BASE + "q/") + canon(BASE))
    assert got == ["canonical_multiple"]


def test_canonical_relative(site: Path) -> None:
    assert canonical_checks(site, canon("/q/")) == ["canonical_relative"]


def test_canonical_offsite(site: Path) -> None:
    assert canonical_checks(site, canon("https://other.org/q/")) == [
        "canonical_offsite"
    ]


def test_canonical_mismatch_is_info(site: Path) -> None:
    write(site, "q/index.html", page("", canon(BASE)))
    write(site, "sitemap.xml", sitemap(BASE, BASE + "q/"))
    report = audit_build(site, base_url=BASE)
    assert [(f.check, f.severity) for f in report.findings] == [
        ("canonical_mismatch", "info")
    ]


def test_canonical_target_missing(site: Path) -> None:
    assert canonical_checks(site, canon(BASE + "ghost/")) == [
        "canonical_target_missing"
    ]


def test_self_canonical_with_fragment_and_query_is_clean(site: Path) -> None:
    assert canonical_checks(site, canon(BASE + "q/?utm=1#x")) == []


# ---- sitemap ------------------------------------------------------------


def test_sitemap_missing(site: Path) -> None:
    (site / "sitemap.xml").unlink()
    got = checks(site)
    assert "sitemap_missing" in got
    assert "not_in_sitemap" not in got
    assert "robots_sitemap_missing" in got


def test_sitemap_invalid(site: Path) -> None:
    write(site, "sitemap.xml", "<urlset><url>")
    report = audit_build(site, base_url=BASE)
    invalid = [f for f in report.findings if f.check == "sitemap_invalid"]
    assert len(invalid) == 1 and invalid[0].severity == "error"
    assert "ParseError" in invalid[0].detail


def test_sitemap_url_missing_file_and_offsite(site: Path) -> None:
    write(site, "sitemap.xml", sitemap(BASE, BASE + "gone/", "https://other.org/z"))
    report = audit_build(site, base_url=BASE)
    by = {f.check: f for f in report.findings}
    assert by["sitemap_url_missing_file"].target == BASE + "gone/"
    assert by["sitemap_url_missing_file"].severity == "error"
    assert by["sitemap_url_offsite"].target == "https://other.org/z"
    assert by["sitemap_url_offsite"].severity == "warning"


def test_sitemap_index_followed(site: Path) -> None:
    write(
        site,
        "sitemap.xml",
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<sitemap><loc>{BASE}s1.xml</loc></sitemap>"
        f"<sitemap><loc>{BASE}absent.xml</loc></sitemap></sitemapindex>",
    )
    write(site, "s1.xml", sitemap(BASE, BASE + "gone/"))
    report = audit_build(site, base_url=BASE)
    got = {(f.check, f.page) for f in report.findings}
    assert ("sitemap_url_missing_file", "s1.xml") in got
    assert not any(
        c == "not_in_sitemap" for c, _ in got
    )  # index page is listed in s1.xml


def test_sitemap_index_child_cap(site: Path) -> None:
    kids = "".join(f"<sitemap><loc>{BASE}c{i}.xml</loc></sitemap>" for i in range(60))
    write(site, "sitemap.xml", f"<sitemapindex>{kids}</sitemapindex>")
    for i in range(60):
        write(site, f"c{i}.xml", sitemap(BASE + f"only{i}/"))
    report = audit_build(site, base_url=BASE)
    gone = [f for f in report.findings if f.check == "sitemap_url_missing_file"]
    assert len(gone) == 50


def test_not_in_sitemap_and_404_exempt(site: Path) -> None:
    write(site, "new/index.html", page("", canon(BASE + "new/")))
    write(site, "404.html", page("", canon(BASE + "404.html")))
    write(site, "404/index.html", page("", canon(BASE + "404/")))
    report = audit_build(site, base_url=BASE)
    missing = [f for f in report.findings if f.check == "not_in_sitemap"]
    assert [(f.page, f.severity) for f in missing] == [("new/index.html", "info")]


# ---- schema -------------------------------------------------------------


def ld(payload: str) -> str:
    return f'<script type="application/ld+json">{payload}</script>'


def schema_checks(site: Path, payload: str) -> list[str]:
    write(site, "index.html", page(ld(payload), canon(BASE)))
    return checks(site)


def test_schema_valid_is_clean(site: Path) -> None:
    ok = '{"@context":"https://schema.org","@type":"Article","headline":"h"}'
    assert schema_checks(site, ok) == []
    graph = '{"@context":"https://schema.org","@graph":[{"@type":"WebSite"},{"@type":"Person"}]}'
    assert schema_checks(site, graph) == []


def test_schema_invalid_json(site: Path) -> None:
    assert schema_checks(site, "{not json") == ["schema_invalid_json"]


def test_schema_missing_type_in_graph_and_list(site: Path) -> None:
    graph = (
        '{"@context":"https://schema.org","@graph":[{"@type":"WebSite"},{"name":"x"}]}'
    )
    assert schema_checks(site, graph) == ["schema_missing_type"]
    lst = '[{"@context":"c","@type":"A"},{"@context":"c","name":"n"}]'
    assert schema_checks(site, lst) == ["schema_missing_type"]
    assert schema_checks(site, '{"@context":"c","name":"n"}') == ["schema_missing_type"]


def test_schema_missing_context(site: Path) -> None:
    assert schema_checks(site, '{"@type":"Article"}') == ["schema_missing_context"]


# ---- robots.txt ---------------------------------------------------------


def test_robots_missing(site: Path) -> None:
    (site / "robots.txt").unlink()
    report = audit_build(site, base_url=BASE)
    assert [(f.check, f.severity) for f in report.findings] == [
        ("robots_txt_missing", "warning")
    ]


def test_robots_sitemap_line_bad_and_offsite_ignored(site: Path) -> None:
    write(
        site,
        "robots.txt",
        f"Sitemap: {BASE}nope.xml\nsitemap: https://other.org/s.xml\n",
    )
    report = audit_build(site, base_url=BASE)
    bad = [f for f in report.findings if f.check == "robots_sitemap_missing"]
    assert [f.target for f in bad] == [BASE + "nope.xml"]
    assert bad[0].severity == "error"


# ---- exclude, determinism ----------------------------------------------


def test_exclude_prefix_skips_and_counts(site: Path) -> None:
    write(site, "drafts/a/index.html", page('<a href="/nope">x</a>'))
    write(site, "drafts/b.html", page(""))
    assert "broken_internal_link" in checks(site)
    report = audit_build(site, base_url=BASE, exclude=["drafts/"])
    assert report.excluded == 2
    assert report.pages == 1
    assert report.findings == ()


def test_report_json_deterministic_and_sorted(site: Path) -> None:
    write(site, "z/index.html", page('<a href="/b">x</a><a href="/a">y</a>'))
    write(site, "m/index.html", page(""))
    first = json.dumps(audit_build(site, base_url=BASE).to_dict())
    second = json.dumps(audit_build(site, base_url=BASE).to_dict())
    assert first == second
    data = json.loads(first)
    keys = [(f["check"], f["page"], f["target"] or "") for f in data["findings"]]
    assert keys == sorted(keys)
    assert data["counts_by_check"]["broken_internal_link"] == 2
    assert set(data) >= {
        "base_url",
        "pages",
        "excluded",
        "external_links",
        "findings",
        "counts_by_check",
    }


# ---- CLI ----------------------------------------------------------------


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "seo_manager.site_audit", *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=Path(__file__).resolve().parents[2],
    )


def test_cli_clean_exit_zero(site: Path) -> None:
    r = run_cli("--build-dir", str(site), "--base-url", BASE)
    assert r.returncode == 0, r.stderr


def test_cli_fail_on_levels(site: Path) -> None:
    (site / "robots.txt").unlink()  # one warning
    base = ["--build-dir", str(site), "--base-url", BASE]
    assert run_cli(*base).returncode == 0
    assert run_cli(*base, "--fail-on", "warning").returncode == 1
    write(site, "sitemap.xml", sitemap(BASE, BASE + "gone/"))  # an error
    r = run_cli(*base)
    assert r.returncode == 1
    assert "error sitemap_url_missing_file" in r.stdout
    assert run_cli(*base, "--fail-on", "never").returncode == 0


def test_cli_json_output(site: Path) -> None:
    (site / "robots.txt").unlink()
    r = run_cli("--build-dir", str(site), "--base-url", BASE, "--json")
    assert r.returncode == 0
    assert json.loads(r.stdout)["counts_by_check"] == {"robots_txt_missing": 1}


def test_cli_exclude_option(site: Path) -> None:
    write(site, "d/index.html", page('<a href="/nope">x</a>'))
    base = ["--build-dir", str(site), "--base-url", BASE]
    assert run_cli(*base).returncode == 1
    assert run_cli(*base, "--exclude", "d/").returncode == 0


def test_cli_usage_errors_exit_two(site: Path, tmp_path: Path) -> None:
    assert (
        run_cli("--build-dir", str(tmp_path / "nope"), "--base-url", BASE).returncode
        == 2
    )
    assert run_cli("--build-dir", str(site), "--base-url", "not a url").returncode == 2
    assert run_cli("--build-dir", str(site)).returncode == 2
