import json

import pytest

from seo_manager import cli
from seo_manager.analytics.perf_report import (
    build_report,
    classify_title,
    join_rows,
    load_latest_views,
    load_videos,
)


def _ev(vid, views, ts):
    return json.dumps(
        {
            "event_id": f"yt_sync_{vid}_{ts}",
            "event_type": "performance_updated",
            "site_id": "examplebrand",
            "content_id": vid,
            "timestamp": ts,
            "data": {"video_id": vid, "views": views, "likes": 0},
        }
    )


@pytest.fixture
def fixture_paths(tmp_path):
    ledger = tmp_path / "events.jsonl"
    ledger.write_text(
        "\n".join(
            [
                _ev("a", 5, "2026-08-01T00:00:00"),  # superseded by later event
                _ev("a", 100, "2026-08-11T00:00:00"),
                _ev("b", 300, "2026-08-11T00:00:00"),
                _ev("c", 40, "2026-08-11T00:00:00"),
                json.dumps({"event_type": "metadata_generated", "timestamp": "x"}),
                _ev("zz", 999, "2026-08-11T00:00:00"),  # not in videos.yaml
            ]
        )
        + "\n"
    )
    videos = tmp_path / "videos.yaml"
    videos.write_text(
        "videos:\n"
        "- {id: a, title: 'X Scenic Drive in 4K | Y', date: '2026-08-01'}\n"
        "- {id: b, title: 'POV Drive', date: '2026-07-12'}\n"
        "- {id: c, title: 'Highlights from Z', date: '2026-07-02'}\n"
        "- {id: d, title: 'Never synced', date: '2026-06-01'}\n"
    )
    return ledger, videos


def test_latest_event_wins_and_join_medians(fixture_paths):
    ledger, videos = fixture_paths
    latest = load_latest_views(ledger)
    assert latest["a"]["data"]["views"] == 100
    rows = join_rows(load_videos(videos), latest)
    by_id = {r["id"]: r for r in rows}
    assert set(by_id) == {"a", "b", "c"}
    assert by_id["a"]["views_per_day"] == pytest.approx(10.0)  # 100 / 10 days
    assert by_id["b"]["views_per_day"] == pytest.approx(10.0)  # 300 / 30 days
    assert by_id["c"]["views_per_day"] == pytest.approx(1.0)  # 40 / 40 days
    md = build_report(ledger, videos)
    assert "| 2026-07 | 2 | 170 | 5.50 |" in md
    assert "| 2026-08 | 1 | 100 | 10.00 |" in md
    assert "| POV ... | 1 | 300 | 10.00 |" in md


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Through Exampleton | 4K Scenic Drive", "route | 4K Scenic Drive"),
        (
            "Examplestate Scenic Drive in 4K | Sampleville, ES",
            "REGION Scenic Drive in 4K | route",
        ),
        ("POV Driving Exampleton", "POV ..."),
        ("Highlights from Samplebridge", "Highlights from ..."),
        ("Testburg, TS in 4K", "{route} in 4K"),
        ("Exampleton to Hill Pass | County Test, Examplia", "other/legacy"),
    ],
)
def test_classify_title(title, expected):
    assert classify_title(title) == expected


def test_cli_report(fixture_paths, capsys):
    ledger, videos = fixture_paths
    assert (
        cli.main(["report", "--ledger", str(ledger), "--videos-yaml", str(videos)]) == 0
    )
    out = capsys.readouterr().out
    assert "| publish month | n | median lifetime views | median views/day |" in out


def _unmeasured_paths(fixture_paths):
    """Fixture ledger plus: ``e`` (``views: None``), ``f`` (no ``views`` key at
    all) and ``g`` (a measured 0) — all published in 2026-08 beside ``a``."""
    ledger, videos = fixture_paths
    absent = json.loads(_ev("f", 1, "2026-08-11T00:00:00"))
    del absent["data"]["views"]
    with open(ledger, "a", encoding="utf-8") as fh:
        fh.write(_ev("e", None, "2026-08-11T00:00:00") + "\n")
        fh.write(json.dumps(absent) + "\n")
        fh.write(_ev("g", 0, "2026-08-11T00:00:00") + "\n")
    with open(videos, "a", encoding="utf-8") as fh:
        fh.write("- {id: e, title: 'POV Null', date: '2026-08-01'}\n")
        fh.write("- {id: f, title: 'POV Absent', date: '2026-08-01'}\n")
        fh.write("- {id: g, title: 'POV Zero', date: '2026-08-01'}\n")
    return ledger, videos


def test_no_views_measurement_is_not_a_row_and_not_a_zero(fixture_paths):
    ledger, videos = _unmeasured_paths(fixture_paths)
    rows = join_rows(load_videos(videos), load_latest_views(ledger))
    by_id = {r["id"]: r for r in rows}
    assert "e" not in by_id and "f" not in by_id
    md = build_report(ledger, videos)
    # 2026-08 medians come from a (100) and g (0) only; e and f do not drag them.
    assert "| 2026-08 | 2 | 50 | 5.00 |" in md
    assert "Videos joined: 4" in md
    assert "Videos with a ledger entry but no views measurement: 2" in md


def test_measured_zero_views_is_still_a_row(fixture_paths):
    ledger, videos = _unmeasured_paths(fixture_paths)
    rows = join_rows(load_videos(videos), load_latest_views(ledger))
    by_id = {r["id"]: r for r in rows}
    assert by_id["g"]["views"] == 0
    assert by_id["g"]["views_per_day"] == 0.0


