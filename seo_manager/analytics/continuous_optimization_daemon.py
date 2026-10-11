"""
Continuous Optimization Daemon.

Automates the SEO improvement feedback loop:
1. Periodically sync YouTube analytics
2. Re-analyze performance patterns
3. Update generation templates based on learnings
4. Observe how the pooled rate moved since the previous cycle

Step 4 is an OBSERVATION, not a validation. Nothing in this service runs a
controlled before/after: there is no control group, no holdout, and no matching
on content age, audience, topic, exposure or sample size -- each of which can
move a rate on its own. A settings-file write is not a measured improvement.

Two further limits are recorded rather than worked around, because working
around either needs data nobody collects yet:

* **Every reported movement carries its denominator.** A delta between two
  rates each resting on a handful of impressions is noise wearing a
  percentage. `RateEvidence` binds a rate to the sample size it was measured
  on so no path can pass one without the other, `classify_ctr_movement`
  refuses a direction below `MIN_IMPRESSIONS_FOR_DIRECTION` on either side --
  saying `ctr_trend_indeterminate` out loud, the way `ctr_unmeasured` already
  refuses the None case -- and the history entry stores the denominators so
  the next cycle inherits the evidence and not just the verdict.
* **The two rates are running averages, not periods.** `get_summary()` is
  called with no date bounds, so each side pools every event in the store.
  The later number contains the earlier one; the difference is damped by
  everything already counted and shrinks as the store grows, whatever
  happened recently. `metrics["window"]` states this (`cumulative_to_date`).
  Comparing genuinely disjoint periods would mean passing bounds here and
  deciding what a period is -- a product decision nobody has made.
"""

import argparse
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .evidence_age import age_days as _age_days
from .recommendation_auto_applier import RecommendationAutoApplier

logger = logging.getLogger(__name__)


class OptimizationCycleType(Enum):
    """Types of optimization cycles."""

    ANALYTICS_SYNC = "analytics_sync"
    PATTERN_ANALYSIS = "pattern_analysis"
    TEMPLATE_UPDATE = "template_update"
    # Was VALIDATION. The step compares this cycle's pooled rate with the
    # previous cycle's; it validates nothing, and the name was the claim.
    TREND_OBSERVATION = "trend_observation"


class SyncOutcome(Enum):
    """What the analytics-sync step actually did.

    ``success`` alone could not tell these apart: a daemon with no collector, a
    daemon with nothing to collect, and a batch in which every single video
    failed all returned ``success=True`` with only a ``changes_made`` string to
    distinguish them. A caller deciding whether it holds fresh data branches on
    this.
    """

    #: No ``youtube_sync`` was supplied -- nothing was attempted.
    UNCONFIGURED = "unconfigured"
    #: A collector, but no videos to ask about. See ``video_mapping_state``:
    #: a new account with nothing published and a mapping file that went
    #: missing are both empty, and are not the same event.
    EMPTY = "empty"
    #: Every requested video came back synced.
    FRESH = "fresh"
    #: Some came back synced, some did not.
    PARTIAL = "partial"
    #: Videos were requested and none came back synced.
    FAILED = "failed"


#: Why a sync found no videos. Reported in ``metrics["video_mapping_state"]``.
MAPPING_NOT_CONFIGURED = "no_mapping_file_configured"
MAPPING_FILE_MISSING = "mapping_file_missing"
MAPPING_FILE_EMPTY = "mapping_file_empty"

#: What the trend step's numbers are. A difference between two periods, with
#: nothing controlled: an observation to look at, never evidence of an effect.
OBSERVATION_UNVALIDATED_TREND = "unvalidated_trend"

#: What window a rate covers. ``get_summary()`` is called with no date bounds,
#: so in practice both sides of the comparison are pooled averages over every
#: event in the store -- a running average, not a period. A change in a running
#: average is damped by everything already in it, and a reader weighing two
#: cycles has to know that is what the two numbers are.
WINDOW_CUMULATIVE_TO_DATE = "cumulative_to_date"
WINDOW_BOUNDED = "bounded"

#: Impressions required on EACH side before a movement is given a direction.
#:
#: This is a floor, NOT a significance test, and it was chosen rather than
#: derived -- there is no right number here, but leaving it unstated is wrong.
#: The reasoning: the standard error of a rate p over n impressions is
#: sqrt(p(1-p)/n). At a typical rate of ~4%, n=100 gives SE ~2
#: points, so a 95% interval spans roughly 0%-8% -- wider than the rate, and a
#: "25% improvement" between two such numbers is indistinguishable from noise.
#: At n=1000 the SE is ~0.6 points, which is still loose but is the point where
#: the DIRECTION of a sizeable move stops being a coin flip. Raising it costs
#: nothing but patience; lowering it buys confident labels that the arithmetic
#: cannot support.
MIN_IMPRESSIONS_FOR_DIRECTION = 1000

#: ``changes_made`` tokens for the trend step. Each names a distinct thing that
#: happened; none of them is silence, because an empty list reads as a healthy
#: cycle and "we could not tell" is not that.
CTR_UNMEASURED = "ctr_unmeasured"
CTR_TREND_INDETERMINATE = "ctr_trend_indeterminate"
CTR_SAMPLE_SIZE_UNKNOWN = "ctr_sample_size_unknown"
CTR_BASELINE_ZERO = "ctr_baseline_zero_change_undefined"
CTR_FIRST_OBSERVATION = "first_observation_baseline"

#: ``metrics["evidence_sufficiency"]`` -- why a direction was or was not given.
EVIDENCE_SUFFICIENT = "sufficient"
EVIDENCE_BELOW_FLOOR = "below_floor"
EVIDENCE_SAMPLE_UNKNOWN = "sample_size_unknown"
EVIDENCE_UNMEASURED = "unmeasured"
EVIDENCE_CHANGE_UNDEFINED = "relative_change_undefined"
EVIDENCE_NO_BASELINE = "no_prior_observation"

#: File in ``state_dir`` holding the LAST cycle's run record. Its absence is
#: the only honest way to say "no cycle has ever been recorded": a cron that
#: never fired and a cron that fired nightly and found nothing to do left
#: identical traces before this existed.
RUN_RECORD_FILENAME = "last_run.json"

