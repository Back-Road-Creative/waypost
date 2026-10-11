"""A click rate stays a rate, for every count Hypothesis can think of.

``AnalyticsTracker._resolve_rate`` (seo_manager/analytics/tracker.py) is the
one place a per-video or site-wide click rate is settled from counts (the pooled read
uses it too). The example tests around it pin
specific readings; these pin the shape of the function itself, over every
non-negative pair of counts:

* with impressions, the rate is ``round(clicks / impressions, 4)`` and sits in
  ``[0, 1]`` whenever ``clicks <= impressions``;
* at a fixed denominator, more clicks never yields a lower rate;
* with no impressions the resolver does NOT divide: it falls through to the
  last reported rate, else says nothing (``None``) -- never the measurement 0.

The contract asserted is the one the source reads, not a stricter one: the
resolver neither refuses nor clamps ``clicks > impressions``. It reports the
ratio it was given (``> 1``), so a bad upstream count is visible instead of
being quietly folded into a plausible number. See ``tracker.py:354-356``.
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from seo_manager.analytics import metric_source
from seo_manager.analytics.tracker import (
    CTR_BASIS_RECOMPUTED,
    CTR_BASIS_REPORTED,
    AnalyticsTracker,
)

COUNT = st.integers(min_value=0, max_value=10**9)
RATE = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)


def _aggregate(clicks, impressions, explicit_ctr=None):
    """The dict shape ``_aggregate`` hands the resolver (``tracker.py:272-307``)
    pared to the keys ``_resolve_rate`` reads. The pooled read in
    ``get_summary`` passes exactly this much (``tracker.py:184-190``)."""
    return {
        "clicks": clicks,
        "impressions": impressions,
        "_explicit_ctr": explicit_ctr,
        "ctr_source": metric_source.UNKNOWN,
    }


def _resolve(clicks, impressions, explicit_ctr=None):
    return AnalyticsTracker._resolve_rate(_aggregate(clicks, impressions, explicit_ctr))


@settings(deadline=None, max_examples=200)
@given(
    st.integers(min_value=1, max_value=10**9).flatmap(
        lambda impressions: st.tuples(
            st.integers(min_value=0, max_value=impressions), st.just(impressions)
        )
    )
)
def test_property_rate_stays_in_unit_interval(counts):
    """clicks <= impressions -> 0 <= ctr <= 1, basis says it was recomputed."""
    clicks, impressions = counts

    resolved = _resolve(clicks, impressions)

    assert 0.0 <= resolved["ctr"] <= 1.0
    assert resolved["ctr"] == round(clicks / impressions, 4)  # tracker.py:355
    assert resolved["ctr_basis"] == CTR_BASIS_RECOMPUTED


@settings(deadline=None, max_examples=200)
@given(
    impressions=st.integers(min_value=1, max_value=10**9),
    clicks=COUNT,
    more=st.integers(min_value=0, max_value=10**9),
)
def test_property_rate_is_monotone_in_clicks_at_fixed_impressions(
    impressions, clicks, more
):
    """More clicks over the same impressions never reads as a lower rate."""
    lower = _resolve(clicks, impressions)["ctr"]
    higher = _resolve(clicks + more, impressions)["ctr"]

    assert higher >= lower


@settings(deadline=None, max_examples=200)
@given(clicks=COUNT)
def test_property_zero_impressions_says_nothing_not_zero(clicks):
    """No denominator, no reported rate -> ``None`` / ``None`` (tracker.py:360-362).

    Any click count, including 0, lands here: the resolver never divides by
    an empty denominator and never reports the measurement 0 for it."""
    resolved = _resolve(clicks, 0)

    assert resolved["ctr"] is None
    assert resolved["ctr_basis"] is None
    assert "_explicit_ctr" not in resolved  # popped, tracker.py:363


@settings(deadline=None, max_examples=200)
@given(clicks=COUNT, reported=RATE)
def test_property_zero_impressions_falls_back_to_the_reported_rate(clicks, reported):
    """No denominator but a platform-reported rate -> that rate, verbatim,
    with ``ctr_basis`` naming it as reported (tracker.py:357-359)."""
    resolved = _resolve(clicks, 0, explicit_ctr=reported)

    assert resolved["ctr"] == reported
    assert resolved["ctr_basis"] == CTR_BASIS_REPORTED


@settings(deadline=None, max_examples=200)
@given(
    clicks=COUNT, impressions=st.integers(min_value=1, max_value=10**9), reported=RATE
)
def test_property_counts_win_over_a_reported_rate(clicks, impressions, reported):
    """With a denominator in hand the recomputed ratio is used, and the reported
    rate is ignored -- the precedence ``_resolve_rate`` documents."""
    resolved = _resolve(clicks, impressions, explicit_ctr=reported)

    assert resolved["ctr"] == round(clicks / impressions, 4)
    assert resolved["ctr_basis"] == CTR_BASIS_RECOMPUTED


@settings(deadline=None, max_examples=200)
@given(
    impressions=st.integers(min_value=1, max_value=10**9),
    excess=st.integers(min_value=1, max_value=10**9),
)
def test_property_more_clicks_than_impressions_is_reported_not_clamped(
    impressions, excess
):
    """The contract as written: ``tracker.py:354-356`` divides unconditionally
    when impressions are non-zero. Neither a refusal nor a clamp to 1.0 exists,
    so an over-count surfaces as a rate above 1 rather than being hidden. If a
    clamp or a raise is ever added, this test is the one to rewrite."""
    clicks = impressions + excess

    resolved = _resolve(clicks, impressions)

    assert resolved["ctr"] == round(clicks / impressions, 4)
    # 4-dp rounding can land an over-count of one on exactly 1.0; never below.
    assert resolved["ctr"] >= 1.0
    assert resolved["ctr_basis"] == CTR_BASIS_RECOMPUTED
