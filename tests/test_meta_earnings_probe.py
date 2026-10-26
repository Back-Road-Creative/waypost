"""The Meta earnings probe (plan B3, 2026-10-26).

Before any monetization comparison is built, the probe asks the Graph API
which Page earnings metrics the stored Page token can read, and names the
refusal for each one it cannot.
"""

import io
import json

from seo_manager.analytics import meta_earnings_probe as probe_mod
from seo_manager.analytics.meta_earnings_probe import (
    EARNINGS_METRICS,
    format_lines,
    probe,
)

CREDS = {
    "page_id": "123",
    "page_access_token": "SECRET-TOKEN",
    "graph_version": "v21.0",
}


def _ok(*values):
    return 200, {
        "data": [
            {
                "name": "m",
                "values": [
                    {"value": v, "end_time": f"t{i}"} for i, v in enumerate(values)
                ],
            }
        ]
    }


def _refused(code, message):
    return 400, {"error": {"code": code, "message": message}}


def test_every_metric_is_asked_on_the_page_with_the_page_token():
    calls = []

    def get(url, params):
        calls.append((url, params))
        return _ok(1.5)

    probe(CREDS, get)
    assert [p["metric"] for _, p in calls] == list(EARNINGS_METRICS)
    assert {u for u, _ in calls} == {"https://graph.facebook.com/v21.0/123/insights"}
    assert all(
        p["access_token"] == "SECRET-TOKEN" and p["period"] == "day" for _, p in calls
    )


def test_readable_metric_reports_points_and_latest_value():
    result = probe(CREDS, lambda url, params: _ok(0, {"ads": 2.5}))
    first = result["metrics"][EARNINGS_METRICS[0]]
    assert first == {"status": "ok", "points": 2, "latest": {"ads": 2.5}}
    assert result["verdict"] == "available"


def test_refusals_are_named_and_all_refused_is_unavailable():
    result = probe(
        CREDS,
        lambda url, params: _refused(10, "(#10) Application does not have permission"),
    )
    assert result["verdict"] == "unavailable"
    assert all(
        m
        == {
            "status": "error",
            "code": 10,
            "message": "(#10) Application does not have permission",
        }
        for m in result["metrics"].values()
    )


def test_metric_with_no_data_points_is_empty_not_zero():
    result = probe(CREDS, lambda url, params: (200, {"data": []}))
    assert all(
        m == {"status": "empty", "points": 0} for m in result["metrics"].values()
    )
    assert result["verdict"] == "unavailable"


def test_token_never_appears_in_output_even_if_echoed():
    result = probe(CREDS, lambda url, params: _refused(190, "bad token SECRET-TOKEN"))
    text = "\n".join(format_lines(result))
    assert "SECRET-TOKEN" not in text
    assert text.splitlines()[0] == "META_EARNINGS verdict=unavailable"


def test_missing_page_fields_refuse_before_any_call():
    def get(url, params):
        raise AssertionError("no call expected")

    result = probe({"page_id": "123"}, get)
    assert result == {
        "verdict": "missing-credentials",
        "missing": ["page_access_token"],
    }


def test_main_reads_sops_file(monkeypatch, capsys):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return type(
            "P", (), {"returncode": 0, "stdout": json.dumps(CREDS), "stderr": ""}
        )()

    monkeypatch.setattr(probe_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(probe_mod, "_http_get", lambda url, params: _ok(3))
    assert probe_mod.main(["--sops-file", "/x/meta_publishing.json.enc"]) == 0
    assert seen["cmd"] == [
        "sops",
        "-d",
        "--output-type",
        "json",
        "/x/meta_publishing.json.enc",
    ]
    out = capsys.readouterr().out
    assert out.startswith("META_EARNINGS verdict=available")
    assert "SECRET-TOKEN" not in out


def test_main_sops_failure_and_usage(monkeypatch, capsys):
    monkeypatch.setattr(
        probe_mod.subprocess,
        "run",
        lambda cmd, **kw: type(
            "P", (), {"returncode": 128, "stdout": "", "stderr": "no key"}
        )(),
    )
    assert probe_mod.main(["--sops-file", "/x/m.enc"]) == 3
    assert "META_EARNINGS verdict=sops-error rc=128" in capsys.readouterr().out
    assert probe_mod.main([]) == 64


def test_main_reads_stdin(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(CREDS)))
    monkeypatch.setattr(probe_mod, "_http_get", lambda url, params: (200, {"data": []}))
    assert probe_mod.main(["--creds-stdin"]) == 0
    assert "META_EARNINGS verdict=unavailable" in capsys.readouterr().out
