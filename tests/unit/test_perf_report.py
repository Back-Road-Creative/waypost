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


def test_cli_report_and_sync_dry_run(fixture_paths, capsys):
    ledger, videos = fixture_paths
    assert (
        cli.main(["report", "--ledger", str(ledger), "--videos-yaml", str(videos)]) == 0
    )
    out = capsys.readouterr().out
    assert "| publish month | n | median lifetime views | median views/day |" in out
    assert cli.main(["sync", "--dry-run"]) == 0
    assert "not implemented" in capsys.readouterr().out


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
