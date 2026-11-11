"""Account-scoped content timing suggestions never reserve or publish a slot."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from seo_manager.scheduling.content_times import (
    MIN_SAMPLES,
    Available,
    Observation,
    Request,
    Unavailable,
    recommend_time,
)

NOW = datetime(2026, 11, 11, 12, tzinfo=UTC)


def observations(hour=15, value=100, count=MIN_SAMPLES, now=NOW):
    return tuple(
        Observation(
            post_id=f"p-{hour}-{i}",
            platform="facebook",
            account_id="123",
            content_format="video",
            metric="views",
            posted_at=(now - timedelta(days=8 + i)).replace(hour=hour),
            observed_at=now - timedelta(hours=1),
            day7_value=value,
        )
        for i in range(count)
    )


def request(**changes):
    return replace(
        Request(
            platform="facebook",
            account_id="123",
            content_format="video",
            timezone="UTC",
            now=NOW,
            observations=observations(),
            occupied_slots=(),
        ),
        **changes,
    )


def unavailable(req, reason):
    result = recommend_time(req)
    assert isinstance(result, Unavailable)
    assert result.reason == reason
    assert result.producer_version
    return result


def test_real_measured_suggestion_has_identity_provenance_and_utc_instant():
    result = recommend_time(request())
    assert isinstance(result, Available)
    assert result.proposed_at == NOW.replace(hour=15)
    assert result.proposed_at.tzinfo is UTC
    assert result.account_id == "123" and result.platform == "facebook"
    assert result.content_format == "video" and result.timezone == "UTC"
    assert result.basis == "measured_account_history"
    assert result.sample_count == MIN_SAMPLES
    assert result.evidence_age_seconds == 3600
    assert len(result.evidence_sha256) == 64
    assert result.reason == "best_measured_hour"


def test_ranking_prefers_measured_performance_not_first_or_largest_raw_sample():
    rows = observations(15, 100) + observations(17, 300)
    assert recommend_time(request(observations=rows)).proposed_at.hour == 17


def test_ties_and_input_order_are_deterministic():
    rows = observations(15, 100) + observations(17, 100)
    first = recommend_time(request(observations=rows))
    assert first.proposed_at.hour == 15
    assert recommend_time(request(observations=tuple(reversed(rows)))) == first


def test_identity_value_and_timezone_change_fingerprint():
    first = recommend_time(request())
    for req in (
        request(observations=observations(value=101)),
        request(timezone="Asia/Tokyo"),
        request(
            account_id="456",
            observations=tuple(replace(o, account_id="456") for o in observations()),
        ),
    ):
        assert recommend_time(req).evidence_sha256 != first.evidence_sha256


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("account_id", "456"),
        ("platform", "youtube"),
        ("content_format", "reel"),
        ("metric", "reel_total_plays"),
    ],
)
def test_mixed_identity_format_or_metric_refuses_entire_input(field, value):
    rows = list(observations())
    rows[-1] = replace(rows[-1], **{field: value})
    unavailable(request(observations=tuple(rows)), "incompatible_evidence")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, True, "100"])
def test_malformed_metric_is_not_a_score(value):
    rows = observations()
    unavailable(
        request(observations=(replace(rows[0], day7_value=value), *rows[1:])),
        "invalid_evidence",
    )


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"posted_at": NOW + timedelta(days=1)}, "future_evidence"),
        ({"observed_at": NOW + timedelta(seconds=1)}, "future_evidence"),
        ({"observed_at": NOW - timedelta(days=30)}, "stale_evidence"),
        ({"posted_at": NOW - timedelta(days=100)}, "stale_evidence"),
        ({"posted_at": NOW - timedelta(days=2)}, "unsettled_evidence"),
        ({"posted_at": NOW.replace(tzinfo=None)}, "invalid_evidence"),
        ({"post_id": ""}, "invalid_evidence"),
    ],
)
def test_time_and_identity_boundaries(changes, reason):
    rows = observations()
    unavailable(request(observations=(replace(rows[0], **changes), *rows[1:])), reason)


def test_duplicate_post_and_unbound_record_fail_closed():
    unavailable(
        request(observations=observations() + observations()[:1]), "duplicate_evidence"
    )
    unavailable(request(observations=({},)), "invalid_evidence")


def test_no_evidence_and_small_samples_remain_visible_unavailable():
    unavailable(request(observations=()), "no_evidence")
    unavailable(
        request(observations=observations(count=MIN_SAMPLES - 1)),
        "insufficient_evidence",
    )
    unavailable(request(content_format="photo", observations=()), "unsupported_content")


@pytest.mark.parametrize(
    "changes",
    [
        {"account_id": ""},
        {"platform": "made-up"},
        {"timezone": "bad/zone"},
        {"now": NOW.replace(tzinfo=None)},
        {"occupied_slots": (NOW.replace(tzinfo=None),)},
    ],
)
def test_invalid_request_has_typed_result(changes):
    unavailable(request(**changes), "invalid_request")


def test_occupied_slot_moves_to_next_day_without_mutation():
    req = request(occupied_slots=(NOW.replace(hour=15),))
    result = recommend_time(req)
    assert result.proposed_at == NOW.replace(hour=15) + timedelta(days=1)
    assert req.occupied_slots == (NOW.replace(hour=15),)


def test_all_slots_occupied_returns_unavailable_and_never_reserves():
    busy = tuple(NOW.replace(hour=15) + timedelta(days=i) for i in range(7))
    unavailable(request(occupied_slots=busy), "no_free_slot")


def test_past_hour_is_next_day_never_immediate():
    result = recommend_time(request(now=NOW.replace(hour=16)))
    assert result.proposed_at == NOW.replace(hour=15) + timedelta(days=1)


@pytest.mark.parametrize(
    ("now", "hour", "expected"),
    [
        (datetime(2026, 11, 1, 4, tzinfo=UTC), 5, datetime(2026, 11, 2, 6, tzinfo=UTC)),
        (datetime(2026, 3, 8, 5, tzinfo=UTC), 7, datetime(2026, 3, 9, 6, tzinfo=UTC)),
    ],
)
def test_ambiguous_and_nonexistent_local_candidate_is_skipped(now, hour, expected):
    result = recommend_time(
        request(
            now=now,
            timezone="America/New_York",
            observations=observations(hour=hour, now=now),
        )
    )
    assert result.proposed_at == expected


def test_facebook_reels_use_plays_and_youtube_has_separate_identity():
    for platform, fmt, metric in (
        ("facebook", "reel", "reel_total_plays"),
        ("youtube", "short", "views"),
    ):
        rows = tuple(
            replace(o, platform=platform, content_format=fmt, metric=metric)
            for o in observations()
        )
        assert isinstance(
            recommend_time(
                request(platform=platform, content_format=fmt, observations=rows)
            ),
            Available,
        )


def test_photo_has_explicit_data_requirement():
    result = unavailable(
        request(content_format="photo", observations=()), "unsupported_content"
    )
    assert "photo" in result.data_requirement
    assert "account" in result.data_requirement


def test_chosen_hours_age_does_not_borrow_newer_unselected_rows():
    old = tuple(
        replace(
            o,
            observed_at=NOW - timedelta(days=1),
            posted_at=o.posted_at - timedelta(days=1),
        )
        for o in observations(15, 300)
    )
    result = recommend_time(request(observations=old + observations(17, 100)))
    assert result.proposed_at.hour == 15
    assert result.evidence_age_seconds == 86400


def test_no_free_best_hour_falls_back_to_next_measured_hour():
    busy = tuple(NOW.replace(hour=15) + timedelta(days=i) for i in range(7))
    result = recommend_time(
        request(
            observations=observations(15, 300) + observations(17, 100),
            occupied_slots=busy,
        )
    )
    assert result.proposed_at == NOW.replace(hour=17)