#: How a cycle finished. ``metrics`` say what a cycle SAW; these say whether it
#: got to the end at all, which is a different question and was unanswerable.
RUN_COMPLETED = "completed"
RUN_STEPS_FAILED = "steps_failed"
RUN_RAISED = "raised"
#: A record on disk that could not be parsed. NOT the same as no record: one
#: says the evidence is damaged, the other says there is none.
RUN_UNREADABLE = "unreadable"
#: ``get_status()["last_cycle_run_state"]`` when nothing has ever been written.
RUN_NEVER_RECORDED = "never_recorded"
RUN_RECORDED = "recorded"

#: Why the analytics collector is absent. ``SyncOutcome.UNCONFIGURED`` says a
#: sync was not attempted; these say which input was missing, because "wire the
#: thing that is not wired" is not an actionable report.
COLLECTOR_NO_CREDENTIALS = "no_youtube_credentials_configured"
#: A credential path was given and building the collector from it failed.
#: Distinct from "not configured": somebody configured it and it did not work.
COLLECTOR_UNAVAILABLE = "youtube_collector_could_not_be_built"
#: A daemon constructed directly with ``youtube_sync=None`` and no reason. The
#: caller knows why and did not say; that is recorded as unrecorded rather than
#: guessed at.
COLLECTOR_REASON_UNRECORDED = "no_collector_supplied_reason_unrecorded"

#: ``changes_made`` tokens for the template step.
LEARNED_VALUES_CHANGED = "learned_values_changed"
LEARNED_VALUES_UNCHANGED = "learned_values_unchanged"

#: Keys of the learned config that move every cycle by construction, and so
#: cannot be evidence that anything was learned.
_LEARNED_CONFIG_VOLATILE_KEYS = frozenset({"generated_at"})


@dataclass(frozen=True)
class RateEvidence:
    """A pooled click rate together with the evidence it rests on.

    The rate and its denominator travel as one value so that no code path can
    hold a rate without also holding what it was measured on. That is the
    defect it exists to prevent: a direction was computed from two bare floats and
    appended to ``optimization_history``, where the next cycle read it back
    with nothing to weigh it against.

    ``impressions is None`` means nobody reported a denominator. That is a
    third state, not a small one and not a large one: "unknown" is never
    promoted to "adequate".
    """

    ctr: Optional[float]
    impressions: Optional[int]
    clicks: Optional[int] = None

    @property
    def is_measured(self) -> bool:
        """Whether a rate was measured at all. A measured 0.0 counts."""
        return self.ctr is not None

    def supports_a_direction(self, floor: int) -> Optional[bool]:
        """True, False, or None when the denominator was never reported."""
        if self.impressions is None:
            return None
        return self.impressions >= floor


def _direction_token(change_percent: float) -> str:
    """Direction only. How much of it matters is the reader's call."""
    if change_percent > 0:
        return f"ctr_trend_up_{change_percent:.1f}%"
    if change_percent < 0:
        return f"ctr_trend_down_{abs(change_percent):.1f}%"
    return "ctr_trend_flat"


def classify_ctr_movement(
    current: RateEvidence,
    previous: RateEvidence,
    floor: int = MIN_IMPRESSIONS_FOR_DIRECTION,
) -> Tuple[List[str], str, Optional[float]]:
    """Say what, if anything, may be claimed about a movement between two rates.

    Pure: no clock, no I/O, no state, so the rule can be exercised directly
    rather than only through a cycle. Returns the tokens to record, the
    evidence verdict, and the relative change where one is defined.

    The order of refusals is deliberate:

    1. Either side unmeasured -- there is no movement to describe. A period
       nobody measured, following one with a real rate, is not a 100% decline.
    2. A baseline of a measured zero -- a relative change has no denominator.
       This case used to fall off the end of an ``elif last_ctr > 0`` chain and
       record *nothing at all*, so a cycle that could compute nothing wrote a
       history entry indistinguishable from a healthy one.
    3. A denominator below the floor on either side -- known, and known to be
       too thin to carry a direction. Reported as indeterminate.
    4. A denominator nobody reported. The direction is still recorded, because
       ``AnalyticsTracker.get_summary`` always supplies ``total_impressions``
       and so this state is unreachable from the shipped pipeline; it arises
       only for a hand-built summary. But ``ctr_sample_size_unknown`` travels
       beside it and the verdict is never ``sufficient`` -- unknown is its own
       state, neither quietly promoted to adequate nor used to suppress a
       direction a real caller can always evidence.

    Only past all four is a direction claimed on its own.
    """
    if not current.is_measured or not previous.is_measured:
        return [CTR_UNMEASURED], EVIDENCE_UNMEASURED, None

    if previous.ctr == 0:
        return [CTR_BASELINE_ZERO], EVIDENCE_CHANGE_UNDEFINED, None

    change_percent = ((current.ctr - previous.ctr) / previous.ctr) * 100
    verdicts = [
        current.supports_a_direction(floor),
        previous.supports_a_direction(floor),
    ]

    if False in verdicts:
        return [CTR_TREND_INDETERMINATE], EVIDENCE_BELOW_FLOOR, change_percent

    tokens = [_direction_token(change_percent)]
    if None in verdicts:
        tokens.append(CTR_SAMPLE_SIZE_UNKNOWN)
        return tokens, EVIDENCE_SAMPLE_UNKNOWN, change_percent

    return tokens, EVIDENCE_SUFFICIENT, change_percent


def changed_learned_sections(previous: Optional[Dict], current: Dict) -> List[str]:
    """Which learned sections differ, ignoring keys that move every cycle.

    ``generated_at`` is regenerated on every call, so comparing it would report
    a change every cycle by construction -- which is precisely the "a write is
    an improvement" claim this function exists to stop.
    """
    if previous is None:
        return sorted(set(current) - _LEARNED_CONFIG_VOLATILE_KEYS)
    return sorted(
        key
        for key in set(previous) | set(current)
        if key not in _LEARNED_CONFIG_VOLATILE_KEYS
        and previous.get(key) != current.get(key)
    )


