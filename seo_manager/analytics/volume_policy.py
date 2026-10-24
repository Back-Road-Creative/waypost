"""Named policy rails for the volume and cadence controller.

These are policy REVISIONS IN CODE, never per-run config: a number change is a
new ``POLICY_REV`` in a reviewed commit, not a flag, an env var or an operator
override. Shorts per LF, LF days per week and shorts posting times are decided
by the controller from analytics every night; the numbers here are only the
rails that decision cannot cross.

Pure module: constants and one derived value, no I/O.

Sample sizes are not typed in here. ``PROBATION_MIN_N``, ``DOWN_MIN_N`` and
``UP_MIN_N`` come from ``anomaly_stats.min_references_for`` at the alphas
below, so the arithmetic (9 at 0.20, 39 at 0.05) stays in one place.
"""

from .anomaly_stats import min_references_for

POLICY_REV = 1

# Shorts per LF. The floor is the long-standing default of the video
# pipeline's ``min_shorts_per_lf`` setting.
SHORTS_FLOOR = 7
SHORTS_CEILING = 10

# LF days.
LF_DAYS_FLOOR = 1
LF_DAYS_CEILING_PER_REGION = 3
LF_DAYS_CEILING_PER_WEEK = 4

# Sum over regions of LF/week x shorts/LF.
JOINT_WEEKLY_SHORTS_CEILING = 30

# A shorts-per-LF rate that platforms treat as spam-like and enforce against.
# No reachable (shorts_per_lf, lf_days) pair may meet it.
ENFORCEMENT_SHORTS_PER_LF = 14

# Marginal-tail rule, as used by the video pipeline's volume tracking
# (default K and floor fraction). Down: the bottom TAIL_K shorts each sit
# below DOWN_FRACTION x median. Up: each sits at or above UP_FRACTION x median.
TAIL_K = 2
DOWN_FRACTION = 0.25
UP_FRACTION = 0.75

COOLDOWN_DAYS: dict[str, int] = {"shorts_per_lf": 14, "lf_days": 28, "shorts_times": 14}

# Evidence sufficiency, derived (never typed).
DOWN_ALPHA = 0.20
UP_ALPHA = 0.05
DOWN_MIN_N = min_references_for(DOWN_ALPHA)
UP_MIN_N = min_references_for(UP_ALPHA)
PROBATION_MIN_N = DOWN_MIN_N
UP_CONFIRM_COHORTS = 2

# A short's day-7 views are settled seven days after it posts.
SETTLE_DAYS = 7

PROBATION_DAYS = 28
PROBATION_MAD_MULTIPLE = 2.0

# No new settled cohort for this long, or analytics not ok: step toward
# last_validated.
BLIND_DAYS = 21

LOCKOUT_CAP_DAYS = 112
LEARNED_CEILING_DAYS = 90

# An LF-day step up needs at least this many ready LFs when depth is measured.
# A conservative proposed value, not a measured one.
SUPPLY_MIN_READY_DEPTH = 2

# Shorts posting hours (brand-timezone clock hours) a region may be moved to: the
# earliest and latest hour in the shorts pools the pipeline uses. A proposed
# range, not a measured one.
SHORTS_HOUR_FLOOR = 8
SHORTS_HOUR_CEILING = 18

KNOBS = ("shorts_per_lf", "lf_days", "shorts_times")