def _report(cli_args, capsys):
    code = cli.main(["report", *cli_args])
    return code, capsys.readouterr()


def _assert_one_line_exit_2(code, captured, *needles):
    assert code == 2
    assert captured.out == ""
    lines = captured.err.strip().splitlines()
    assert len(lines) == 1, captured.err
    assert lines[0].startswith("seo-manager report: ")
    assert "Traceback" not in captured.err
    for needle in needles:
        assert needle in lines[0]


def test_cli_report_missing_videos_yaml_exits_2(fixture_paths, tmp_path, capsys):
    ledger, _ = fixture_paths
    missing = tmp_path / "nope.yaml"
    code, cap = _report(
        ["--ledger", str(ledger), "--videos-yaml", str(missing)], capsys
    )
    _assert_one_line_exit_2(code, cap, "videos.yaml", "nope.yaml")


def test_cli_report_bad_ledger_json_line_exits_2(fixture_paths, capsys):
    ledger, videos = fixture_paths
    with open(ledger, "a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    code, cap = _report(["--ledger", str(ledger), "--videos-yaml", str(videos)], capsys)
    _assert_one_line_exit_2(code, cap, "ledger", "line 7", "JSON")


def test_cli_report_missing_videos_key_exits_2(fixture_paths, tmp_path, capsys):
    ledger, _ = fixture_paths
    bad = tmp_path / "other.yaml"
    bad.write_text("clips:\n- {id: a}\n")
    code, cap = _report(["--ledger", str(ledger), "--videos-yaml", str(bad)], capsys)
    _assert_one_line_exit_2(code, cap, "videos")


def test_cli_report_video_missing_field_exits_2(fixture_paths, tmp_path, capsys):
    ledger, _ = fixture_paths
    bad = tmp_path / "videos.yaml"
    bad.write_text("videos:\n- {id: a, title: T}\n")
    code, cap = _report(["--ledger", str(ledger), "--videos-yaml", str(bad)], capsys)
    _assert_one_line_exit_2(code, cap, "date")


def test_cli_report_unparseable_yaml_exits_2(fixture_paths, tmp_path, capsys):
    ledger, _ = fixture_paths
    bad = tmp_path / "videos.yaml"
    bad.write_text("videos: [unclosed\n")
    code, cap = _report(["--ledger", str(ledger), "--videos-yaml", str(bad)], capsys)
    _assert_one_line_exit_2(code, cap, "videos.yaml")


def test_cli_report_event_without_timestamp_exits_2(fixture_paths, capsys):
    ledger, videos = fixture_paths
    ev = json.loads(_ev("a", 1, "2026-08-12T00:00:00"))
    del ev["timestamp"]
    with open(ledger, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(ev) + "\n")
    code, cap = _report(["--ledger", str(ledger), "--videos-yaml", str(videos)], capsys)
    _assert_one_line_exit_2(code, cap, "timestamp", "line 7")


def _empty_join(tmp_path, ledger_lines, videos_text):
    ledger = tmp_path / "e.jsonl"
    ledger.write_text("".join(line + "\n" for line in ledger_lines))
    videos = tmp_path / "v.yaml"
    videos.write_text(videos_text)
    return ledger, videos


@pytest.mark.parametrize(
    ("ledger_lines", "videos_text", "cause"),
    [
        ([_ev("a", 1, "2026-08-11T00:00:00")], "videos: []\n", "no videos"),
        (
            [json.dumps({"event_type": "metadata_generated", "timestamp": "x"})],
            "videos:\n- {id: a, title: T, date: '2026-08-01'}\n",
            "no performance_updated events",
        ),
        (
            [_ev("zz", 1, "2026-08-11T00:00:00")],
            "videos:\n- {id: a, title: T, date: '2026-08-01'}\n",
            "no video id in common",
        ),
        (
            [_ev("a", None, "2026-08-11T00:00:00")],
            "videos:\n- {id: a, title: T, date: '2026-08-01'}\n",
            "no views measurement",
        ),
    ],
)
def test_cli_report_empty_join_names_the_cause(
    tmp_path, capsys, ledger_lines, videos_text, cause
):
    ledger, videos = _empty_join(tmp_path, ledger_lines, videos_text)
    out_file = tmp_path / "r.md"
    code, cap = _report(
        [
            "--ledger",
            str(ledger),
            "--videos-yaml",
            str(videos),
            "--out",
            str(out_file),
        ],
        capsys,
    )
    assert code == 0
    assert cause in cap.err
    assert cause in out_file.read_text()
    assert "Videos joined: 0" in out_file.read_text()


def test_cli_report_populated_join_has_no_notice(fixture_paths, capsys):
    ledger, videos = fixture_paths
    code, cap = _report(["--ledger", str(ledger), "--videos-yaml", str(videos)], capsys)
    assert code == 0
    assert cap.err == ""
    assert "Note:" not in cap.out


def test_cli_report_unwritable_out_exits_2(fixture_paths, tmp_path, capsys):
    ledger, videos = fixture_paths
    out = tmp_path / "no_such_dir" / "r.md"
    code, cap = _report(
        [
            "--ledger",
            str(ledger),
            "--videos-yaml",
            str(videos),
            "--out",
            str(out),
        ],
        capsys,
    )
    assert code == 2
    assert cap.err.startswith("seo-manager report: ")
    assert "Traceback" not in cap.err
