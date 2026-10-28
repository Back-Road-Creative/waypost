"""Deterministic, offline audit of a built site directory.

Stdlib only; no network. See docs/site-audit.md for what is and is not checked.
"""

from __future__ import annotations

import json
import posixpath
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urldefrag, urljoin, urlsplit

MAX_CHILD_SITEMAPS = 50

SEVERITY = {
    "broken_internal_link": "error",
    "noindex": "warning",
    "noindex_in_sitemap": "error",
    "canonical_missing": "warning",
    "canonical_multiple": "error",
    "canonical_relative": "error",
    "canonical_offsite": "warning",
    "canonical_mismatch": "info",
    "canonical_target_missing": "error",
    "sitemap_missing": "error",
    "sitemap_invalid": "error",
    "sitemap_url_missing_file": "error",
    "sitemap_url_offsite": "warning",
    "not_in_sitemap": "info",
    "schema_invalid_json": "error",
    "schema_missing_type": "warning",
    "schema_missing_context": "warning",
    "robots_txt_missing": "warning",
    "robots_sitemap_missing": "error",
}


@dataclass(frozen=True)
class Finding:
    check: str
    severity: str
    page: str
    detail: str
    target: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "check": self.check,
            "severity": self.severity,
            "page": self.page,
            "target": self.target,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class AuditReport:
    base_url: str
    pages: int
    excluded: int
    external_links: int
    findings: tuple[Finding, ...]

    @property
    def counts_by_check(self) -> dict[str, int]:
        counts = Counter(f.check for f in self.findings)
        return {k: counts[k] for k in sorted(counts)}

    def to_dict(self) -> dict:
        return {
            "base_url": self.base_url,
            "pages": self.pages,
            "excluded": self.excluded,
            "external_links": self.external_links,
            "counts_by_check": self.counts_by_check,
            "findings": [f.to_dict() for f in self.findings],
        }


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.refs: list[str] = []
        self.canonicals: list[str] = []
        self.noindex = False
        self.ldjson: list[str] = []
        self._ld: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "a" and a.get("href"):
            self.refs.append(a["href"])
        elif tag == "img" and a.get("src"):
            self.refs.append(a["src"])
        elif tag == "link":
            rels = a.get("rel", "").lower().split()
            if "canonical" in rels:
                self.canonicals.append(a.get("href", "").strip())
            elif "stylesheet" in rels and a.get("href"):
                self.refs.append(a["href"])
        elif tag == "meta":
            if (
                a.get("name", "").lower() in ("robots", "googlebot")
                and "noindex" in a.get("content", "").lower()
            ):
                self.noindex = True
        elif tag == "script":
            if a.get("src"):
                self.refs.append(a["src"])
            if a.get("type", "").lower().strip() == "application/ld+json":
                self._ld = []

    def handle_data(self, data: str) -> None:
        if self._ld is not None:
            self._ld.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._ld is not None:
            self.ldjson.append("".join(self._ld))
            self._ld = None


class _Site:
    def __init__(self, root: Path, base_url: str) -> None:
        parts = urlsplit(base_url)
        if (
            parts.scheme not in ("http", "https")
            or not parts.netloc
            or not parts.hostname
        ):
            raise ValueError(f"base_url must be http(s) with a host: {base_url!r}")
        path = parts.path if parts.path.endswith("/") else parts.path + "/"
        self.base = f"{parts.scheme}://{parts.netloc}{path}"
        self.host = parts.netloc.lower()
        self.base_path = path
        self.root = root
        self.files = {
            p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()
        }

    def page_url(self, rel: str) -> str:
        if rel == "index.html":
            return self.base
        if rel.endswith("/index.html"):
            return self.base + rel[: -len("index.html")]
        return self.base + rel

    def is_onsite(self, url: str) -> bool:
        return urlsplit(url).netloc.lower() == self.host

    def exists(self, url: str) -> bool:
        path = unquote(urlsplit(url).path)
        if path.startswith(self.base_path):
            path = path[len(self.base_path) :]
        rel = posixpath.normpath(path.lstrip("/")) if path.strip("/") else ""
        if rel == ".." or rel.startswith("../"):
            return False
        if rel == "":
            return "index.html" in self.files
        return any(c in self.files for c in (rel, f"{rel}/index.html", f"{rel}.html"))


def _is_404(rel: str) -> bool:
    return rel in ("404.html", "404/index.html")


def _sitemap_locs(root: ET.Element, tag: str) -> list[str]:
    out = []
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == tag:
            for loc in el:
                if loc.tag.rsplit("}", 1)[-1] == "loc" and (loc.text or "").strip():
                    out.append((loc.text or "").strip())
    return out


def _canonical_findings(
    site: _Site, rel: str, url: str, canonicals: list[str]
) -> list[tuple[str, str, str | None]]:
    if not canonicals:
        return [("canonical_missing", "no rel=canonical link", None)]
    if len(canonicals) > 1:
        return [("canonical_multiple", f"{len(canonicals)} canonical links", None)]
    href = canonicals[0]
    parts = urlsplit(href)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return [
            ("canonical_relative", "canonical is not an absolute http(s) URL", href)
        ]
    if not site.is_onsite(href):
        return [("canonical_offsite", "canonical points to another host", href)]
    if not site.exists(href):
        return [
            (
                "canonical_target_missing",
                "canonical target has no file in the build",
                href,
            )
        ]
    if urldefrag(href)[0].split("?")[0] != url:
        return [("canonical_mismatch", "canonical points to a different page", href)]
    return []


