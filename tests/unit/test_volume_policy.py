"""The named rails: values, derivations and the safe-rate pin."""

from seo_manager.analytics import volume_policy as policy
from seo_manager.analytics.anomaly_stats import min_references_for


def test_rails_have_the_declared_values():
    assert policy.POLICY_REV == 1
    assert (policy.SHORTS_FLOOR, policy.SHORTS_CEILING) == (7, 10)
    assert policy.LF_DAYS_FLOOR == 1
    assert policy.LF_DAYS_CEILING_PER_REGION == 3
    assert policy.LF_DAYS_CEILING_PER_WEEK == 4
    assert policy.JOINT_WEEKLY_SHORTS_CEILING == 30
    assert (policy.DOWN_FRACTION, policy.UP_FRACTION, policy.TAIL_K) == (0.25, 0.75, 2)
    assert policy.COOLDOWN_DAYS == {
        "shorts_per_lf": 14,
        "lf_days": 28,
        "shorts_times": 14,
    }
    assert policy.PROBATION_DAYS == 28
    assert policy.UP_CONFIRM_COHORTS == 2
    assert policy.BLIND_DAYS == 21
    assert policy.LOCKOUT_CAP_DAYS == 112
    assert policy.LEARNED_CEILING_DAYS == 90


def test_sample_sizes_are_derived_not_typed():
    assert (policy.DOWN_ALPHA, policy.UP_ALPHA) == (0.20, 0.05)
    assert policy.DOWN_MIN_N == min_references_for(0.20) == 9
    assert policy.UP_MIN_N == min_references_for(0.05) == 39
    assert min_references_for(policy.DOWN_ALPHA) == policy.PROBATION_MIN_N


def test_every_reachable_pair_stays_below_the_enforcement_rate():
    reachable = [
        (shorts, days)
        for shorts in range(policy.SHORTS_FLOOR, policy.SHORTS_CEILING + 1)
        for days in range(policy.LF_DAYS_FLOOR, policy.LF_DAYS_CEILING_PER_WEEK + 1)
        if shorts * days <= policy.JOINT_WEEKLY_SHORTS_CEILING
    ]
    assert reachable
    assert policy.ENFORCEMENT_SHORTS_PER_LF == 14
    for shorts, days in reachable:
        assert shorts < policy.ENFORCEMENT_SHORTS_PER_LF
        assert shorts * days / days < policy.ENFORCEMENT_SHORTS_PER_LF


def test_policy_states_it_is_code_not_config():
    doc = policy.__doc__ or ""
    assert "REVISIONS IN CODE" in doc
    assert "never per-run config" in " ".join(doc.split())


def test_shorts_hour_rail_is_a_stated_proposal():
    assert (policy.SHORTS_HOUR_FLOOR, policy.SHORTS_HOUR_CEILING) == (8, 18)
    assert policy.SHORTS_HOUR_FLOOR < policy.SHORTS_HOUR_CEILING