@dataclass(frozen=True)
class CycleRunRecord:
    """Evidence that one cycle was attempted, and how it ended.

    Every field defaults to ``None`` on purpose. A record read back from a
    file written by an older release is missing whatever that release did not
    write, and a missing field must read back as *unknown* -- never as a zero,
    an empty result, or a timestamp of now. That substitution is the defect
    this record exists to remove, so it may not be reintroduced inside it.

    The age of the run is computed on read, never stored: an age written to
    disk is wrong one second later. How old is too old is deliberately not
    here -- staleness is reported, and judging it is a product decision.
    """

    started_at: Optional[str] = None
    #: ``None`` when the cycle never reached the end -- it did not finish.
    finished_at: Optional[str] = None
    #: ``None`` when nobody recorded which mode the cycle ran in.
    dry_run: Optional[bool] = None
    completion: Optional[str] = None
    #: ``None`` when no sync step reported one, e.g. a cycle that raised
    #: before the first step returned. Unknown, not ``unconfigured``.
    sync_outcome: Optional[str] = None
    failed_steps: Tuple[str, ...] = ()
    error: Optional[str] = None

    @property
    def age_days(self) -> Optional[float]:
        """Days since the cycle started, or ``None`` when that is unknowable."""
        return _age_days(self.started_at)

    def as_status(self) -> Dict[str, Any]:
        """The record plus its age, for a status surface. Not what is stored."""
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "dry_run": self.dry_run,
            "completion": self.completion,
            "sync_outcome": self.sync_outcome,
            "failed_steps": list(self.failed_steps),
            "error": self.error,
            "age_days": self.age_days,
        }


def read_last_run(state_dir) -> Optional[CycleRunRecord]:
    """The last cycle's record, or ``None`` when no cycle has ever run.

    ``None`` is the whole point of this function: an operator, a health check
    or a sibling service asking "has the scheduler ever fired?" gets an answer
    that cannot be confused with a cycle that ran and changed nothing.
    """
    path = Path(state_dir) / RUN_RECORD_FILENAME
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("run record is not a JSON object")
    except (OSError, ValueError) as e:
        logger.warning(f"could not read the run record at {path}: {e}")
        # Damaged evidence is its own state. Returning None here would report
        # a daemon that has been running for a year as having never run.
        return CycleRunRecord(completion=RUN_UNREADABLE)
    known = {f.name for f in fields(CycleRunRecord)}
    payload = {k: v for k, v in data.items() if k in known}
    steps = payload.get("failed_steps")
    if isinstance(steps, list):
        payload["failed_steps"] = tuple(steps)
    return CycleRunRecord(**payload)


@dataclass
class OptimizationResult:
    """Result of a single optimization cycle."""

    cycle_type: OptimizationCycleType
    timestamp: str
    success: bool
    changes_made: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    #: Set by the analytics-sync step only; ``None`` for every other step,
    #: which define no outcome vocabulary of their own.
    sync_outcome: Optional[SyncOutcome] = None


@dataclass
class DaemonState:
    """Persistent state for the daemon."""

    last_analytics_sync: Optional[str] = None
    last_pattern_analysis: Optional[str] = None
    last_template_update: Optional[str] = None
    total_videos_synced: int = 0
    total_patterns_learned: int = 0
    total_templates_updated: int = 0
    optimization_history: List[Dict] = field(default_factory=list)

    # Learned optimal values
    optimal_title_length_min: int = 50
    optimal_title_length_max: int = 70
    top_performing_tags: List[str] = field(default_factory=list)
    thumbnail_style_performance: Dict[str, float] = field(default_factory=dict)


