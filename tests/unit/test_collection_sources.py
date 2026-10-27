"""build_adapters: what is configured runs; what is not is named, never faked."""

from __future__ import annotations

import json

import pytest

from seo_manager.collection import sources
from seo_manager.collection.sources import SourceNotRun, build_adapters

TERMS = "youtube_analytics_search_terms"
REACH = "youtube_reach"
SC = "search_console"


class FakeClients:
    def __init__(self, fail=()):
        self.fail = set(fail)
        self.calls = []

    def analytics(self, credentials):
        self.calls.append(("analytics", credentials))
        if "analytics" in self.fail:
            raise PermissionError("secret-token-text")
        return object()

    def reach(self, credentials):
        self.calls.append(("reach", credentials))
        if "reach" in self.fail:
            raise ValueError("secret-token-text")
        return object(), lambda url: ""

    def search_console(self, credentials):
        self.calls.append(("search_console", credentials))
        if "search_console" in self.fail:
            raise OSError("secret-token-text")
        return object()


@pytest.fixture
def creds(tmp_path):
    p = tmp_path / "token.json"
    p.write_text("{}")
    return p


@pytest.fixture
def mapping(tmp_path):
    p = tmp_path / "map.json"
    p.write_text(json.dumps({"vb": "c2", "va": "c1"}))
    return p


def reasons(skipped):
    return {n.source: n.reason for n in skipped}


def test_nothing_configured_builds_nothing_and_builds_no_client():
    fake = FakeClients()
    adapters, skipped = build_adapters(
        youtube_credentials=None, video_mapping=None, search_console_site=None,
        clients=fake,
    )  # fmt: skip
    assert adapters == [] and fake.calls == []
    assert reasons(skipped) == {
        TERMS: "not_configured:youtube_credentials",
        REACH: "not_configured:youtube_credentials",
        SC: "not_configured:youtube_credentials",
    }


def test_credentials_only_runs_reach_and_names_the_rest(creds):
    adapters, skipped = build_adapters(
        youtube_credentials=creds, video_mapping=None, search_console_site=None,
        clients=FakeClients(),
    )  # fmt: skip
    assert [a.source for a in adapters] == [REACH]
    assert reasons(skipped) == {
        TERMS: "not_configured:video_mapping",
        SC: "not_configured:search_console_site",
    }


def test_everything_configured_builds_all_three(creds, mapping):
    adapters, skipped = build_adapters(
        youtube_credentials=creds, video_mapping=mapping,
        search_console_site="sc-domain:example.com", clients=FakeClients(),
    )  # fmt: skip
    assert {a.source for a in adapters} == {TERMS, REACH, SC}
    assert skipped == []


def test_video_ids_are_the_sorted_mapping_keys(creds, mapping):
    adapters, _ = build_adapters(
        youtube_credentials=creds, video_mapping=mapping, search_console_site=None,
        clients=FakeClients(),
    )  # fmt: skip
    terms = next(a for a in adapters if a.source == TERMS)
    assert terms._video_ids == ("va", "vb")


def test_empty_mapping_is_not_configured(creds, tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text("{}")
    adapters, skipped = build_adapters(
        youtube_credentials=creds, video_mapping=empty, search_console_site=None,
        clients=FakeClients(),
    )  # fmt: skip
    assert TERMS not in {a.source for a in adapters}
    assert reasons(skipped)[TERMS] == "not_configured:video_mapping_empty"


def test_client_failure_is_build_failed_not_unconfigured_and_leaks_nothing(
    creds, mapping
):
    adapters, skipped = build_adapters(
        youtube_credentials=creds, video_mapping=mapping,
        search_console_site="https://example.com/",
        clients=FakeClients(fail={"analytics", "reach"}),
    )  # fmt: skip
    assert [a.source for a in adapters] == [SC]
    assert reasons(skipped) == {
        TERMS: "client_build_failed:PermissionError",
        REACH: "client_build_failed:ValueError",
    }
    assert all("secret" not in n.reason for n in skipped)


def test_unreadable_mapping_is_build_failed(creds, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    _, skipped = build_adapters(
        youtube_credentials=creds, video_mapping=bad, search_console_site=None,
        clients=FakeClients(),
    )  # fmt: skip
    assert reasons(skipped)[TERMS] == "client_build_failed:JSONDecodeError"


def test_source_not_run_is_a_value():
    assert SourceNotRun("a", "b") == SourceNotRun("a", "b")
    assert hasattr(sources, "ClientFactory")
