"""The YouTube Reporting API reach probe (plan B2a, 2026-10-26).

`TemplateObservation` needs thumbnail impressions and click rate by traffic
source, and the Analytics API v2 does not expose them (youtube_sync.py,
ANALYTICS_CTR_LIMITATION). The probe asks the Reporting API whether the
channel's token can reach a reach report, and says which step stops it.
"""

import io
import json

from googleapiclient.errors import HttpError

from seo_manager.analytics import youtube_reach_probe as probe_mod
from seo_manager.analytics.youtube_reach_probe import format_lines, probe


class _Call:
    def __init__(self, result):
        self._result = result

    def execute(self):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _Reports:
    def __init__(self, by_job):
        self.by_job = by_job

    def list(self, jobId, pageToken=None):
        return _Call({"reports": self.by_job.get(jobId, [])})


class _Jobs:
    def __init__(self, jobs, reports):
        self.jobs = jobs
        self._reports = _Reports(reports)
        self.created = []

    def list(self, includeSystemManaged=False, pageToken=None):
        return _Call({"jobs": self.jobs})

    def create(self, body):
        self.created.append(body)
        return _Call({"id": "new-job", **body})

    def reports(self):
        return self._reports


class _ReportTypes:
    def __init__(self, types):
        self.types = types

    def list(self, includeSystemManaged=False, pageToken=None):
        return _Call({"reportTypes": self.types})


class FakeReporting:
    def __init__(self, types, jobs=(), reports=None):
        self._types = _ReportTypes(list(types))
        self._jobs = _Jobs(list(jobs), reports or {})

    def reportTypes(self):
        return self._types

    def jobs(self):
        return self._jobs


REACH = {"id": "channel_reach_combined_a1", "name": "Reach combined"}
OTHER = {"id": "channel_basic_a2", "name": "User activity"}
CSV_WITH_SOURCE = (
    "date,channel_id,video_id,traffic_source_type,video_thumbnail_impressions,video_thumbnail_impressions_ctr\n"
    "20261024,UC1,v1,3,120,0.05\n"
    "20261024,UC1,v2,9,80,0.0125\n"
)


def _no_download(url):
    raise AssertionError(f"unexpected download {url}")


def test_no_reach_report_type_stops_at_step_one():
    result = probe(FakeReporting([OTHER]), _no_download)
    assert result["verdict"] == "no-reach-report-type"
    assert result["reach_types"] == []


def test_reach_type_without_job_says_so_and_creates_nothing_by_default():
    reporting = FakeReporting([OTHER, REACH])
    result = probe(reporting, _no_download)
    assert result["verdict"] == "no-job"
    assert result["reach_types"] == ["channel_reach_combined_a1"]
    assert reporting.jobs().created == []


def test_create_job_is_opt_in():
    reporting = FakeReporting([REACH])
    result = probe(reporting, _no_download, create_job=True)
    assert result["verdict"] == "job-created"
    assert reporting.jobs().created == [
        {"reportTypeId": "channel_reach_combined_a1", "name": "waypost-reach-probe"}
    ]


def test_job_with_no_report_yet():
    job = {
        "id": "j1",
        "reportTypeId": "channel_reach_combined_a1",
        "createTime": "2026-10-26T15:00:00Z",
    }
    result = probe(FakeReporting([REACH], [job]), _no_download)
    assert result["verdict"] == "no-report-yet"
    assert result["jobs"] == ["j1"]


def test_newest_report_is_read_and_its_columns_settle_the_question():
    job = {"id": "j1", "reportTypeId": "channel_reach_combined_a1"}
    reports = {
        "j1": [
            {
                "id": "old",
                "createTime": "2026-10-24T00:00:00Z",
                "downloadUrl": "https://x/old",
            },
            {
                "id": "new",
                "createTime": "2026-10-25T00:00:00Z",
                "downloadUrl": "https://x/new",
            },
        ]
    }
    seen = []

    def download(url):
        seen.append(url)
        return CSV_WITH_SOURCE

    result = probe(FakeReporting([REACH], [job], reports), download)
    assert seen == ["https://x/new"]
    assert result["verdict"] == "reach-available"
    assert result["has_reach_metrics"] is True
    assert result["has_traffic_source"] is True
    assert result["rows"] == 2
    assert result["videos"] == 2
    assert result["reports"] == 2


def test_report_without_the_reach_columns_is_named():
    job = {"id": "j1", "reportTypeId": "channel_reach_combined_a1"}
    reports = {
        "j1": [
            {
                "id": "r",
                "createTime": "2026-10-25T00:00:00Z",
                "downloadUrl": "https://x/r",
            }
        ]
    }
    result = probe(
        FakeReporting([REACH], [job], reports),
        lambda url: "date,video_id,views\n1,v1,3\n",
    )
    assert result["verdict"] == "reach-columns-missing"
    assert result["columns"] == ["date", "video_id", "views"]