class ContinuousOptimizationDaemon:
    """
    Daemon that continuously improves SEO based on performance data.

    Workflow:
    1. Every 24 hours: Sync YouTube analytics for all published videos
    2. After sync: Re-analyze patterns to identify what's working
    3. Apply learnings: Update generation templates and recommendations
    4. Observe: record how the pooled rate moved since the last cycle. This
       is a trend across time, NOT a controlled before/after, and it is
       never evidence that step 3 caused the movement.

    Example usage:
        daemon = ContinuousOptimizationDaemon(
            manager=seo_manager,
            youtube_sync=youtube_analytics_sync,
            state_dir=Path("./data/optimization")
        )

        # Run single cycle
        daemon.run_optimization_cycle()

        # Or run as daemon
        daemon.run_forever(interval_hours=24)
    """

    DEFAULT_SYNC_INTERVAL_HOURS = 24
    DEFAULT_MIN_VIEWS_FOR_ANALYSIS = 100
    # DEFAULT_MIN_VIDEOS_FOR_LEARNING = 10 and DEFAULT_CONFIDENCE_THRESHOLD =
    # 0.7 lived here and were read by nothing. Both are product decisions --
    # how much data is enough to learn from, how sure is sure enough -- that
    # nobody made and nothing in this service computes. Deleted rather than
    # wired up: a number shaped like a policy needs a policy behind it.

    def __init__(
        self,
        manager,
        youtube_sync=None,
        state_dir: Optional[Path] = None,
        video_mapping_file: Optional[Path] = None,
        sync_interval_hours: int = DEFAULT_SYNC_INTERVAL_HOURS,
        min_views_for_analysis: int = DEFAULT_MIN_VIEWS_FOR_ANALYSIS,
        dry_run: bool = False,
        sync_unconfigured_reason: Optional[str] = None,
    ):
        """
        Initialize the daemon.

        Args:
            manager: SEOManager instance with analytics and optimizer
            youtube_sync: YouTubeAnalyticsSync instance (optional)
            state_dir: Directory for persistent state
            video_mapping_file: JSON file mapping video_id -> content_id
            sync_interval_hours: How often to sync analytics
            min_views_for_analysis: Minimum views to include in analysis
            dry_run: If True, don't persist changes
            sync_unconfigured_reason: Why no ``youtube_sync`` was supplied,
                when the caller knows. The daemon can see that its mapping
                file is missing; it cannot see that a credential path was
                never passed to ``build_daemon``, so that half travels in
                from the builder rather than being guessed at here.
        """
        self.manager = manager
        self.youtube_sync = youtube_sync
        self.state_dir = state_dir or Path("./data/optimization")
        self.video_mapping_file = video_mapping_file
        self.sync_interval_hours = sync_interval_hours
        self.min_views_for_analysis = min_views_for_analysis
        self.dry_run = dry_run
        self.sync_unconfigured_reason = sync_unconfigured_reason

        # Ensure state directory exists
        self.state_dir.mkdir(parents=True, exist_ok=True)

        # Load state
        self.state = self._load_state()

    def run_optimization_cycle(self) -> List[OptimizationResult]:
        """Run one cycle, and leave evidence on disk that it was attempted.

        The record is written on every exit, including the one where a step
        raised: a cycle that blew up is not a cycle that never happened, and
        before this an operator could not tell either of them from a cron that
        never fired. It is written for a dry run too -- the shipped CLI is dry
        unless ``--apply``, so gating the evidence on ``--apply`` would leave
        the default nightly schedule exactly as unprovable as it was.

        Returns:
            List of OptimizationResult for each step
        """
        started_at = datetime.now().isoformat()
        results: List[OptimizationResult] = []
        try:
            results = self._run_cycle_steps(results)
        except BaseException as e:
            # Recorded, then re-raised: swallowing it here would turn a crash
            # into a silent success, which is the disease, not the cure.
            self._write_run_record(self._build_run_record(started_at, results, error=e))
            raise
        self._write_run_record(
            self._build_run_record(started_at, results, finished=True)
        )
        return results

    def _build_run_record(
        self,
        started_at: str,
        results: List[OptimizationResult],
        finished: bool = False,
        error: Optional[BaseException] = None,
    ) -> CycleRunRecord:
        """Describe the run from what actually happened, inventing nothing."""
        failed = tuple(r.cycle_type.value for r in results if not r.success)
        if error is not None:
            completion = RUN_RAISED
        elif failed:
            completion = RUN_STEPS_FAILED
        else:
            completion = RUN_COMPLETED
        # The outcome of the step that defines one. Absent -- because the
        # cycle died before the sync returned -- stays absent.
        outcome = next(
            (r.sync_outcome.value for r in results if r.sync_outcome is not None),
            None,
        )
        return CycleRunRecord(
            started_at=started_at,
            finished_at=datetime.now().isoformat() if finished else None,
            dry_run=self.dry_run,
            completion=completion,
            sync_outcome=outcome,
            failed_steps=failed,
            error=None if error is None else str(error),
        )

    def _write_run_record(self, record: CycleRunRecord) -> None:
        """Persist the run record, dry run included.

        ``_save_state`` skips a dry run because a dry run must not persist
        what it learned. This is not a learning: it is the only thing that
        distinguishes a schedule that fires from one that does not, and a
        failure to write it is logged loudly rather than failing the cycle --
        bookkeeping must not be the thing that turns a good run red.
        """
        path = self.state_dir / RUN_RECORD_FILENAME
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            with open(path, "w") as f:
                json.dump(asdict(record), f, indent=2)
        except OSError as e:
            logger.error(f"could not write the run record to {path}: {e}")

    def _run_cycle_steps(
        self, results: List[OptimizationResult]
    ) -> List[OptimizationResult]:
        """The cycle itself. Separated so every exit records that it ran."""

        logger.info("=" * 60)
        logger.info("Starting optimization cycle")
        logger.info("=" * 60)

        # Step 1: Sync YouTube Analytics
        sync_result = self._sync_youtube_analytics()
        results.append(sync_result)

        if not sync_result.success:
            logger.warning("Analytics sync failed, skipping remaining steps")
            return results

        # Step 2: Re-analyze patterns
        analysis_result = self._analyze_patterns()
        results.append(analysis_result)

        if not analysis_result.success:
            logger.warning("Pattern analysis failed, skipping template update")
            return results

        # Step 3: Update templates based on learnings
        update_result = self._update_templates()
        results.append(update_result)

        # Step 4: Observe the trend since the previous cycle. Nothing here
        # gates on it, and nothing here claims step 3 caused it.
        results.append(self._observe_ctr_trend())

        # Save state
        self._save_state()

        # Log summary
        self._log_cycle_summary(results)

        return results

    def run_forever(self, interval_hours: Optional[int] = None):
        """
        Run the daemon continuously.

        Args:
            interval_hours: Override default sync interval
        """
        interval = interval_hours or self.sync_interval_hours

        logger.info(f"Starting continuous optimization daemon (interval: {interval}h)")

        while True:
            try:
                # Check if we should run
                if self._should_run_cycle():
                    self.run_optimization_cycle()
                else:
                    next_run = self._get_next_run_time()
                    logger.debug(f"Next optimization cycle at: {next_run}")

                # Sleep for 1 hour and re-check
                time.sleep(3600)

            except KeyboardInterrupt:
                logger.info("Daemon stopped by user")
                break
            except Exception as e:
                logger.error(f"Daemon error: {e}")
                time.sleep(600)  # Wait 10 min before retry

    def _sync_youtube_analytics(self) -> OptimizationResult:
        """Sync analytics from YouTube."""
        logger.info("[1/4] Syncing YouTube Analytics...")

        timestamp = datetime.now().isoformat()
        changes = []
        metrics = {}

        try:
            if not self.youtube_sync:
                reasons = self._unconfigured_reasons()
                logger.info(
                    "YouTube sync not configured (%s): no fresh analytics were "
                    "collected this cycle",
                    ", ".join(reasons),
                )
                return OptimizationResult(
                    cycle_type=OptimizationCycleType.ANALYTICS_SYNC,
                    timestamp=timestamp,
                    success=True,
                    # Existing marker kept verbatim; the outcome is what a
                    # caller should branch on. The reasons travel beside it:
                    # `unconfigured` alone tells an operator nothing they can
                    # act on, and the two missing inputs are different jobs.
                    changes_made=["skipped_no_youtube_sync", *reasons],
                    metrics={"unconfigured_reasons": reasons},
                    sync_outcome=SyncOutcome.UNCONFIGURED,
                )

            # Load video mapping
            video_mapping = self._load_video_mapping()

            if not video_mapping:
                mapping_state = self._video_mapping_state()
                logger.warning(f"No videos to sync: {mapping_state}")
                return OptimizationResult(
                    cycle_type=OptimizationCycleType.ANALYTICS_SYNC,
                    timestamp=timestamp,
                    success=True,
                    changes_made=["no_videos_to_sync", mapping_state],
                    # An account with nothing published yet and a collector
                    # pointed at a mapping that vanished are both "no videos".
                    # Which one it was is the difference between "wait" and
                    # "somebody broke the collector", so it is reported.
                    metrics={"video_mapping_state": mapping_state},
                    sync_outcome=SyncOutcome.EMPTY,
                )

            # Batch sync
            requested = len(video_mapping)
            logger.info(f"Syncing analytics for {requested} videos")
            results = self.youtube_sync.batch_sync(self.manager, video_mapping)

            # Count successes
            synced = sum(1 for r in results if r["success"])
            failed = len(results) - synced
            # A video we asked about that the batch reported nothing at all
            # for is not synced either -- counting only `failed` would let it
            # disappear from both sides of the ledger.
            missing = requested - len(results)

            changes.append(f"synced_{synced}_videos")
            if failed > 0:
                changes.append(f"failed_{failed}_videos")
            if missing > 0:
                changes.append(f"missing_{missing}_videos")

            metrics["videos_requested"] = requested
            metrics["videos_synced"] = synced
            metrics["videos_failed"] = failed
            metrics["videos_missing"] = missing

            if synced == requested:
                outcome = SyncOutcome.FRESH
            elif synced == 0:
                # Every video we asked about came back failed or not at all.
                outcome = SyncOutcome.FAILED
            else:
                outcome = SyncOutcome.PARTIAL
            metrics["sync_outcome"] = outcome.value

            # A sync that collected nothing is not a sync: stamping the clock
            # here would park the next attempt a full interval away and let
            # `get_status` advertise an account as synced on the strength of a
            # batch that returned no data.
            if synced:
                self.state.last_analytics_sync = timestamp
                self.state.total_videos_synced += synced

            # No threshold on `synced/requested`: what fraction of coverage is
            # enough to learn from is a product decision nobody has made, so
            # the count and the outcome are reported and the caller decides.
            if outcome is SyncOutcome.FRESH:
                logger.info(f"✓ Synced {synced}/{requested} videos")
            else:
                logger.warning(
                    f"Analytics sync {outcome.value}: {synced}/{requested} "
                    f"videos synced ({failed} failed, {missing} not reported)"
                )

            return OptimizationResult(
                cycle_type=OptimizationCycleType.ANALYTICS_SYNC,
                timestamp=timestamp,
                # A batch that collected nothing is a failed sync, not a
                # success with a caveat buried in `changes_made`.
                success=outcome is not SyncOutcome.FAILED,
                changes_made=changes,
                metrics=metrics,
                sync_outcome=outcome,
            )

        except Exception as e:
            logger.error(f"Analytics sync failed: {e}")
            return OptimizationResult(
                cycle_type=OptimizationCycleType.ANALYTICS_SYNC,
                timestamp=timestamp,
                success=False,
                error=str(e),
                sync_outcome=SyncOutcome.FAILED,
            )

    def _unconfigured_reasons(self) -> List[str]:
        """Which inputs a real sync is still waiting for.

        Each missing input is named, not just the first: telling an operator
        to supply a credential path and then telling them, a day later, that a
        mapping file is also missing is two nights lost to one report.
        """
        reasons = []
        if self.sync_unconfigured_reason:
            reasons.append(self.sync_unconfigured_reason)
        if self.video_mapping_file is None:
            reasons.append(MAPPING_NOT_CONFIGURED)
        # Never an empty list: silence here would read as "configured", which
        # is the exact confusion SyncOutcome was introduced to end.
        return reasons or [COLLECTOR_REASON_UNRECORDED]

    def _video_mapping_state(self) -> str:
        """Say WHY there were no videos to sync."""
        if self.video_mapping_file is None:
            return MAPPING_NOT_CONFIGURED
        if not self.video_mapping_file.exists():
            return MAPPING_FILE_MISSING
        return MAPPING_FILE_EMPTY

    def _analyze_patterns(self) -> OptimizationResult:
        """Re-analyze performance patterns."""
        logger.info("[2/4] Analyzing performance patterns...")

        timestamp = datetime.now().isoformat()
        changes = []
        metrics = {}

        try:
            # Get optimizer
            optimizer = getattr(self.manager, "optimizer", None)
            if not optimizer:
                from .seo_optimizer import SEOOptimizer

                optimizer = SEOOptimizer(self.manager)

            # Run pattern analysis
            patterns = optimizer.analyze_patterns(min_views=self.min_views_for_analysis)

            # Extract key learnings

            # 1. Top performing tags
            if patterns.get("tags"):
                top_tags = [p.value for p in patterns["tags"][:20]]
                old_tags = self.state.top_performing_tags
                new_tags = [t for t in top_tags if t not in old_tags]

                if new_tags:
                    changes.append(f"learned_{len(new_tags)}_new_top_tags")
                    metrics["new_top_tags"] = new_tags[:5]

                self.state.top_performing_tags = top_tags

            # 2. Optimal title length
            min_len, max_len = optimizer.get_optimal_title_length()

            if (
                min_len != self.state.optimal_title_length_min
                or max_len != self.state.optimal_title_length_max
            ):
                changes.append(f"updated_title_length_{min_len}-{max_len}")
                self.state.optimal_title_length_min = min_len
                self.state.optimal_title_length_max = max_len

            metrics["optimal_title_length"] = f"{min_len}-{max_len}"

            # 3. Pattern counts
            total_patterns = sum(len(v) for v in patterns.values())
            self.state.total_patterns_learned = total_patterns
            metrics["total_patterns"] = total_patterns

            self.state.last_pattern_analysis = timestamp

            if changes:
                logger.info(f"✓ Learned {len(changes)} new patterns")
            else:
                logger.info("✓ No new patterns (stable)")

            return OptimizationResult(
                cycle_type=OptimizationCycleType.PATTERN_ANALYSIS,
                timestamp=timestamp,
                success=True,
                changes_made=changes,
                metrics=metrics,
            )

        except Exception as e:
            logger.error(f"Pattern analysis failed: {e}")
            return OptimizationResult(
                cycle_type=OptimizationCycleType.PATTERN_ANALYSIS,
                timestamp=timestamp,
                success=False,
                error=str(e),
            )

    def _update_templates(self) -> OptimizationResult:
        """Update generation templates based on learnings."""
        logger.info("[3/4] Updating generation templates...")

        timestamp = datetime.now().isoformat()
        changes = []
        metrics = {}

        try:
            if self.dry_run:
                logger.info("Dry run - skipping template updates")
                return OptimizationResult(
                    cycle_type=OptimizationCycleType.TEMPLATE_UPDATE,
                    timestamp=timestamp,
                    success=True,
                    changes_made=["dry_run_skipped"],
                    metrics={},
                )

            # Update template configuration file
            template_config = self._generate_template_config()
            config_path = self.state_dir / "learned_config.json"

            # What changed is decided BEFORE the write, against what is already
            # on disk. This step used to append "updated_learned_config" and
            # "updated_recommended_tags" unconditionally, so a daemon that
            # learned nothing for a month still reported sixty template
            # "changes" -- a file write and the mere existence of a tag list,
            # both counted as learning outcomes. A settings-file write
            # is not a measured improvement.
            changed_sections = changed_learned_sections(
                self._read_learned_config(config_path), template_config
            )

            with open(config_path, "w") as f:
                json.dump(template_config, f, indent=2)

            # The write is an I/O event, so it is recorded as one, in metrics.
            metrics["config_path"] = str(config_path)
            metrics["config_written"] = True
            metrics["changed_sections"] = changed_sections
            metrics["recommended_tags_count"] = len(self.state.top_performing_tags)

            if changed_sections:
                changes.append(LEARNED_VALUES_CHANGED)
            else:
                # Explicit, never an empty list: "this cycle learned nothing
                # new" is a result, and an empty changes list reads as a
                # healthy one.
                changes.append(LEARNED_VALUES_UNCHANGED)

            self.state.last_template_update = timestamp
            self.state.total_templates_updated += 1

            logger.info(
                f"wrote {config_path.name}; learned values changed in "
                f"{len(changed_sections)} section(s)"
            )

            return OptimizationResult(
                cycle_type=OptimizationCycleType.TEMPLATE_UPDATE,
                timestamp=timestamp,
                success=True,
                changes_made=changes,
                metrics=metrics,
            )

        except Exception as e:
            logger.error(f"Template update failed: {e}")
            return OptimizationResult(
                cycle_type=OptimizationCycleType.TEMPLATE_UPDATE,
                timestamp=timestamp,
                success=False,
                error=str(e),
            )

    def _observe_ctr_trend(self) -> OptimizationResult:
        """Record how the pooled click rate moved since the previous cycle.

        This is NOT a validation, and was called ``_validate_improvements``
        until the name was retired. It compares one summary average with the
        one before it. There is no control group, no holdout, and no matching
        on content age, audience, topic, exposure or sample size -- any of
        which moves a rate on its own -- so the delta cannot say whether the
        template changes this cycle wrote caused anything. The result carries
        ``observation_kind`` and a ``causal_attribution`` of ``None`` so no
        downstream reader has to infer that from a method name.
        """
        logger.info("[4/4] Observing click-rate trend (not a validation)...")

        timestamp = datetime.now().isoformat()
        changes = []
        metrics = {}

        try:
            # Get analytics summary
            summary = self.manager.analytics.get_summary()

            # Compare to previous period
            performance = summary.get("performance_metrics", {})
            window = summary.get("period") or {}

            # An unmeasured rate arrives as None (tracker.get_summary) and
            # stays None: defaulting it to 0 here would re-fabricate exactly
            # the measurement the tracker refused to make. The denominator
            # travels with it -- a rate without its sample size is a defect
            # that `RateEvidence` exists to prevent by binding the two.
            current = RateEvidence(
                ctr=performance.get("avg_ctr"),
                impressions=performance.get("total_impressions"),
                clicks=performance.get("total_clicks"),
            )
            current_views = performance.get("total_views", 0)

            metrics["current_avg_ctr"] = current.ctr
            metrics["current_total_views"] = current_views
            # The evidence, recorded beside the number it supports and carried
            # into the history entry, so the NEXT cycle can weigh this rate
            # instead of taking it on trust.
            metrics["current_impressions"] = current.impressions
            metrics["current_clicks"] = current.clicks
            metrics["window"] = {
                "start_date": window.get("start_date"),
                "end_date": window.get("end_date"),
                "kind": (
                    WINDOW_BOUNDED
                    if window.get("start_date") or window.get("end_date")
                    else WINDOW_CUMULATIVE_TO_DATE
                ),
            }
            # Provenance, stated in the record rather than left to the reader.
            metrics["observation_kind"] = OBSERVATION_UNVALIDATED_TREND
            # Deliberately unset, and deliberately not omitted: nothing here
            # observed what acting on a recommendation did, so what caused a
            # movement is not determinable from this data. `None` is the
            # finding; a plausible-looking attribution would be the fabrication.
            metrics["causal_attribution"] = None
            metrics["min_impressions_for_direction"] = MIN_IMPRESSIONS_FOR_DIRECTION

            # Compare with the previous cycle's rate. A difference, not an
            # effect -- and only where the evidence can carry even that much.
            if self.state.optimization_history:
                last_metrics = self.state.optimization_history[-1].get("metrics", {})
                previous = RateEvidence(
                    ctr=last_metrics.get("current_avg_ctr"),
                    # Absent in every entry written before this field existed.
                    # Unknown, not adequate: that is the whole point.
                    impressions=last_metrics.get("current_impressions"),
                    clicks=last_metrics.get("current_clicks"),
                )
                metrics["previous_impressions"] = previous.impressions

                tokens, sufficiency, change = classify_ctr_movement(current, previous)
                changes.extend(tokens)
                metrics["evidence_sufficiency"] = sufficiency
                if change is not None:
                    # The percentage is a fact about two numbers. The direction
                    # label is a claim about the world. Refusing the second
                    # does not mean hiding the first.
                    metrics["ctr_change_percent"] = round(change, 2)
                self._log_movement(tokens, change)
            else:
                changes.append(CTR_FIRST_OBSERVATION)
                metrics["evidence_sufficiency"] = EVIDENCE_NO_BASELINE

            # Record in history
            self.state.optimization_history.append(
                {"timestamp": timestamp, "metrics": metrics, "changes": changes}
            )

            # Keep only last 30 days of history
            if len(self.state.optimization_history) > 30:
                self.state.optimization_history = self.state.optimization_history[-30:]

            return OptimizationResult(
                cycle_type=OptimizationCycleType.TREND_OBSERVATION,
                timestamp=timestamp,
                success=True,
                changes_made=changes,
                metrics=metrics,
            )

        except Exception as e:
            logger.error(f"Trend observation failed: {e}")
            return OptimizationResult(
                cycle_type=OptimizationCycleType.TREND_OBSERVATION,
                timestamp=timestamp,
                success=False,
                error=str(e),
            )

    @staticmethod
    def _log_movement(tokens: List[str], change: Optional[float]) -> None:
        """Log only what was actually claimed.

        A refused direction is not a decline, and must not reach an operator's
        WARNING stream as one -- that is how a noise-level wobble becomes an
        incident. A refusal is logged too, at INFO: the cycle saying it could
        not tell is information, and silence would read as "nothing moved".
        """
        for token in tokens:
            if token.startswith("ctr_trend_up_"):
                logger.info(
                    f"CTR trend up {change:.1f}% since the previous cycle "
                    "(trend only, cause not measured)"
                )
            elif token.startswith("ctr_trend_down_"):
                logger.warning(
                    f"CTR trend down {abs(change):.1f}% since the previous "
                    "cycle (trend only, cause not measured)"
                )
            elif token == CTR_TREND_INDETERMINATE:
                logger.info(
                    f"CTR moved {change:.1f}%, but a period carries fewer than "
                    f"{MIN_IMPRESSIONS_FOR_DIRECTION} impressions: no direction "
                    "claimed"
                )
            elif token == CTR_BASELINE_ZERO:
                logger.info(
                    "previous cycle's rate was a measured 0: a relative change "
                    "has no denominator, so none is reported"
                )
            elif token == CTR_SAMPLE_SIZE_UNKNOWN:
                logger.info(
                    "a period reported no impression count: the direction above "
                    "rests on an unknown sample size"
                )

    @staticmethod
    def _read_learned_config(path: Path) -> Optional[Dict]:
        """The config already on disk, or ``None`` when there is none to read.

        Unreadable is not "unchanged": a file this cannot parse yields ``None``
        so the cycle reports a change rather than claiming a stability it never
        managed to observe.
        """
        if not path.exists():
            return None
        try:
            with open(path) as f:
                loaded = json.load(f)
        except (OSError, ValueError) as e:
            logger.warning(f"could not read {path} to compare against: {e}")
            return None
        return loaded if isinstance(loaded, dict) else None

    def _generate_template_config(self) -> Dict:
        """Generate configuration based on learned patterns."""
        return {
            "generated_at": datetime.now().isoformat(),
            "title": {
                "optimal_length_min": self.state.optimal_title_length_min,
                "optimal_length_max": self.state.optimal_title_length_max,
            },
            "tags": {
                "recommended": self.state.top_performing_tags[:15],
                "count_min": 8,
                "count_max": 15,
            },
            "thumbnails": {"style_performance": self.state.thumbnail_style_performance},
        }

    def _load_video_mapping(self) -> Dict[str, str]:
        """Load video_id -> content_id mapping."""
        if self.video_mapping_file and self.video_mapping_file.exists():
            with open(self.video_mapping_file) as f:
                return json.load(f)
        return {}

    def _should_run_cycle(self) -> bool:
        """Check if enough time has passed since last cycle."""
        if not self.state.last_analytics_sync:
            return True

        last_sync = datetime.fromisoformat(self.state.last_analytics_sync)
        next_sync = last_sync + timedelta(hours=self.sync_interval_hours)

        return datetime.now() >= next_sync

    def _get_next_run_time(self) -> Optional[datetime]:
        """Get the next scheduled run time."""
        if not self.state.last_analytics_sync:
            return datetime.now()

        last_sync = datetime.fromisoformat(self.state.last_analytics_sync)
        return last_sync + timedelta(hours=self.sync_interval_hours)

    def _load_state(self) -> DaemonState:
        """Load state from disk."""
        state_file = self.state_dir / "daemon_state.json"

        if state_file.exists():
            try:
                with open(state_file) as f:
                    data = json.load(f)
            except json.JSONDecodeError as e:
                logger.warning(f"Could not load state: {e}")
                return DaemonState()
            # A key this release removed -- or one a newer release wrote --
            # used to raise TypeError out of DaemonState(**data) and land in a
            # fresh DaemonState(), silently discarding every learned value the
            # daemon had. Schema drift costs the drifted key now, not the state.
            known = {f.name for f in fields(DaemonState)}
            unknown = sorted(set(data) - known)
            if unknown:
                logger.warning(
                    "Ignoring unknown key(s) in %s: %s", state_file, ", ".join(unknown)
                )
            try:
                return DaemonState(**{k: v for k, v in data.items() if k in known})
            except TypeError as e:
                logger.warning(f"Could not load state: {e}")

        return DaemonState()

    def _save_state(self):
        """Save state to disk."""
        if self.dry_run:
            return

        state_file = self.state_dir / "daemon_state.json"

        with open(state_file, "w") as f:
            json.dump(asdict(self.state), f, indent=2)

    def _log_cycle_summary(self, results: List[OptimizationResult]):
        """Log a summary of the optimization cycle."""
        logger.info("")
        logger.info("=" * 60)
        logger.info("OPTIMIZATION CYCLE COMPLETE")
        logger.info("=" * 60)

        for result in results:
            status = "✓" if result.success else "✗"
            outcome = f" [{result.sync_outcome.value}]" if result.sync_outcome else ""
            logger.info(f"  {status} {result.cycle_type.value}{outcome}")

            if result.changes_made:
                for change in result.changes_made[:3]:
                    logger.info(f"      - {change}")

        # Key metrics
        logger.info("")
        logger.info(
            f"  State: {self.state.total_videos_synced} videos synced, "
            f"{self.state.total_patterns_learned} patterns learned"
        )
        logger.info("=" * 60)

    def get_status(self) -> Dict:
        """Get current daemon status.

        ``last_cycle_run_state`` is the field that answers "has this thing
        ever run?", and it is a separate key from ``last_cycle_run`` on
        purpose: a reader seeing only a null would have to decide for itself
        whether that meant never, or damaged, and deciding is guessing.
        """
        record = read_last_run(self.state_dir)
        if record is None:
            run_state = RUN_NEVER_RECORDED
        elif record.completion == RUN_UNREADABLE:
            run_state = RUN_UNREADABLE
        else:
            run_state = RUN_RECORDED
        return {
            # No freshness verdict here, deliberately. The age is reported;
            # how old is too old for this schedule is a product decision, and
            # a threshold invented in code would be that decision made by
            # whoever typed fastest.
            "last_cycle_run_state": run_state,
            "last_cycle_run": record.as_status() if record else None,
            "last_analytics_sync": self.state.last_analytics_sync,
            "last_pattern_analysis": self.state.last_pattern_analysis,
            "total_videos_synced": self.state.total_videos_synced,
            "total_patterns_learned": self.state.total_patterns_learned,
            "top_tags_count": len(self.state.top_performing_tags),
            "optimal_title_length": f"{self.state.optimal_title_length_min}-{self.state.optimal_title_length_max}",
            "next_sync": self._get_next_run_time().isoformat()
            if self._get_next_run_time()
            else None,
        }


