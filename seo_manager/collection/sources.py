"""Build the collection adapters a ``collect run`` can use from what is configured.

A source that lacks an input is reported as not run (``not_configured:<input>``),
and one whose client could not be built as ``client_build_failed:<ExceptionType>``.
Neither is ever written to the store or shown as a success. Token contents are never
read here beyond handing the file to the client builder, and never printed.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from seo_manager.collection.cycle import SourceAdapter
from seo_manager.collection.search_console import SearchConsoleAdapter
from seo_manager.collection.site_audit import SiteAuditAdapter
from seo_manager.collection.youtube_reach import YouTubeReachAdapter
from seo_manager.collection.youtube_search_terms import YouTubeSearchTermsAdapter

SEARCH_CONSOLE_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"


#: Marks a ``build_adapters`` caller that did not mention the site audit at all, so
#: it is not listed as "not run"; ``collect run`` passes ``None`` for an unset flag.
_OMITTED: Any = object()


@dataclass(frozen=True)
class SourceNotRun:
    """A source that was not attempted, and why."""

    source: str
    reason: str


class ClientFactory(Protocol):
    """Builds API clients from an authorized-user token file."""

    def analytics(self, credentials: Path) -> Any: ...

    def reach(self, credentials: Path) -> tuple[Any, Callable[[str], str]]: ...

    def search_console(self, credentials: Path) -> Any: ...


class GoogleClients:
    """The real factory; imports Google libraries only when a client is built."""

    def analytics(self, credentials: Path) -> Any:
        from seo_manager.analytics.youtube_sync import YouTubeAnalyticsSync

        return YouTubeAnalyticsSync(str(credentials))

    def reach(self, credentials: Path) -> tuple[Any, Callable[[str], str]]:
        from seo_manager.analytics.youtube_reach_probe import _build

        return _build(json.loads(credentials.read_text(encoding="utf-8")))

    def search_console(self, credentials: Path) -> Any:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        info = json.loads(credentials.read_text(encoding="utf-8"))
        creds = Credentials.from_authorized_user_info(info, [SEARCH_CONSOLE_SCOPE])
        return build("searchconsole", "v1", credentials=creds, cache_discovery=False)


def _video_ids(mapping: Path) -> list[str]:
    data = json.loads(mapping.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("video mapping must be a JSON object")
    return sorted(str(k) for k in data)


def build_adapters(
    *,
    youtube_credentials: Path | None,
    video_mapping: Path | None,
    search_console_site: str | None,
    clients: ClientFactory | None = None,
    site_build_dir: Path | None = _OMITTED,
    site_base_url: str | None = _OMITTED,
    site_exclude: Sequence[str] = (),
) -> tuple[list[SourceAdapter], list[SourceNotRun]]:
    """Return (adapters that could be built, sources not run with a reason)."""
    factory = clients or GoogleClients()
    adapters: list[SourceAdapter] = []
    skipped: list[SourceNotRun] = []

    def attempt(source: str, build: Callable[[], SourceAdapter]) -> None:
        try:
            adapters.append(build())
        except Exception as exc:  # the type only: a message could echo a token
            skipped.append(
                SourceNotRun(source, f"client_build_failed:{type(exc).__name__}")
            )

    creds = youtube_credentials

    # Source names come from each adapter module's SOURCE constant.
    terms_src = YouTubeSearchTermsAdapter.source
    reach_src = YouTubeReachAdapter.source
    sc_src = SearchConsoleAdapter.source

    if creds is None:
        skipped.append(SourceNotRun(terms_src, "not_configured:youtube_credentials"))
    elif video_mapping is None:
        skipped.append(SourceNotRun(terms_src, "not_configured:video_mapping"))
    else:

        def terms() -> SourceAdapter:
            ids = _video_ids(video_mapping)
            if not ids:
                raise _Empty
            return YouTubeSearchTermsAdapter(factory.analytics(creds), ids)

        try:
            adapters.append(terms())
        except _Empty:
            skipped.append(
                SourceNotRun(terms_src, "not_configured:video_mapping_empty")
            )
        except Exception as exc:
            skipped.append(
                SourceNotRun(terms_src, f"client_build_failed:{type(exc).__name__}")
            )

    if creds is None:
        skipped.append(SourceNotRun(reach_src, "not_configured:youtube_credentials"))
    else:
        attempt(reach_src, lambda: YouTubeReachAdapter(*factory.reach(creds)))

    if creds is None:
        skipped.append(SourceNotRun(sc_src, "not_configured:youtube_credentials"))
    elif not search_console_site:
        skipped.append(SourceNotRun(sc_src, "not_configured:search_console_site"))
    else:
        attempt(
            sc_src,
            lambda: SearchConsoleAdapter(
                factory.search_console(creds), search_console_site
            ),
        )

    if site_build_dir is not _OMITTED or site_base_url is not _OMITTED:
        site_src = SiteAuditAdapter.source
        if not site_build_dir or site_build_dir is _OMITTED:
            skipped.append(SourceNotRun(site_src, "not_configured:site_build_dir"))
        elif not site_base_url or site_base_url is _OMITTED:
            skipped.append(SourceNotRun(site_src, "not_configured:site_base_url"))
        else:
            attempt(
                site_src,
                lambda: _site_adapter(site_build_dir, site_base_url, site_exclude),
            )
    return adapters, skipped


def _site_adapter(
    build_dir: Path, base_url: str, exclude: Sequence[str]
) -> SiteAuditAdapter:
    from urllib.parse import urlsplit

    parts = urlsplit(base_url)  # the audit's own check, so a bad URL fails here
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("site_base_url must be http(s) with a host")
    return SiteAuditAdapter(build_dir, base_url, exclude)


class _Empty(Exception):
    """The video mapping holds no videos."""