def _schema_findings(blobs: list[str]) -> list[tuple[str, str, str | None]]:
    out: list[tuple[str, str, str | None]] = []
    for blob in blobs:
        try:
            data = json.loads(blob)
        except ValueError as exc:
            out.append(
                ("schema_invalid_json", f"{type(exc).__name__} in ld+json block", None)
            )
            continue
        for top in data if isinstance(data, list) else [data]:
            if not isinstance(top, dict):
                continue
            if "@context" not in top:
                out.append(
                    ("schema_missing_context", "top-level object has no @context", None)
                )
            graph = top.get("@graph")
            items = graph if isinstance(graph, list) else [top]
            for item in items:
                if isinstance(item, dict) and "@type" not in item:
                    out.append(("schema_missing_type", "object has no @type", None))
    return out


def audit_build(
    build_dir: Path, *, base_url: str, exclude: Sequence[str] = ()
) -> AuditReport:
    """Audit every ``*.html`` under ``build_dir``; see docs/site-audit.md."""
    site = _Site(Path(build_dir), base_url)
    findings: list[Finding] = []

    def add(check: str, page: str, detail: str, target: str | None = None) -> None:
        findings.append(Finding(check, SEVERITY[check], page, detail, target))

    # Sitemap (index followed, bounded).
    sitemap_urls: set[str] = set()
    have_sitemap = "sitemap.xml" in site.files
    if not have_sitemap:
        add("sitemap_missing", "sitemap.xml", "no sitemap.xml at the build root")
    else:
        queue, seen = ["sitemap.xml"], 0
        while queue:
            name = queue.pop(0)
            try:
                tree = ET.fromstring((site.root / name).read_bytes())
            except ET.ParseError as exc:
                add(
                    "sitemap_invalid",
                    name,
                    f"{type(exc).__name__}: sitemap is not well-formed XML",
                )
                continue
            for child in (
                _sitemap_locs(tree, "sitemap")
                if tree.tag.endswith("sitemapindex")
                else []
            ):
                path = (
                    unquote(urlsplit(child).path)[len(site.base_path) :]
                    if site.is_onsite(child)
                    else ""
                )
                if path in site.files and seen < MAX_CHILD_SITEMAPS:
                    seen += 1
                    queue.append(path)
            if tree.tag.endswith("sitemapindex"):
                continue
            for loc in _sitemap_locs(tree, "url"):
                sitemap_urls.add(loc)
                if not site.is_onsite(loc):
                    add(
                        "sitemap_url_offsite",
                        name,
                        "sitemap URL is on another host",
                        loc,
                    )
                elif not site.exists(loc):
                    add(
                        "sitemap_url_missing_file",
                        name,
                        "sitemap URL has no file in the build",
                        loc,
                    )

    # Pages.
    excluded = external = 0
    pages = 0
    prefixes = tuple(exclude)
    for rel in sorted(f for f in site.files if f.endswith(".html")):
        if prefixes and rel.startswith(prefixes):
            excluded += 1
            continue
        pages += 1
        url = site.page_url(rel)
        parser = _PageParser()
        parser.feed((site.root / rel).read_text(encoding="utf-8", errors="replace"))
        parser.close()

        for ref in parser.refs:
            joined = urljoin(url, ref.strip())
            parts = urlsplit(joined)
            if parts.scheme not in ("http", "https"):
                continue
            if not site.is_onsite(joined):
                external += 1
            elif not site.exists(joined):
                add(
                    "broken_internal_link",
                    rel,
                    "link target has no file in the build",
                    ref,
                )

        for check, detail, target in _canonical_findings(
            site, rel, url, parser.canonicals
        ):
            add(check, rel, detail, target)
        for check, detail, target in _schema_findings(parser.ldjson):
            add(check, rel, detail, target)

        if parser.noindex:
            add("noindex", rel, "meta robots contains noindex")
            if url in sitemap_urls:
                add(
                    "noindex_in_sitemap",
                    rel,
                    "noindex page is listed in the sitemap",
                    url,
                )
        elif have_sitemap and url not in sitemap_urls and not _is_404(rel):
            add("not_in_sitemap", rel, "indexable page has no sitemap entry", url)

    # robots.txt
    if "robots.txt" not in site.files:
        add("robots_txt_missing", "robots.txt", "no robots.txt at the build root")
    else:
        text = (site.root / "robots.txt").read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            key, _, value = line.partition(":")
            value = value.strip()
            if (
                key.strip().lower() == "sitemap"
                and value
                and site.is_onsite(value)
                and not site.exists(value)
            ):
                add(
                    "robots_sitemap_missing",
                    "robots.txt",
                    "Sitemap line points to a missing file",
                    value,
                )

    findings.sort(key=lambda f: (f.check, f.page, f.target or "", f.detail))
    return AuditReport(site.base, pages, excluded, external, tuple(findings))
