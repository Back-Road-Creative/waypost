"""Quota accounting by (project, API, bucket) with atomic reservations."""

import json
import logging
import threading
from datetime import UTC, datetime

import pytest

from seo_manager.quota.manager import Outcome, QuotaExceededError, QuotaManager

DATA = "youtube_data"


class Clock:
    def __init__(self, now: datetime):
        self.now = now

    __call__ = lambda self: self.now  # noqa: E731


def _mgr(tmp_path, **kw) -> QuotaManager:
    kw.setdefault("project_id", "proj")
    return QuotaManager(storage_path=tmp_path, **kw)


class TestBuckets:
    def test_insert_and_search_buckets_are_independent_of_the_10k_pool(self, tmp_path):
        m = _mgr(tmp_path)
        m.reserve("videos.insert")
        m.reserve("search.list")
        assert m.get_bucket_usage(DATA, "videos.insert")["used"] == 1
        assert m.get_bucket_usage(DATA, "search.list")["used"] == 1
        pool = m.get_bucket_usage(DATA, "default")
        assert pool["used"] == 0
        assert pool["limit"] == 10000
        assert m.get_bucket_usage(DATA, "videos.insert")["limit"] == 100
        assert m.get_bucket_usage(DATA, "search.list")["limit"] == 100

    def test_videos_update_costs_50_from_pool(self, tmp_path):
        m = _mgr(tmp_path)
        res = m.reserve("videos.update")
        assert res.cost == 50
        assert (res.api, res.bucket) == (DATA, "default")
        assert m.get_bucket_usage(DATA, "default")["used"] == 50

    @pytest.mark.parametrize(
        ("op", "cost"),
        [("videos.list", 1), ("channels.list", 1), ("thumbnails.set", 50)],
    )
    def test_documented_costs(self, tmp_path, op, cost):
        assert _mgr(tmp_path).estimate_cost(op) == cost

    @pytest.mark.parametrize(
        "key", ["youtube_data/videos.insert", (DATA, "videos.insert")]
    )
    def test_limits_are_overridable(self, tmp_path, key):
        m = _mgr(tmp_path, limits={key: 3})
        for _ in range(3):
            m.reserve("videos.insert")
        with pytest.raises(QuotaExceededError):
            m.reserve("videos.insert")

    def test_unknown_limit_apis_are_unbudgeted(self, tmp_path):
        m = _mgr(tmp_path)
        for _ in range(500):
            m.reserve("analytics.query", api="youtube_analytics")
        usage = m.get_bucket_usage("youtube_analytics", "default")
        assert usage["budgeted"] is False
        assert usage["limit"] is None
        assert usage["remaining"] is None
        assert usage["used"] == 500
        report = m.generate_report()
        assert "youtube_analytics/default" in report["unbudgeted"]

    def test_unbudgeted_api_can_be_configured(self, tmp_path):
        m = _mgr(tmp_path, limits={"search_console/default": 2})
        m.reserve("sc.query", api="search_console")
        m.reserve("sc.query", api="search_console")
        with pytest.raises(QuotaExceededError):
            m.reserve("sc.query", api="search_console")

    def test_unknown_operation_without_api_is_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            _mgr(tmp_path).reserve("no.such.operation")


class TestBrandAllocations:
    def test_brand_cap_inside_bucket(self, tmp_path):
        alloc = {"youtube_data/videos.insert": {"a": 2, "b": 3}}
        a = _mgr(tmp_path, brand_id="a", brand_allocations=alloc)
        b = _mgr(tmp_path, brand_id="b", brand_allocations=alloc)
        a.reserve("videos.insert")
        a.reserve("videos.insert")
        with pytest.raises(QuotaExceededError):
            a.reserve("videos.insert")
        for _ in range(3):
            b.reserve("videos.insert")
        usage = a.get_bucket_usage(DATA, "videos.insert")
        assert usage["by_brand"] == {"a": 2, "b": 3}

    def test_unallocated_brand_gets_only_the_remainder(self, tmp_path):
        alloc = {"youtube_data/videos.insert": {"a": 90}}
        other = _mgr(tmp_path, brand_id="z", brand_allocations=alloc)
        for _ in range(10):
            other.reserve("videos.insert")
        with pytest.raises(QuotaExceededError):
            other.reserve("videos.insert")

    def test_allocations_over_limit_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            _mgr(
                tmp_path,
                brand_allocations={"youtube_data/videos.insert": {"a": 80, "b": 30}},
            )


