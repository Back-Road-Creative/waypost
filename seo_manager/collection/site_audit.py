"""Offline site-audit evidence for the collection store, read-only.

The adapter runs ``audit_build`` over a built site directory and reports what it
found. It reads files and never writes the store; the cycle does the writing.

Unlike an API that omits rows it did not return, a completed audit that finds no
``broken_internal_link`` has *measured* zero of them, so every check in
``SEVERITY`` is reported as a site-level count, ``0`` included. A build that
could not be audited (missing or unreadable directory) raises instead and
produces no observation at all.

Metrics, all in this source's ``build`` stream:

* subject ``site``: ``findings_<check>`` for every known check, plus ``pages``,
  ``pages_excluded`` and ``external_links``.
* subject ``page:<page>[|<target>]``: one row per finding group, metric = the
  check, value = how many findings share that page and target (normally 1).
  Provenance carries ``severity=`` and the first finding's detail.

The window is the audit instant, ``[now, now + 1s]`` (a window must have a
positive length). The checkpoint cursor is a fingerprint of the build's content
and of the audit settings, so an unchanged build is skipped as ``EMPTY``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from pathlib import Path

from seo_manager.analytics.continuous_optimization_daemon import SyncOutcome
from seo_manager.collection.contracts import (
    MAX_IDENTIFIER_LEN,
    MAX_PROVENANCE_LEN,
    Checkpoint,
    Observation,
)
from seo_manager.site_audit.audit import SEVERITY, AuditReport, audit_build
from seo_manager.transport import InvalidRequestError

SOURCE = "site_audit"
STREAM = "build"
#: Files whose bytes the audit reads; every other file counts by name and size.
_CONTENT_SUFFIXES = frozenset({".html", ".xml", ".txt"})
_SUBJECT_ROOM = 200


class SiteBuildError(InvalidRequestError):
    """The build directory is missing or cannot be read."""


def _fingerprint(root: Path, base_url: str, exclude: Sequence[str]) -> str:
    """Hash file names, sizes and the bytes of html/xml/txt, plus the settings.

    Content, not mtime: a deterministic rebuild rewrites every file's mtime but
    not its bytes, and must not count as a change. Hugo/Astro output holds
    assets that the audit only needs to exist, so those are hashed by size.
    """
    h = hashlib.sha256()
    h.update(f"{base_url}\0{sorted(exclude)!r}\0".encode())
    try:
        files = sorted(p for p in root.rglob("*") if p.is_file())
        for path in files:
            rel = path.relative_to(root).as_posix()
            if path.suffix.lower() in _CONTENT_SUFFIXES:
                body = hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                body = str(path.stat().st_size)
            h.update(f"{rel}\0{body}\0".encode())
    except OSError as exc:
        raise SiteBuildError(
            f"cannot read build directory: {type(exc).__name__}"
        ) from exc
    return h.hexdigest()


def _subject(page: str, target: str | None) -> tuple[str, str | None]:
    subject = f"page:{page}" if target is None else f"page:{page}|{target}"
    if len(subject) <= MAX_IDENTIFIER_LEN:
        return subject, None
    digest = hashlib.sha256(subject.encode()).hexdigest()[:32]
    return f"page:sha256:{digest}", subject


def _provenance(severity: str, detail: str, raw: str | None) -> str:
    text = f"severity={severity}"
    if raw is not None:
        text += ";subject=" + raw[:_SUBJECT_ROOM]
    room = MAX_PROVENANCE_LEN - len(text) - len(";detail=")
    if detail and room > 0:
        text += ";detail=" + detail[:room]
    return text


class SiteAuditAdapter:
    """Collects the offline audit of one built site for one tenant."""

    source = SOURCE
    stream = STREAM

    def __init__(
        self,
        build_dir: Path,
        base_url: str,
        exclude: Sequence[str] = (),
        audit: Callable[..., AuditReport] = audit_build,
    ) -> None:
        self._dir = Path(build_dir)
        self._base_url = base_url
        self._exclude = tuple(exclude)
        self._audit = audit

    def collect(
        self, tenant: str, checkpoint: Checkpoint | None, now: datetime
    ) -> tuple[list[Observation], Checkpoint | None, SyncOutcome]:
        if not self._dir.is_dir():
            raise SiteBuildError("build directory does not exist")
        cursor = _fingerprint(self._dir, self._base_url, self._exclude)
        if checkpoint is not None and checkpoint.cursor == cursor:
            return [], checkpoint, SyncOutcome.EMPTY
        report = self._audit(self._dir, base_url=self._base_url, exclude=self._exclude)
        end = now + timedelta(seconds=1)

        def obs(
            subject: str, metric: str, value: int, prov: str | None = None
        ) -> Observation:
            return Observation(
                tenant, SOURCE, subject, metric, now, end, value, now, prov
            )

        counts = report.counts_by_check
        out = [obs("site", f"findings_{c}", counts.get(c, 0)) for c in SEVERITY]
        out += [
            obs("site", "pages", report.pages),
            obs("site", "pages_excluded", report.excluded),
            obs("site", "external_links", report.external_links),
        ]
        groups: dict[tuple[str, str, str | None], list] = {}
        for f in report.findings:
            groups.setdefault((f.check, f.page, f.target), []).append(f)
        for (check, page, target), found in groups.items():
            subject, raw = _subject(page, target)
            first = found[0]
            out.append(
                obs(
                    subject,
                    check,
                    len(found),
                    _provenance(first.severity, first.detail, raw),
                )
            )
        return out, Checkpoint(tenant, SOURCE, STREAM, cursor, now), SyncOutcome.FRESH
