"""title_evidence: YouTube reach rows become the snapshot select_title reads."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from seo_manager.analytics import metric_source
from seo_manager.analytics.title_evidence import (
    TRAFFIC_CODES,
    TitleAttribution,
    TitleEvidenceRefused,
    snapshot_from_dict,
    snapshot_to_dict,
    title_evidence,
    title_evidence_from_store,
)
from seo_manager.analytics.title_selection import (
    BASELINE,
    DEFAULT_POLICY,
    LIFT_OVER_BASELINE,
    Place,
    TitleFacts,
    select_title,
)
from seo_manager.collection.contracts import Observation
from seo_manager.collection.store import CollectionStore
from seo_manager.collection.youtube_reach import COMBINED_SOURCE

AS_OF = datetime(2026, 11, 1, tzinfo=UTC)
DAY = timedelta(days=1)
ATTR = {
    "vidA": TitleAttribution("route", "longform"),
    "vidB": TitleAttribution("highlight_route", "longform"),
    "vidS": TitleAttribution("place", "short"),
}


def _rows(video, code, imps, ctr, days_ago=3, detail="", tenant="t"):
    subject = "|".join([video, str(code), detail, "ANDROID", "MOBILE"])
    start = AS_OF - timedelta(days=days_ago + 1)
    return [
        Observation(
            tenant=tenant,
            source=COMBINED_SOURCE,
            subject=subject,
            metric=metric,
            window_start=start,
            window_end=start + DAY,
            value=value,
            reported_at=AS_OF,
        )
        for metric, value in (
            ("video_thumbnail_impressions", imps),
            ("video_thumbnail_impressions_ctr", ctr),
        )
    ]


def test_ctr_fraction_sums_clicks_per_group():
    rows = _rows("vidA", 5, 1000, 0.10, 3) + _rows("vidA", 5, 3000, 0.02, 4)
    res = title_evidence(rows, ATTR, AS_OF)
    (o,) = res.snapshot.observations
    assert (o.template_id, o.format, o.traffic_source) == (
        "route",
        "longform",
        "search",
    )
    assert o.n == 4000
    assert o.rate == pytest.approx((100 + 60) / 4000)
    assert o.metric_source == metric_source.YOUTUBE_THUMBNAIL_IMPRESSION_CTR
    assert res.rows_used == 2
    assert o.window_start < o.window_end


def test_ctr_over_one_row_summed_group_over_one_dropped():
    ok = title_evidence(
        _rows("vidA", 5, 1000, 1.5) + _rows("vidA", 5, 9000, 0.1, 4), ATTR, AS_OF
    )
    assert ok.snapshot.observations[0].rate == pytest.approx((1500 + 900) / 10000)
    assert ok.groups_dropped_rate_over_1 == 0
    bad = title_evidence(_rows("vidA", 5, 1000, 1.5), ATTR, AS_OF)
    assert bad.snapshot.observations == ()
    assert bad.groups_dropped_rate_over_1 == 1


def test_traffic_mapping_and_other_codes_ignored():
    assert TRAFFIC_CODES == {"5": "search", "3": "browse"}
    rows = (
        _rows("vidA", 5, 500, 0.1)
        + _rows("vidA", 3, 500, 0.2)
        + _rows("vidA", 24, 500, 0.3)
        + _rows("vidA", 0, 500, 0.3)
    )
    res = title_evidence(rows, ATTR, AS_OF)
    assert {o.traffic_source for o in res.snapshot.observations} == {"search", "browse"}
    assert res.rows_other_traffic == 2


def test_skips_are_counted():
    digest = _rows("vidA", 5, 500, 0.1)
    digest = [
        Observation(**{**o.__dict__, "subject": "sha256:" + "a" * 32}) for o in digest
    ]
    none_val = _rows("vidA", 3, None, 0.1)
    rows = (
        _rows("ghost", 5, 500, 0.1)
        + digest
        + none_val
        + _rows("vidB", 5, 500, 0.1, days_ago=40)
        + _rows("vidB", 5, 500, 0.1, days_ago=-2)
        + _rows("vidB", 5, 500, 0.1, days_ago=2)
    )
    res = title_evidence(rows, ATTR, AS_OF)
    assert res.rows_unattributed == 1
    assert res.rows_unparseable_subject == 1
    assert res.rows_unknown_value == 1
    assert res.rows_out_of_window == 2
    assert res.rows_used == 1
    (o,) = res.snapshot.observations
    assert o.template_id == "highlight_route"


def test_missing_ctr_row_is_unknown_not_zero():
    rows = _rows("vidA", 5, 500, 0.1)[:1]
    res = title_evidence(rows, ATTR, AS_OF)
    assert res.rows_unknown_value == 1 and res.snapshot.observations == ()


def test_deterministic_order():
    rows = (
        _rows("vidB", 5, 500, 0.1)
        + _rows("vidA", 3, 500, 0.1)
        + _rows("vidS", 3, 500, 0.1)
        + _rows("vidA", 5, 500, 0.1)
    )
    a = title_evidence(rows, ATTR, AS_OF).snapshot
    b = title_evidence(list(reversed(rows)), ATTR, AS_OF).snapshot
    assert a == b
    keys = [(o.template_id, o.format, o.traffic_source) for o in a.observations]
    assert keys == sorted(keys)


def test_dict_round_trip_and_refusals():
    snap = title_evidence(_rows("vidA", 5, 500, 0.1), ATTR, AS_OF).snapshot
    data = json.loads(json.dumps(snapshot_to_dict(snap)))
    assert snapshot_from_dict(data) == snap
    for bad in (
        None,
        {"as_of": "nope", "observations": []},
        {"as_of": snap.as_of, "observations": "x"},
        {"as_of": snap.as_of, "observations": [{"template_id": "a"}]},
    ):
        with pytest.raises(TitleEvidenceRefused):
            snapshot_from_dict(bad)
    data["observations"][0]["n"] = "5"
    with pytest.raises(TitleEvidenceRefused):
        snapshot_from_dict(data)


def test_end_to_end_store_to_select_title(tmp_path):
    store = CollectionStore(tmp_path / "c.db")
    store.init()
    rows = (
        _rows("vidA", 5, 600, 0.05)  # baseline route
        + _rows("vidB", 5, 600, 0.10)  # candidate, double the rate
    )
    store.write_batch("t", rows)
    res = title_evidence_from_store(store, "t", ATTR, AS_OF)
    assert res.rows_used == 2
    facts = TitleFacts(
        format="longform",
        baseline_title="Brindlemoor to Quillhaven, Nordale",
        origin=Place("Brindlemoor", "a"),
        destination=Place("Quillhaven", "b"),
        region=Place("Nordale", "c"),
        highlight=Place("Ashtarn Ridge", "d"),
    )
    decision = select_title(facts, res.snapshot, DEFAULT_POLICY)
    assert decision.reason == LIFT_OVER_BASELINE
    assert decision.candidates[decision.chosen_index].template_id != BASELINE
