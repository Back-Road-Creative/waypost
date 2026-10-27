"""Read-only ingestion of YouTube Reporting API reach reports (plan S2, part 1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from seo_manager.analytics.continuous_optimization_daemon import SyncOutcome
from seo_manager.collection.contracts import Checkpoint
from seo_manager.collection.store import CollectionStore
from seo_manager.collection.youtube_reach import (
    BASIC_SOURCE,
    COMBINED_SOURCE,
    STREAM,
    YouTubeReachAdapter,
)
from seo_manager.transport import AuthError, QuotaExhaustedError

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
BASIC_T = "channel_reach_basic_a1"
COMB_T = "channel_reach_combined_a1"
BASIC_CSV = (
    "date,channel_id,video_id,video_thumbnail_impressions,"
    "video_thumbnail_impressions_ctr\n"
    "20261024,UC1,v1,120,0.05\n"
)
COMB_CSV = (
    "date,channel_id,video_id,traffic_source_type,traffic_source_detail,"
    "operating_system,device_type,video_thumbnail_impressions,"
    "video_thumbnail_impressions_ctr\n"
    "20261024,UC1,v1,3,,0,1,100,0.04\n"
    "20261024,UC1,v1,9,,0,1,20,0.1\n"
)


class _Call:
    def __init__(self, result):
        self._result = result

    def execute(self):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _Reports:
    def __init__(self, by_job, calls):
        self.by_job, self.calls = by_job, calls

    def list(self, jobId, pageToken=None, createdAfter=None):
        self.calls.append((jobId, createdAfter))
        return _Call({"reports": self.by_job.get(jobId, [])})


class _Jobs:
    def __init__(self, jobs, reports):
        self.jobs, self.created, self.report_calls = jobs, [], []
        self._reports = _Reports(reports, self.report_calls)

    def list(self, includeSystemManaged=False, pageToken=None):
        return _Call({"jobs": self.jobs})

    def create(self, body):
        self.created.append(body)
        return _Call({"id": "x"})

    def reports(self):
        return self._reports


class FakeReporting:
    def __init__(self, jobs, reports):
        self._jobs = _Jobs(list(jobs), reports)

    def jobs(self):
        return self._jobs


def _job(jid, type_id):
    return {"id": jid, "reportTypeId": type_id}


def _rep(rid, created, url=None):
    return {
        "id": rid,
        "createTime": created,
        "downloadUrl": url or rid,
        "startTime": "2026-10-24T07:00:00Z",
        "endTime": "2026-10-25T07:00:00Z",
    }


def _adapter(reporting, files, **kw):
    seen = []

    def download(url):
        seen.append(url)
        value = files[url]
        if isinstance(value, Exception):
            raise value
        return value

    adapter = YouTubeReachAdapter(reporting, download, read=lambda fn: fn(), **kw)
    adapter.downloads = seen
    return adapter


def _one(jobs=None, reports=None, files=None, **kw):
    jobs = jobs or [_job("j1", BASIC_T)]
    reports = reports or {"j1": [_rep("r1", "2026-10-25T08:00:00Z")]}
    files = files or {"r1": BASIC_CSV}
    return _adapter(FakeReporting(jobs, reports), files, **kw)


def test_basic_rows_become_observations():
    obs, cp, outcome = _one().collect("brc", None, NOW)
    assert outcome is SyncOutcome.FRESH
    assert {o.metric for o in obs} == {
        "video_thumbnail_impressions",
        "video_thumbnail_impressions_ctr",
    }
    imp = next(o for o in obs if o.metric == "video_thumbnail_impressions")
    assert (imp.tenant, imp.source, imp.subject) == ("brc", BASIC_SOURCE, "v1")
    assert imp.value == 120
    # one Pacific day, PDT: 2026-10-24 00:00 PDT = 07:00Z
    assert imp.window_start == datetime(2026, 10, 24, 7, tzinfo=UTC)
    assert imp.window_end == datetime(2026, 10, 25, 7, tzinfo=UTC)
    assert imp.reported_at == datetime(2026, 10, 25, 8, tzinfo=UTC)
    assert imp.provenance == f"report=r1;type={BASIC_T}"
    assert (cp.stream, cp.cursor.startswith("2026-10-25T08:00:00")) == (STREAM, True)


def test_pacific_day_is_dst_safe():
    csv_text = BASIC_CSV.replace("20261024", "20261101")  # fall-back day, 25 h
    obs, _, _ = _one(files={"r1": csv_text}).collect("brc", None, NOW)
    span = obs[0].window_end - obs[0].window_start
    assert span == timedelta(hours=25)


def test_ctr_provenance_says_unit_unverified_and_value_is_raw():
    obs, _, _ = _one().collect("brc", None, NOW)
    ctr = next(o for o in obs if o.metric.endswith("_ctr"))
    assert ctr.value == 0.05
    assert ctr.provenance == f"rate_unit=unverified;report=r1;type={BASIC_T}"


def test_only_reach_jobs_are_read_and_no_job_is_created():
    reporting = FakeReporting(
        [_job("j1", BASIC_T), _job("jx", "channel_basic_a2")],
        {
            "j1": [_rep("r1", "2026-10-25T08:00:00Z")],
            "jx": [_rep("rx", "2026-10-25T09:00:00Z")],
        },
    )
    adapter = _adapter(reporting, {"r1": BASIC_CSV, "rx": BASIC_CSV})
    adapter.collect("brc", None, NOW)
    assert adapter.downloads == ["r1"]
    assert reporting.jobs().created == []


def test_no_jobs_is_empty_and_creates_nothing():
    reporting = FakeReporting([], {})
    cp = Checkpoint("brc", "youtube_reach", STREAM, "2026-10-01T00:00:00Z", NOW)
    obs, out_cp, outcome = _adapter(reporting, {}).collect("brc", cp, NOW)
    assert (list(obs), outcome) == ([], SyncOutcome.EMPTY)
    assert out_cp == cp
    assert reporting.jobs().created == []


def test_checkpoint_passes_created_after_and_filters_client_side():
    reporting = FakeReporting(
        [_job("j1", BASIC_T)],
        {
            "j1": [
                _rep("old", "2026-10-24T08:00:00Z"),
                _rep("r1", "2026-10-25T08:00:00Z"),
            ]
        },
    )
    cp = Checkpoint("brc", "youtube_reach", STREAM, "2026-10-24T08:00:00+00:00", NOW)
    adapter = _adapter(reporting, {"old": BASIC_CSV, "r1": BASIC_CSV})
    _, out_cp, _ = adapter.collect("brc", cp, NOW)
    assert adapter.downloads == ["r1"]
    assert reporting.jobs().report_calls[0][1] is not None
    assert out_cp.cursor.startswith("2026-10-25T08:00:00")


def test_no_checkpoint_sends_no_created_after():
    reporting = FakeReporting([_job("j1", BASIC_T)], {"j1": []})
    _adapter(reporting, {}).collect("brc", None, NOW)
    assert reporting.jobs().report_calls == [("j1", None)]


def test_restart_from_cursor_processes_nothing_twice():
    adapter = _one()
    _, cp, _ = adapter.collect("brc", None, NOW)
    obs, cp2, outcome = adapter.collect("brc", cp, NOW)
    assert (list(obs), outcome, cp2) == ([], SyncOutcome.EMPTY, cp)
    assert adapter.downloads == ["r1"]


def test_oldest_first_and_max_reports_is_partial():
    reports = {
        "j1": [
            _rep("c", "2026-10-27T08:00:00Z"),
            _rep("a", "2026-10-25T08:00:00Z"),
            _rep("b", "2026-10-26T08:00:00Z"),
        ]
    }
    adapter = _one(reports=reports, files={k: BASIC_CSV for k in "abc"}, max_reports=2)
    _, cp, outcome = adapter.collect("brc", None, NOW)
    assert adapter.downloads == ["a", "b"]
    assert outcome is SyncOutcome.PARTIAL
    assert cp.cursor.startswith("2026-10-26T08:00:00")
    _, cp2, outcome2 = adapter.collect("brc", cp, NOW)
    assert adapter.downloads == ["a", "b", "c"]
    assert outcome2 is SyncOutcome.FRESH
    assert cp2.cursor.startswith("2026-10-27T08:00:00")


def test_header_order_does_not_matter():
    shuffled = (
        "video_thumbnail_impressions_ctr,video_id,date,channel_id,"
        "video_thumbnail_impressions\n0.05,v1,20261024,UC1,120\n"
    )
    obs, _, _ = _one(files={"r1": shuffled}).collect("brc", None, NOW)
    by = {o.metric: o.value for o in obs}
    assert by == {
        "video_thumbnail_impressions": 120,
        "video_thumbnail_impressions_ctr": 0.05,
    }


def test_empty_and_malformed_cells_are_none_not_a_crash():
    text = (
        "date,channel_id,video_id,video_thumbnail_impressions,"
        "video_thumbnail_impressions_ctr\n20261024,UC1,v1,,abc\n"
    )
    obs, _, outcome = _one(files={"r1": text}).collect("brc", None, NOW)
    assert outcome is SyncOutcome.FRESH
    assert [o.value for o in obs] == [None, None]


def test_missing_column_skips_that_report_only():
    bad = "date,video_id,video_thumbnail_impressions\n20261024,v1,5\n"
    reports = {
        "j1": [_rep("bad", "2026-10-25T08:00:00Z"), _rep("ok", "2026-10-26T08:00:00Z")]
    }
    obs, cp, outcome = _one(
        reports=reports, files={"bad": bad, "ok": BASIC_CSV}
    ).collect("brc", None, NOW)
    assert outcome is SyncOutcome.PARTIAL
    assert all(o.provenance and "report=ok" in o.provenance for o in obs) and obs
    assert cp.cursor.startswith("2026-10-26T08:00:00")


def test_transient_download_failure_holds_cursor_back():
    from seo_manager.transport import TransientError

    reports = {
        "j1": [_rep("a", "2026-10-25T08:00:00Z"), _rep("b", "2026-10-26T08:00:00Z")]
    }
    files = {"a": TransientError("boom"), "b": BASIC_CSV}
    obs, cp, outcome = _one(reports=reports, files=files).collect("brc", None, NOW)
    assert outcome is SyncOutcome.PARTIAL
    assert obs  # b still yielded
    assert cp is None  # nothing safely processed before the failure


def test_same_time_report_held_by_transient_is_retried_next_run():
    from seo_manager.transport import TransientError

    t = "2026-10-25T08:00:00Z"
    jobs = [_job("j1", BASIC_T), _job("j2", COMB_T)]
    reports = {"j1": [_rep("a", t)], "j2": [_rep("b", t)]}
    files = {"a": BASIC_CSV, "b": TransientError("boom")}
    adapter = _one(jobs=jobs, reports=reports, files=files)
    _, cp, outcome = adapter.collect("brc", None, NOW)
    assert outcome is SyncOutcome.PARTIAL
    files["b"] = COMB_CSV
    adapter.collect("brc", cp, NOW)
    assert adapter.downloads.count("b") == 2


def test_cap_never_splits_reports_sharing_a_create_time():
    t = "2026-10-25T08:00:00Z"
    jobs = [_job("j1", BASIC_T), _job("j2", COMB_T)]
    reports = {"j1": [_rep("a", t)], "j2": [_rep("b", t)]}
    files = {"a": BASIC_CSV, "b": COMB_CSV}
    adapter = _one(jobs=jobs, reports=reports, files=files, max_reports=1)
    _, cp, outcome = adapter.collect("brc", None, NOW)
    assert sorted(adapter.downloads) == ["a", "b"]
    assert outcome is SyncOutcome.FRESH
    assert cp.cursor.startswith("2026-10-25T08:00:00")


def test_basic_and_combined_are_separate_sources_never_summed():
    reporting = FakeReporting(
        [_job("jb", BASIC_T), _job("jc", COMB_T)],
        {
            "jb": [_rep("rb", "2026-10-25T08:00:00Z")],
            "jc": [_rep("rc", "2026-10-25T09:00:00Z")],
        },
    )
    obs, _, _ = _adapter(reporting, {"rb": BASIC_CSV, "rc": COMB_CSV}).collect(
        "brc", None, NOW
    )
    assert {o.source for o in obs} == {BASIC_SOURCE, COMBINED_SOURCE}
    assert BASIC_SOURCE != COMBINED_SOURCE
    comb = [o for o in obs if o.source == COMBINED_SOURCE]
    assert {o.subject for o in comb} == {"v1|3||0|1", "v1|9||0|1"}
    # no total anywhere: the bare video subject exists only under basic
    assert [o.subject for o in obs if o.subject == "v1"] and all(
        o.source == BASIC_SOURCE for o in obs if o.subject == "v1"
    )
    assert sum(1 for o in obs if o.subject == "v1") == 2


def test_overlong_combined_subject_is_digested_with_raw_in_provenance():
    long_detail = "d" * 300
    text = COMB_CSV.splitlines()[0] + f"\n20261024,UC1,v1,3,{long_detail},0,1,1,0.1\n"
    reporting = FakeReporting(
        [_job("jc", COMB_T)], {"jc": [_rep("rc", "2026-10-25T09:00:00Z")]}
    )
    obs, _, _ = _adapter(reporting, {"rc": text}).collect("brc", None, NOW)
    assert obs[0].subject.startswith("sha256:") and len(obs[0].subject) == 39
    assert "subject_raw=v1|3|" in obs[0].provenance
    assert len(obs[0].provenance) <= 500


def test_quota_and_auth_propagate():
    for exc in (
        QuotaExhaustedError("q", next_eligible=NOW),
        AuthError("a"),
    ):
        adapter = _one(files={"r1": exc})
        with pytest.raises(type(exc)):
            adapter.collect("brc", None, NOW)


def test_tenant_is_stamped_on_everything():
    obs, cp, _ = _one().collect("acme", None, NOW)
    assert {o.tenant for o in obs} == {"acme"} and cp.tenant == "acme"


def test_end_to_end_store_revisions_and_idempotent_rerun(tmp_path):
    store = CollectionStore(tmp_path / "c.db")
    store.init()
    reports = {"j1": [_rep("r1", "2026-10-25T08:00:00Z")]}
    adapter = _one(reports=reports)
    obs, cp, _ = adapter.collect("brc", None, NOW)
    assert store.write_batch("brc", obs, cp) == 2
    # a rerun from the stored checkpoint adds nothing
    obs2, cp2, _ = adapter.collect(
        "brc", store.checkpoint("brc", cp.source, cp.stream), NOW
    )
    assert store.write_batch("brc", obs2, cp2) == 0
    # YouTube re-issues the same window with a changed value
    revised = BASIC_CSV.replace("120,0.05", "130,0.05")
    reports["j1"].append(_rep("r2", "2026-10-26T08:00:00Z"))
    adapter2 = _one(reports=reports, files={"r1": BASIC_CSV, "r2": revised})
    obs3, cp3, _ = adapter2.collect(
        "brc", store.checkpoint("brc", cp.source, cp.stream), NOW
    )
    assert (
        store.write_batch("brc", obs3, cp3) == 2
    )  # impressions changed; ctr too (new report)
    latest = {o.metric: o.value for o in store.latest("brc", BASIC_SOURCE, "v1")}
    assert latest["video_thumbnail_impressions"] == 130
    history = [
        o.value
        for o in store.revisions("brc", BASIC_SOURCE, "v1")
        if o.metric == "video_thumbnail_impressions"
    ]
    assert sorted(history) == [120, 130]