def build_daemon(
    state_dir,
    dry_run,
    site_id="default",
    storage=Path("data/seo"),
    youtube_credentials: Optional[Path] = None,
    video_mapping_file: Optional[Path] = None,
):
    """Daemon over the real SEO store; imported late to keep the CLI testable.

    A fresh YouTube sync needs two values: an OAuth token file, and a JSON
    ``video_id -> content_id`` mapping (the shape ``_load_video_mapping``
    already reads, and the shape ``youtube_theme_sync`` already writes). Both
    arrive as arguments -- there is no default path and no environment
    fallback, because neither value is this code's to guess and a wrong guess
    would be a credential path that silently points at nothing.

    With both present a real ``YouTubeAnalyticsSync`` is wired and the cycle
    can collect. With either absent the behaviour is unchanged from before
    -- ``SyncOutcome.unconfigured``, nothing collected, nothing invented --
    except that the cycle now names the missing input instead of reporting a
    bare ``unconfigured`` an operator cannot act on.
    """
    from ..core.manager import SEOManager
    from ..platforms import YouTubeAdapter

    manager = SEOManager(
        site_id=site_id, platform=YouTubeAdapter(), storage_path=storage
    )
    youtube_sync, reason = _build_collector(youtube_credentials, video_mapping_file)
    return ContinuousOptimizationDaemon(
        manager=manager,
        state_dir=state_dir,
        dry_run=dry_run,
        youtube_sync=youtube_sync,
        video_mapping_file=video_mapping_file,
        sync_unconfigured_reason=reason,
    )


