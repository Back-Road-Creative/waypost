"""EvidenceBundle v1 reader: round-trips the golden fixture, refuses the rest."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from seo_manager.analytics import evidence as ev

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "evidence_bundle_v1.json"


def golden():
    return json.loads(FIXTURE.read_text())


def test_golden_fixture_round_trips_byte_for_byte():
    raw = golden()
    assert ev.bundle_to_dict(ev.parse_bundle(raw)) == raw


def test_parsed_records_keep_the_producers_strings_and_types():
    b = ev.parse_bundle(golden())
    assert b.schema_version == 1
    assert b.health.analytics_status == "ok"
    assert b.health.excluded == [{"video_id": "vid-c", "reason": "no_series"}]
    assert [s.video_id for s in b.shorts] == ["vid-a", "vid-b"]
    assert b.shorts[1].region is None and b.shorts[1].day7_views == 87.5
    assert b.longforms[0].avg_view_percentage == 41.2
    assert ev.parse_ts(b.as_of) == datetime(2026, 10, 9, 6, 0)


def test_aware_timestamp_becomes_naive_utc():
    assert ev.parse_ts("2026-10-09T02:00:00-04:00") == datetime(2026, 10, 9, 6, 0)


@pytest.mark.parametrize(
    "mutate, needle",
    [
        (lambda r: r.update(schema_version=3), "schema_version 3"),
        (lambda r: r.update(schema_version=True), "schema_version"),
        (lambda r: r["health"].update(analytics_status="maybe"), "analytics_status"),
        (lambda r: r["health"].pop("n_registered"), "n_registered"),
        (
            lambda r: r["shorts"][0].update(platform="tiktok"),
            "platform must be 'youtube'",
        ),
        (lambda r: r["shorts"][0].update(source="guess"), "source 'guess'"),
        (lambda r: r["shorts"][0].update(weekday=7), "weekday"),
        (lambda r: r["shorts"][1].update(hour_local=24), "hour_local"),
        (lambda r: r["longforms"][0].update(posted_at="yesterday"), "posted_at"),
        (lambda r: r.update(longforms={}), "longforms must be a list"),
    ],
)
def test_unknown_or_malformed_bundle_is_refused_with_a_reason(mutate, needle):
    raw = golden()
    mutate(raw)
    with pytest.raises(ev.EvidenceRefused) as exc:
        ev.parse_bundle(raw)
    assert needle in str(exc.value)


def test_non_object_is_refused():
    with pytest.raises(ev.EvidenceRefused, match="not a JSON object"):
        ev.parse_bundle([1, 2])


def test_no_recommendation_or_threshold_fields_exist_on_the_bundle():
    names = {
        f
        for cls in (ev.EvidenceBundle, ev.Health, ev.ShortRecord, ev.LongformRecord)
        for f in cls.__dataclass_fields__
    }
    assert not {
        n for n in names if "recommend" in n or "threshold" in n or "target" in n
    }


# ---- v2: per-platform day-7 counters, Waypost-owned reach table ----

FIXTURE_V2 = FIXTURE.with_name("evidence_bundle_v2.json")


def golden2():
    return json.loads(FIXTURE_V2.read_text())


def test_v1_fixture_parses_identically_with_derived_fields():
    b = ev.parse_bundle(golden())
    assert b.unmeasured == []
    s = b.shorts[0]
    assert (s.platform, s.post_format, s.reach_metric) == ("youtube", None, "views")
    assert s.day7 == {"views": 412} and s.day7_views == 412


def test_v2_parses_and_reach_follows_the_table():
    b = ev.parse_bundle(golden2())
    assert b.schema_version == 2
    assert [s.video_id for s in b.shorts] == ["yt-a", "yt-b", "fb-reel"]
    yt, _, fb = b.shorts
    assert (yt.day7_views, yt.reach_metric) == (412, "views")
    assert (fb.day7_views, fb.reach_metric) == (1300, "reel_total_plays")
    assert fb.day7["reel_initial_plays"] == 1000 and fb.post_format == "reel"
    assert b.longforms[0].day7_views == 5310


def test_reach_table():
    t = ev.REACH_METRIC
    assert t[("youtube", "short")] == "views"
    assert t[("facebook", "reel")] == "reel_total_plays"
    assert t[("facebook", "video")] == "views"
    assert ("facebook", None) not in t


def test_unmeasured_row_is_listed_not_in_shorts():
    b = ev.parse_bundle(golden2())
    assert "fb-none" not in [s.video_id for s in b.shorts]
    assert b.unmeasured == [
        {
            "video_id": "fb-none",
            "platform": "facebook",
            "reason": "reel_total_plays not measured",
        }
    ]


def test_facebook_without_format_has_no_reach():
    raw = golden2()
    raw["shorts"][2]["post_format"] = None
    b = ev.parse_bundle(raw)
    assert b.unmeasured[0]["video_id"] == "fb-reel"
    assert "no reach metric" in b.unmeasured[0]["reason"]


def test_facebook_reel_plays_are_not_taken_from_views():
    raw = golden2()
    raw["shorts"][2]["day7"] = {"views": 999, "reel_total_plays": None}
    b = ev.parse_bundle(raw)
    assert "fb-reel" not in [s.video_id for s in b.shorts]


@pytest.mark.parametrize(
    "mutate, needle",
    [
        (lambda r: r["shorts"][0].update(platform="tiktok"), "platform 'tiktok'"),
        (lambda r: r["shorts"][0]["day7"].update(likes=3), "counter 'likes'"),
        (lambda r: r["shorts"][0]["day7"].update(views=-1), "views"),
        (lambda r: r["shorts"][0]["day7"].update(views="many"), "views"),
        (lambda r: r["shorts"][0].update(post_format="story"), "post_format"),
        (lambda r: r["shorts"][0].pop("post_format"), "post_format"),
        (lambda r: r["shorts"][0].pop("day7"), "day7"),
        (lambda r: r["shorts"][0].update(day7=[1]), "day7 must be an object"),
        (lambda r: r["shorts"][3].update(source="guess"), "source"),
    ],
)
def test_v2_malformed_is_refused_with_a_reason(mutate, needle):
    raw = golden2()
    mutate(raw)
    with pytest.raises(ev.EvidenceRefused) as exc:
        ev.parse_bundle(raw)
    assert needle in str(exc.value)


def test_v2_platform_summary_keeps_platforms_apart():
    s = ev.platform_reach_summary(ev.parse_bundle(golden2()))
    assert s == {
        "facebook": {"metric": "reel_total_plays", "n": 1, "median_day7": 1300},
        "youtube": {"metric": "views", "n": 2, "median_day7": 249.75},
    }


def test_summary_of_platform_with_only_unmeasured_rows():
    raw = golden2()
    raw["shorts"][2]["day7"] = {}
    s = ev.platform_reach_summary(ev.parse_bundle(raw))
    assert s["facebook"] == {"metric": None, "n": 0, "median_day7": None}