def test_format_lines_are_one_greppable_verdict_line_first():
    lines = format_lines(
        {"verdict": "no-job", "reach_types": ["channel_reach_combined_a1"]}
    )
    assert lines[0] == "REACH_PROBE verdict=no-job"
    assert "REACH_PROBE reach_types=channel_reach_combined_a1" in lines


def test_main_reports_an_api_refusal_with_its_status(monkeypatch, capsys):
    resp = type("R", (), {"status": 403, "reason": "Forbidden"})()
    err = HttpError(
        resp,
        b'{"error": {"message": "Request had insufficient authentication scopes."}}',
    )

    class Refusing(FakeReporting):
        def reportTypes(self):
            return type("T", (), {"list": lambda self, **kw: _Call(err)})()

    monkeypatch.setattr(probe_mod, "_build", lambda info: (Refusing([]), _no_download))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"token": "t"})))
    assert probe_mod.main(["--token-stdin"]) == 2
    out = capsys.readouterr().out
    assert "REACH_PROBE verdict=api-error status=403" in out
    assert "insufficient authentication scopes" in out


def test_main_refuses_without_a_token_source(capsys):
    assert probe_mod.main([]) == 64


def test_sops_file_is_decrypted_and_a_binary_wrapper_unwrapped(monkeypatch, capsys):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        out = json.dumps({"data": json.dumps({"token": "t", "refresh_token": "r"})})
        return type("P", (), {"returncode": 0, "stdout": out, "stderr": ""})()

    def fake_build(info):
        seen["info"] = info
        return FakeReporting([OTHER]), _no_download

    monkeypatch.setattr(probe_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(probe_mod, "_build", fake_build)
    assert probe_mod.main(["--sops-file", "/x/youtube_upload_token.json.enc"]) == 0
    assert seen["cmd"] == [
        "sops",
        "-d",
        "--output-type",
        "json",
        "/x/youtube_upload_token.json.enc",
    ]
    assert seen["info"] == {"token": "t", "refresh_token": "r"}
    assert "REACH_PROBE verdict=no-reach-report-type" in capsys.readouterr().out


def test_sops_failure_is_reported_without_the_payload(monkeypatch, capsys):
    monkeypatch.setattr(
        probe_mod.subprocess,
        "run",
        lambda cmd, **kw: type(
            "P", (), {"returncode": 128, "stdout": "", "stderr": "no key could decrypt"}
        )(),
    )
    assert probe_mod.main(["--sops-file", "/x/t.enc"]) == 3
    assert "REACH_PROBE verdict=sops-error rc=128" in capsys.readouterr().out


BASIC = {"id": "channel_reach_basic_a1", "name": "Reach basic"}
CSV_NO_SOURCE = (
    "date,channel_id,video_id,video_thumbnail_impressions,video_thumbnail_impressions_ctr\n"
    "20261025,UC1,v1,200,0.04\n"
)


def test_create_job_asks_for_every_reach_type_not_just_the_first():
    # Live run 2026-10-26: the channel offers basic and combined; only combined
    # may carry traffic_source_type, and basic sorts first.
    reporting = FakeReporting([BASIC, REACH])
    result = probe(reporting, _no_download, create_job=True)
    assert result["verdict"] == "job-created"
    assert [b["reportTypeId"] for b in reporting.jobs().created] == [
        "channel_reach_basic_a1",
        "channel_reach_combined_a1",
    ]


def test_create_job_fills_in_a_reach_type_that_has_no_job_yet():
    job = {"id": "j1", "reportTypeId": "channel_reach_basic_a1"}
    reporting = FakeReporting([BASIC, REACH], [job])
    result = probe(reporting, _no_download, create_job=True)
    assert result["verdict"] == "job-created"
    assert reporting.jobs().created == [
        {"reportTypeId": "channel_reach_combined_a1", "name": "waypost-reach-probe"}
    ]


def test_a_newer_report_without_traffic_source_does_not_hide_one_with_it():
    jobs = [
        {"id": "jb", "reportTypeId": "channel_reach_basic_a1"},
        {"id": "jc", "reportTypeId": "channel_reach_combined_a1"},
    ]
    reports = {
        "jb": [
            {
                "id": "basic",
                "createTime": "2026-10-26T00:00:00Z",
                "downloadUrl": "https://x/basic",
            }
        ],
        "jc": [
            {
                "id": "combined",
                "createTime": "2026-10-25T00:00:00Z",
                "downloadUrl": "https://x/combined",
            }
        ],
    }
    csv_by_url = {
        "https://x/basic": CSV_NO_SOURCE,
        "https://x/combined": CSV_WITH_SOURCE,
    }
    result = probe(FakeReporting([BASIC, REACH], jobs, reports), csv_by_url.__getitem__)
    assert result["verdict"] == "reach-available"
    assert result["newest_report"] == "combined"
    assert result["has_traffic_source"] is True
    assert result["reports"] == 2
