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
    assert b.schema_version == ev.SCHEMA_VERSION
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
        (lambda r: r.update(schema_version=2), "schema_version 2"),
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
