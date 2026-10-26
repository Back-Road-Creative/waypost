"""The append-only cadence decision log."""

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

import pytest

from seo_manager.analytics import cadence_decisions as cd
from seo_manager.analytics import volume_policy as policy
from seo_manager.analytics.evidence import EvidenceRefused, bundle_to_dict, parse_bundle

NOW = datetime(2026, 10, 9, 12, 0)
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "evidence_bundle_v1.json"


def put(db, knob="shorts_per_lf", value=7, region=None, **kw):
    fields = dict(
        knob=knob,
        region=region,
        value=value,
        prev=value,
        status="hold",
        source="policy",
        reason_code="INSUFFICIENT_EVIDENCE",
        basis="test",
        applied_at=NOW,
    )
    fields.update(kw)
    return cd.append(db, **fields)


def test_append_only_triggers_reject_update_and_delete(tmp_path):
    db = tmp_path / "cadence_decisions.sqlite3"
    put(db)
    conn = sqlite3.connect(db)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE cadence_decisions SET value_json = '9'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM cadence_decisions")
    conn.close()
    assert len(cd.history(db)) == 1


def test_current_on_empty_table_returns_seed_source_seed_and_creates_no_file(tmp_path):
    db = tmp_path / "sub" / "cadence_decisions.sqlite3"
    got = cd.current(db, "shorts_per_lf")
    assert (got.value, got.source) == (policy.SHORTS_FLOOR, "seed")
    assert not db.exists() and not db.parent.exists()
    assert cd.current(db, "lf_days", "us", seed=2).value == 2
    # a created-but-empty table is the same as no file
    cd.ensure_schema(db)
    assert cd.current(db, "shorts_per_lf").source == "seed"


def test_current_returns_latest_row_per_knob_and_region(tmp_path):
    db = tmp_path / "d.sqlite3"
    put(db, value=7)
    put(db, value=8, prev=7, status="ok", reason_code="TAIL_UP")
    put(db, "lf_days", 2, region="us")
    got = cd.current(db, "shorts_per_lf")
    assert (got.value, got.source, got.status) == (8, "policy", "ok")
    assert cd.current(db, "lf_days", "us").value == 2
    assert cd.current(db, "lf_days", "uk", seed=1).source == "seed"
    assert [r.value for r in cd.history(db, "shorts_per_lf")] == [7, 8]


def test_current_on_corrupt_db_raises_DecisionStoreUnreadable(tmp_path):
    db = tmp_path / "cadence_decisions.sqlite3"
    db.write_bytes(b"this is not a sqlite database" * 50)
    with pytest.raises(cd.DecisionStoreUnreadable):
        cd.current(db, "shorts_per_lf")
    with pytest.raises(cd.DecisionStoreUnreadable):
        put(db)


def test_write_rejects_shorts_per_lf_above_windows_times_7(tmp_path):
    db = tmp_path / "d.sqlite3"
    put(db, "shorts_times", ["09:00"], reason_code="X")
    with pytest.raises(cd.DecisionRejected, match="coupling"):
        put(db, value=policy.SHORTS_CEILING, prev=7, status="ok")
    put(db, value=7)
    # shrinking the windows under the live shorts_per_lf is refused too
    put(db, "shorts_times", ["09:00", "13:00"])
    put(db, value=policy.SHORTS_CEILING, prev=7, status="ok")
    with pytest.raises(cd.DecisionRejected, match="coupling"):
        put(db, "shorts_times", ["09:00"])


def test_write_rejects_value_outside_floor_ceiling(tmp_path):
    db = tmp_path / "d.sqlite3"
    for knob, bad in (
        ("shorts_per_lf", policy.SHORTS_FLOOR - 1),
        ("shorts_per_lf", policy.SHORTS_CEILING + 1),
        ("lf_days", policy.LF_DAYS_FLOOR - 1),
        ("lf_days", policy.LF_DAYS_CEILING_PER_REGION + 1),
        ("shorts_per_lf", True),
        ("shorts_per_lf", "8"),
    ):
        with pytest.raises(cd.DecisionRejected):
            put(db, knob, bad)
    with pytest.raises(cd.DecisionRejected):
        put(db, "nonsense", 1)
    assert not db.exists()