class TestReservation:
    def test_concurrent_reservers_cannot_both_pass_when_one_fits(self, tmp_path):
        n = 12
        managers = [
            _mgr(tmp_path, brand_id=f"b{i}", limits={"youtube_data/search.list": 5})
            for i in range(n)
        ]
        barrier = threading.Barrier(n)
        results: list[bool] = []
        guard = threading.Lock()

        def go(m):
            barrier.wait()
            try:
                m.reserve("search.list")
                ok = True
            except QuotaExceededError:
                ok = False
            with guard:
                results.append(ok)

        threads = [threading.Thread(target=go, args=(m,)) for m in managers]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert results.count(True) == 5
        assert results.count(False) == n - 5
        assert _mgr(tmp_path).get_bucket_usage(DATA, "search.list")["used"] == 5

    def test_settle_used_keeps_charge_and_can_correct_cost(self, tmp_path):
        m = _mgr(tmp_path)
        assert m.charge_on_failure is True  # failed requests are charged by default
        assert m.settle(m.reserve("videos.update"), Outcome.USED) is True
        assert m.get_bucket_usage(DATA, "default")["reserved"] == 0
        m.settle(m.reserve("thumbnails.set"), Outcome.USED, actual_cost=1)
        assert m.get_bucket_usage(DATA, "default")["used"] == 51

    @pytest.mark.parametrize(("charge", "used"), [(True, 50), (False, 0)])
    def test_failed_request_charge_policy(self, tmp_path, charge, used):
        m = _mgr(tmp_path, charge_on_failure=charge)
        m.settle(m.reserve("videos.update"), Outcome.FAILED)
        usage = m.get_bucket_usage(DATA, "default")
        assert usage["used"] == used
        assert usage["charged_failures"] == (1 if charge else 0)

    def test_not_sent_is_refunded(self, tmp_path):
        m = _mgr(tmp_path, brand_id="a")
        res = m.reserve("videos.insert")
        m.release(res)
        usage = m.get_bucket_usage(DATA, "videos.insert")
        assert usage["used"] == 0
        assert usage["by_brand"].get("a", 0) == 0

    def test_settle_twice_does_not_double_refund(self, tmp_path):
        m = _mgr(tmp_path)
        keep = m.reserve("videos.update")
        res = m.reserve("videos.update")
        assert m.release(res) is True
        assert m.release(res) is False
        assert m.get_bucket_usage(DATA, "default")["used"] == 50
        assert keep.cost == 50

    def test_reservation_visible_to_other_instance_before_settle(self, tmp_path):
        m1 = _mgr(tmp_path)
        m2 = _mgr(tmp_path)
        res = m1.reserve("videos.update")
        assert m2.get_bucket_usage(DATA, "default")["reserved"] == 50
        assert m2.settle(res, Outcome.USED) is True


class TestLosAngelesDay:
    @pytest.mark.parametrize(
        ("before", "after"),
        [
            # DST starts 2026-03-08: 07:00Z on Mar 9 is 00:00 PDT.
            (
                datetime(2026, 3, 9, 6, 59, tzinfo=UTC),
                datetime(2026, 3, 9, 7, 0, tzinfo=UTC),
            ),
            # DST ends 2026-11-01: 08:00Z on Nov 2 is 00:00 PST.
            (
                datetime(2026, 11, 2, 7, 59, tzinfo=UTC),
                datetime(2026, 11, 2, 8, 0, tzinfo=UTC),
            ),
        ],
    )
    def test_reset_at_la_midnight_across_dst(self, tmp_path, before, after):
        clock = Clock(before)
        m = _mgr(tmp_path, clock=clock)
        m.reserve("videos.update")
        assert m.get_bucket_usage(DATA, "default")["used"] == 50
        clock.now = after
        assert m.get_bucket_usage(DATA, "default")["used"] == 0

    def test_utc_midnight_is_not_a_reset(self, tmp_path):
        clock = Clock(datetime(2026, 10, 27, 23, 0, tzinfo=UTC))  # 16:00 PDT
        m = _mgr(tmp_path, clock=clock)
        m.reserve("videos.update")
        clock.now = datetime(2026, 10, 28, 1, 0, tzinfo=UTC)  # UTC day rolled
        assert m.get_bucket_usage(DATA, "default")["used"] == 50

    def test_next_reset_is_la_midnight(self, tmp_path):
        clock = Clock(datetime(2026, 3, 8, 12, 0, tzinfo=UTC))
        m = _mgr(tmp_path, clock=clock)
        assert m.next_reset() == datetime(2026, 3, 9, 7, 0, tzinfo=UTC)
        clock.now = datetime(2026, 12, 1, 12, 0, tzinfo=UTC)
        assert m.next_reset() == datetime(2026, 12, 2, 8, 0, tzinfo=UTC)


class TestOldFormatState:
    def test_old_state_starts_fresh_with_a_warning(self, tmp_path, caplog):
        d = tmp_path / "proj"
        d.mkdir()
        (d / "quota_usage.json").write_text(
            json.dumps(
                {
                    "total_used": 1750,
                    "last_reset": datetime.now(UTC).isoformat(),
                    "operations": [],
                }
            )
        )
        with caplog.at_level(logging.WARNING):
            m = _mgr(tmp_path)
        assert m.get_bucket_usage(DATA, "default")["used"] == 0
        assert any("old format" in r.message for r in caplog.records)
        assert json.loads(m.quota_file.read_text())["version"] == 2