def _build_collector(
    youtube_credentials: Optional[Path], video_mapping_file: Optional[Path]
) -> Tuple[Any, Optional[str]]:
    """The collector, or ``None`` and the reason there isn't one.

    Half a collector is not a collector: credentials with no mapping would
    reach ``batch_sync`` with nothing to ask about, and report ``empty`` --
    a state that means "the account has nothing published", not "nobody told
    us which videos to look at". So both inputs are required before one is
    built, and the missing one is named.
    """
    if youtube_credentials is None:
        return None, COLLECTOR_NO_CREDENTIALS
    if video_mapping_file is None:
        # Named by _unconfigured_reasons from video_mapping_file itself.
        return None, None
    from .youtube_sync import YouTubeAnalyticsSync

    try:
        return YouTubeAnalyticsSync(str(youtube_credentials)), None
    except Exception as e:
        # A credential that was supplied and did not work is a different
        # report from one that was never supplied, and an operator who
        # supplied one needs to see that it failed rather than that it is
        # "not configured".
        logger.error(
            f"could not build the YouTube collector from {youtube_credentials}: {e}"
        )
        return None, COLLECTOR_UNAVAILABLE


def _apply_learned(state_dir: Path, config_dir: Path) -> int:
    """Hand the config this cycle wrote to the applier, under its own gates."""
    learned_path = Path(state_dir) / "learned_config.json"
    if not learned_path.exists():
        logger.error(f"--apply: {learned_path} was not written; nothing applied")
        return 1
    with open(learned_path) as f:
        learned = json.load(f)
    # `_apply_config_value` writes config_dir/learned_config.json without
    # creating the directory, so a first apply on a host with no ./config dies.
    Path(config_dir).mkdir(parents=True, exist_ok=True)
    # No `config=`: the applier keeps its own AutoApplierConfig defaults. A
    # scheduled job must never be the thing that widens them.
    applier = RecommendationAutoApplier(
        config_dir=Path(config_dir), state_dir=Path(state_dir) / "auto_applier"
    )
    # No gate here: `apply_learned_config` gates every section itself, so a
    # second filter would only be a copy that can drift out of step with it.
    applied = applier.apply_learned_config(learned, source="scheduled-cycle")
    logger.info(f"applied {len(applied)} learned setting(s) from {learned_path}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    """Run ONE cycle, dry run unless --apply. There is deliberately no
    `--forever`: cron owns the schedule, and a process that loops under it is a
    second, competing scheduler."""
    p = argparse.ArgumentParser(description="Run one SEO optimization cycle.")
    p.add_argument(
        "--apply",
        action="store_true",
        help="persist the learned config and apply it under the auto-applier's "
        "own gates; without this nothing is written and nothing changes",
    )
    p.add_argument("--state-dir", type=Path, default=Path("./data/optimization"))
    p.add_argument(
        "--site-id",
        default="default",
        help="site the SEO events are stored under (default: %(default)s)",
    )
    p.add_argument("--config-dir", type=Path, default=Path("./config"))
    p.add_argument(
        "--youtube-credentials",
        type=Path,
        default=None,
        help="OAuth 2.0 token JSON for YouTube Analytics. No default: without "
        "it the cycle collects nothing and says which input was missing",
    )
    p.add_argument(
        "--video-mapping",
        type=Path,
        default=None,
        help="JSON file mapping video_id -> content_id. No default, for the "
        "same reason; both are needed before a sync is attempted",
    )
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    daemon = build_daemon(
        state_dir=args.state_dir,
        dry_run=not args.apply,
        site_id=args.site_id,
        youtube_credentials=args.youtube_credentials,
        video_mapping_file=args.video_mapping,
    )
    failures = [r for r in daemon.run_optimization_cycle() if not r.success]
    for result in failures:
        logger.error(f"cycle step {result.cycle_type.value} failed: {result.error}")
    if failures:
        return 1
    if not args.apply:
        logger.info("dry run: learned config not persisted, nothing applied")
        return 0
    return _apply_learned(args.state_dir, args.config_dir)


if __name__ == "__main__":
    sys.exit(main())