def test_two_concurrent_deciders_serialize(tmp_path):
    db = tmp_path / "d.sqlite3"
    n, errors = 25, []

    def writer(tag):
        try:
            for i in range(n):
                put(db, "lf_days", 1 + i % 3, region=tag, basis=f"{tag}-{i}")
        except Exception as exc:  # pragma: no cover - the failure path
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(t,)) for t in ("us", "uk")]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    rows = cd.history(db)
    assert len(rows) == 2 * n
    assert len({r.id for r in rows}) == 2 * n
    for tag in ("us", "uk"):
        assert [r.basis for r in rows if r.region == tag] == [
            f"{tag}-{i}" for i in range(n)
        ]


def test_append_many_is_atomic(tmp_path):
    db = tmp_path / "d.sqlite3"
    good = cd.NewRow("shorts_per_lf", None, 7, 7, "hold", "policy", "C", "b", NOW)
    bad = cd.NewRow("shorts_per_lf", None, 99, 7, "ok", "policy", "C", "b", NOW)
    with pytest.raises(cd.DecisionRejected):
        cd.append_many(db, [good, bad])
    assert cd.history(db) == []


def test_golden_bundle_roundtrip():
    raw = json.loads(FIXTURE.read_text())
    bundle = parse_bundle(raw)
    assert bundle.health.analytics_status == "ok"
    assert [s.source for s in bundle.shorts] == ["analytics_daily", "snapshot"]
    assert bundle_to_dict(bundle) == raw


def test_bundle_with_unknown_schema_version_refused_with_reason():
    raw = json.loads(FIXTURE.read_text())
    raw["schema_version"] = 3
    with pytest.raises(EvidenceRefused, match="schema_version"):
        parse_bundle(raw)


@pytest.mark.parametrize(
    "mutate, needle",
    [
        (lambda r: r["health"].update(analytics_status="maybe"), "analytics_status"),
        (lambda r: r["shorts"][0].update(source="guess"), "source"),
        (lambda r: r["shorts"][0].update(day7_views="many"), "day7_views"),
        (lambda r: r["shorts"][0].pop("posted_at"), "posted_at"),
        (lambda r: r.pop("health"), "health"),
    ],
)
def test_malformed_bundle_refused(mutate, needle):
    raw = json.loads(FIXTURE.read_text())
    mutate(raw)
    with pytest.raises(EvidenceRefused, match=needle):
        parse_bundle(raw)


def test_coupling_is_checked_against_every_regions_windows(tmp_path):
    db = tmp_path / "d.sqlite3"
    put(db, "shorts_times", ["09:00", "13:00"], region="south")
    put(db, "shorts_times", ["10:00"], region="north")
    with pytest.raises(cd.DecisionRejected, match="coupling"):
        put(db, value=policy.SHORTS_CEILING, prev=7, status="ok")  # north: 1 x 7
    put(db, "shorts_times", ["10:00", "14:00"], region="north")
    put(db, value=policy.SHORTS_CEILING, prev=7, status="ok")
    with pytest.raises(cd.DecisionRejected, match="coupling"):
        put(db, "shorts_times", ["10:00"], region="north")
    put(db, "shorts_times", ["11:00", "14:00"], region="north")  # a swap is fine


def test_shorts_times_write_rejects_malformed_or_repeated_windows(tmp_path):
    db = tmp_path / "d.sqlite3"
    for bad in (["09:00", "09:00"], ["9am"], ["25:00"]):
        with pytest.raises(cd.DecisionRejected):
            put(db, "shorts_times", bad, region="south")


def test_coupling_uses_the_share_one_lf_day_gets(tmp_path):
    db = tmp_path / "d.sqlite3"
    four = ["10:00", "14:00", "16:00", "18:00"]
    put(db, "shorts_times", four, region="south")
    put(db, "lf_days", 2, region="south")
    put(db, value=policy.SHORTS_CEILING, prev=7, status="ok")  # 2 a day x 7 carries 10
    with pytest.raises(cd.DecisionRejected, match="coupling"):
        put(db, "lf_days", 3, region="south")  # 1 a day x 7 cannot
    put(db, value=7)
    put(db, "lf_days", 3, region="south")
    with pytest.raises(cd.DecisionRejected, match="coupling"):
        put(db, value=8, prev=7, status="ok")
    with pytest.raises(cd.DecisionRejected, match="coupling"):
        put(db, "shorts_times", ["10:00", "14:00"], region="south")  # 0 a day
