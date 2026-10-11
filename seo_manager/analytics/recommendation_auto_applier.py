"""
Recommendation Auto-Applier.

Automatically applies high-confidence SEO recommendations to:
- Generation templates
- Default configurations
- Tag/keyword recommendations

Five recommendation types are applicable today — TITLE_LENGTH,
TAG_RECOMMENDATION, THUMBNAIL_STYLE, DESCRIPTION_TEMPLATE and POSTING_TIME —
and the handler registry built in `__init__` is the single source of truth for
that. KEYWORD_PRIORITY is the one enum member left without a handler: no
category maps to it in `_determine_rec_type`, so nothing in this codebase can
produce one, and there is no config key a priority ordering would land in.
A type with no handler is reported as rejected with reason `unsupported_type`
rather than silently dropped, and no config flag can override that.

DESCRIPTION_TEMPLATE and POSTING_TIME are both off by default
(`allow_description_template`, `allow_posting_time`) because they are more
disruptive than the tuning knobs. Each takes its value from an explicit,
validated field on the recommendation — `template` and `posting_time` — never
from parsed prose and never from a default: a recommendation missing its field
is refused (`apply_failed`), not filled in.

**Nothing reads what this module applies.** `_apply_config_value` writes the
applied settings into `config_dir/learned_config.json` under
`seo.title.optimal_length`, `seo.tags.recommended`, `thumbnails.default_style`,
`descriptions.default_template` and `posting.default_time`. No
generator reads any of them. The only readers anywhere are two self-reads in
this same class: `_get_config_value`, called by `_apply_config_change` to
capture the old value so `rollback_recommendation` can restore it, and by
`_apply_tag_rec` to merge with the tag list this class wrote last time. The
file is a ledger the applier keeps about itself.

So the demonstration an audit would want -- "the next draft came out different,
for this cited reason" -- cannot be written, and is deliberately not faked
against a fixture that pretends a generator consults the file. An applied-
settings path claiming to be proven when it is not is the defect, not the
documentation of it. `tests/unit/test_learned_config_has_no_consumer.py` pins
the absence so this paragraph fails loudly the day a real consumer is wired,
rather than quietly becoming false.

The generators that DO use analytics -- the long-form metadata generator of
the video pipeline that embeds this package -- go through
`SEOManager` and `SEOOptimizer.analyze_patterns` against live events. They
never open this file, and a change applied here reaches no draft.

Creates an audit trail of all changes for review and rollback. Each record
cites the data its change was computed from (`evidence`) or records None —
`source` names the module that ran, which never distinguished two changes to
the same key computed from different video sets. There is no
`expected_impact`: it was the literal "medium" on every record ever written
and nothing read it back.

There is no `confidence` on the record either, for the same reason.
`apply_learned_config` typed in 0.8 for title, 0.75 for tags and 0.7 for
thumbnails, and every handler passed `rec.get("confidence", 0.7)` — eight
numbers no code in this service computed, written to a field no code ever
read. They were not a gate: `_apply_config_change` never compared them to
anything, so the three literals ranked nothing and gated nothing while
looking exactly like a measurement. The number `process_recommendations`
does act on is different in kind — it arrives on the caller's
recommendation and is compared against `min_confidence_for_auto` there — so
it stays, at the one place a caller supplies it.
"""

import json
import logging
import re
import shutil
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, ClassVar, Dict, List, Optional

logger = logging.getLogger(__name__)


class RecommendationType(Enum):
    """Types of recommendations that can be auto-applied."""

    TITLE_LENGTH = "title_length"
    TAG_RECOMMENDATION = "tag_recommendation"
    KEYWORD_PRIORITY = "keyword_priority"
    POSTING_TIME = "posting_time"
    THUMBNAIL_STYLE = "thumbnail_style"
    DESCRIPTION_TEMPLATE = "description_template"


class ApplyStatus(Enum):
    """Status of recommendation application."""

    PENDING = "pending"
    APPLIED = "applied"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"


@dataclass
class AppliedRecommendation:
    """Record of an applied recommendation."""

    id: str
    recommendation_type: RecommendationType
    applied_at: str
    status: ApplyStatus

    # What was changed
    target_config: str  # Config file or setting path
    old_value: Any
    new_value: Any

    # Who ran, not how sure it was: there is no `confidence` here. Every
    # value ever written to it was a literal typed into a call site (see the
    # module docstring), nothing read it, and a field shaped like a
    # measurement invites the next reader to trust one.
    source: str  # Which module triggered this — a name, not evidence

    # What the change was computed from, or None when nobody recorded it.
    # There is no `expected_impact`: it was the literal "medium" on every
    # record ever written, nothing read it back, and a stated impact nobody
    # measured is a claim, not an audit trail.
    evidence: Optional[Dict[str, Any]] = None

    # Audit trail
    applied_by: str = "auto_applier"
    rollback_available: bool = True
    rollback_at: Optional[str] = None
    notes: Optional[str] = None


@dataclass
class AutoApplierConfig:
    """Configuration for the auto-applier."""

    # Thresholds applied to the confidence a CALLER puts on an incoming
    # recommendation (`process_recommendations`). They are not consulted by
    # `apply_learned_config`, which has no caller-supplied number to judge
    # and decides purely on the per-type flags below.
    min_confidence_for_auto: float = 0.8
    min_confidence_for_suggest: float = 0.6

    # What can be auto-applied
    allow_title_length: bool = True
    allow_tag_changes: bool = True
    allow_thumbnail_style: bool = True
    # More disruptive, require approval. Both stay off until an operator
    # opts in; enabling one is what makes its handler reachable.
    allow_posting_time: bool = False
    allow_description_template: bool = False

    # Safety limits
    max_tag_changes_per_cycle: int = 5
    max_config_changes_per_cycle: int = 3

    # Rollback settings
    keep_backup_days: int = 30


class RecommendationAutoApplier:
    """
    Automatically applies high-confidence recommendations.

    Workflow:
    1. Receive recommendations from SEOOptimizer
    2. Filter by confidence threshold
    3. Apply changes to config files
    4. Create audit trail
    5. Track impact for validation

    Example:
        applier = RecommendationAutoApplier(
            config_dir=Path("./config"),
            state_dir=Path("./data/auto_applier")
        )

        # Process recommendations
        results = applier.process_recommendations(recommendations)

        # View history
        history = applier.get_application_history()

        # Rollback if needed
        applier.rollback_recommendation("rec_001")
    """

    # Config flag gating each type the applier knows about. Being listed here
    # is NOT permission to apply: _can_auto_apply also requires a handler.
    _CONFIG_FLAGS: ClassVar[Dict[RecommendationType, str]] = {
        RecommendationType.TITLE_LENGTH: "allow_title_length",
        RecommendationType.TAG_RECOMMENDATION: "allow_tag_changes",
        RecommendationType.THUMBNAIL_STYLE: "allow_thumbnail_style",
        RecommendationType.POSTING_TIME: "allow_posting_time",
        RecommendationType.DESCRIPTION_TEMPLATE: "allow_description_template",
    }

    def __init__(
        self,
        config_dir: Path,
        state_dir: Optional[Path] = None,
        config: Optional[AutoApplierConfig] = None,
    ):
        """
        Initialize the auto-applier.

        Args:
            config_dir: Directory containing config files to update
            state_dir: Directory for state and backups
            config: Configuration settings
        """
        self.config_dir = Path(config_dir)
        self.state_dir = state_dir or Path("./data/auto_applier")
        self.config = config or AutoApplierConfig()

        # Ensure directories exist
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "backups").mkdir(exist_ok=True)

        # The applicable types, by construction: a type reaches
        # _apply_recommendation only if it has a bound handler here. Enum
        # members absent from this dict have no implementation and are kept
        # only so _load_state can deserialise older audit records.
        self._handlers: Dict[
            RecommendationType, Callable[[Dict], Optional[AppliedRecommendation]]
        ] = {
            RecommendationType.TITLE_LENGTH: self._apply_title_length_rec,
            RecommendationType.TAG_RECOMMENDATION: self._apply_tag_rec,
            RecommendationType.THUMBNAIL_STYLE: self._apply_thumbnail_style_rec,
            RecommendationType.DESCRIPTION_TEMPLATE: self._apply_description_rec,
            RecommendationType.POSTING_TIME: self._apply_posting_time_rec,
        }

        # Load history
        self.applied_recommendations: List[AppliedRecommendation] = []
        self._load_state()

    def process_recommendations(
        self, recommendations: List[Dict], dry_run: bool = False
    ) -> Dict[str, List[AppliedRecommendation]]:
        """
        Process a batch of recommendations.

        Args:
            recommendations: List of recommendation dicts from SEOOptimizer
            dry_run: If True, don't actually apply changes

        Returns:
            Dict with 'applied', 'suggested', 'rejected' lists
        """
        results = {"applied": [], "suggested": [], "rejected": []}

        changes_this_cycle = 0

        for rec in recommendations:
            confidence = rec.get("confidence", 0)
            rec_type = self._determine_rec_type(rec)

            if not rec_type:
                logger.debug(f"Unknown recommendation type: {rec.get('category')}")
                continue

            # Check if this type can be auto-applied. Two distinct reasons:
            # the applier has no handler for it at all, or it has one and the
            # operator has turned it off.
            if not self._can_auto_apply(rec_type):
                results["rejected"].append(
                    {
                        "recommendation": rec,
                        "reason": (
                            "type_not_allowed"
                            if rec_type in self._handlers
                            else "unsupported_type"
                        ),
                        "type": rec_type.value,
                    }
                )
                continue

            # Check confidence threshold
            if confidence >= self.config.min_confidence_for_auto:
                # Check safety limits
                if changes_this_cycle >= self.config.max_config_changes_per_cycle:
                    results["suggested"].append(
                        {
                            "recommendation": rec,
                            "reason": "max_changes_reached",
                            "confidence": confidence,
                        }
                    )
                    continue

                # Apply the recommendation
                if not dry_run:
                    applied = self._apply_recommendation(rec, rec_type)
                    if applied:
                        results["applied"].append(applied)
                        changes_this_cycle += 1
                    else:
                        # The handler raised, found nothing to parse, or the
                        # value was already current. Record it — a decision
                        # that vanishes from all three buckets is invisible.
                        results["rejected"].append(
                            {
                                "recommendation": rec,
                                "reason": "apply_failed",
                                "confidence": confidence,
                                "type": rec_type.value,
                            }
                        )
                else:
                    logger.info(f"[DRY RUN] Would apply: {rec}")
                    results["applied"].append({"recommendation": rec, "dry_run": True})

            elif confidence >= self.config.min_confidence_for_suggest:
                results["suggested"].append(
                    {"recommendation": rec, "confidence": confidence}
                )

            else:
                results["rejected"].append(
                    {
                        "recommendation": rec,
                        "reason": "low_confidence",
                        "confidence": confidence,
                    }
                )

        self._save_state()

        # Log summary
        logger.info(
            f"Processed {len(recommendations)} recommendations: "
            f"{len(results['applied'])} applied, "
            f"{len(results['suggested'])} suggested, "
            f"{len(results['rejected'])} rejected"
        )

        return results

    def apply_learned_config(
        self, learned_config: Dict, source: str = "pattern_analysis"
    ) -> List[AppliedRecommendation]:
        """
        Apply a learned configuration from the optimization daemon.

        Every section is gated on the same flag `process_recommendations` uses,
        so no caller can reach `_apply_config_change` past a gate an operator
        left closed. The daemon used to filter the sections before calling
        this, which protected the nightly cron and nothing else.

        Which sections apply is decided by those flags and nothing else. This
        method used to pass a per-section confidence as well — 0.8 title, 0.75
        tags, 0.7 thumbnails — and the shape of those numbers (one per
        section, descending, straddling `min_confidence_for_auto`) read as a
        ranking that decided what a nightly run touched. It never was one:
        `_apply_config_change` takes no threshold, so all three sections
        applied whenever their flag was on, and the numbers only ever reached
        the audit record. They are gone rather than corrected because nothing
        here measures a confidence to correct them to.

        Args:
            learned_config: Configuration dict from ContinuousOptimizationDaemon
            source: What generated this config

        Returns:
            List of applied recommendations
        """
        applied = []

        # Apply title length settings
        if "title" in learned_config and self._learned_section_allowed(
            "title", RecommendationType.TITLE_LENGTH
        ):
            title_config = learned_config["title"]
            rec = self._apply_config_change(
                rec_type=RecommendationType.TITLE_LENGTH,
                config_key="seo.title.optimal_length",
                new_value={
                    "min": title_config.get("optimal_length_min", 50),
                    "max": title_config.get("optimal_length_max", 70),
                },
                source=source,
                evidence=self._learned_config_evidence(learned_config, "title"),
            )
            if rec:
                applied.append(rec)

        # Apply tag recommendations
        if "tags" in learned_config and self._learned_section_allowed(
            "tags", RecommendationType.TAG_RECOMMENDATION
        ):
            tags_config = learned_config["tags"]
            rec = self._apply_config_change(
                rec_type=RecommendationType.TAG_RECOMMENDATION,
                config_key="seo.tags.recommended",
                new_value=tags_config.get("recommended", []),
                source=source,
                evidence=self._learned_config_evidence(learned_config, "tags"),
            )
            if rec:
                applied.append(rec)

        # Apply thumbnail style recommendation
        if "thumbnails" in learned_config and self._learned_section_allowed(
            "thumbnails", RecommendationType.THUMBNAIL_STYLE
        ):
            thumb_config = learned_config["thumbnails"]
            style_perf = thumb_config.get("style_performance", {})

            if style_perf:
                best_style = max(style_perf.keys(), key=lambda k: style_perf[k])
                rec = self._apply_config_change(
                    rec_type=RecommendationType.THUMBNAIL_STYLE,
                    config_key="thumbnails.default_style",
                    new_value=best_style,
                    source=source,
                    evidence=self._learned_config_evidence(
                        learned_config, "thumbnails"
                    ),
                )
                if rec:
                    applied.append(rec)

        self._save_state()
        return applied

    def rollback_recommendation(self, rec_id: str) -> bool:
        """
        Rollback a previously applied recommendation.

        Args:
            rec_id: ID of the recommendation to rollback

        Returns:
            True if rollback successful
        """
        # Find the recommendation
        rec = None
        for r in self.applied_recommendations:
            if r.id == rec_id:
                rec = r
                break

        if not rec:
            logger.error(f"Recommendation not found: {rec_id}")
            return False

        if not rec.rollback_available:
            logger.error(f"Rollback not available for: {rec_id}")
            return False

        if rec.status == ApplyStatus.ROLLED_BACK:
            logger.warning(f"Already rolled back: {rec_id}")
            return True

        # Restore old value
        try:
            self._apply_config_value(rec.target_config, rec.old_value)

            rec.status = ApplyStatus.ROLLED_BACK
            rec.rollback_at = datetime.now().isoformat()
            rec.notes = "Rolled back by user request"

            self._save_state()

            logger.info(f"Rolled back recommendation: {rec_id}")
            return True

        except Exception as e:
            logger.error(f"Rollback failed for {rec_id}: {e}")
            return False

    def get_application_history(
        self, limit: int = 50, status_filter: Optional[ApplyStatus] = None
    ) -> List[AppliedRecommendation]:
        """
        Get history of applied recommendations.

        Args:
            limit: Maximum records to return
            status_filter: Only return records with this status

        Returns:
            List of AppliedRecommendation records
        """
        history = self.applied_recommendations

        if status_filter:
            history = [r for r in history if r.status == status_filter]

        return sorted(history, key=lambda r: r.applied_at, reverse=True)[:limit]

    def get_pending_suggestions(self) -> List[Dict]:
        """
        Get recommendations that need manual approval.

        Returns:
            List of suggested recommendations
        """
        suggestions_file = self.state_dir / "pending_suggestions.json"

        if suggestions_file.exists():
            with open(suggestions_file) as f:
                return json.load(f)

        return []

    def _apply_recommendation(
        self, rec: Dict, rec_type: RecommendationType
    ) -> Optional[AppliedRecommendation]:
        """Apply a single recommendation through the handler registry."""
        handler = self._handlers.get(rec_type)

        if handler is None:
            # Unreachable via process_recommendations (_can_auto_apply gates
            # on the same registry); kept for direct callers.
            logger.warning(f"Unhandled recommendation type: {rec_type}")
            return None

        try:
            return handler(rec)

        except Exception as e:
            logger.error(f"Failed to apply recommendation: {e}")
            return None

    def _apply_title_length_rec(self, rec: Dict) -> Optional[AppliedRecommendation]:
        """Apply title length recommendation."""
        # Parse the suggestion for length values
        suggestion = rec.get("suggestion", "")

        # Extract length range (e.g., "50-70 chars")
        match = re.search(r"(\d+)-(\d+)", suggestion)

        if not match:
            return None

        min_len, max_len = int(match.group(1)), int(match.group(2))

        return self._apply_config_change(
            rec_type=RecommendationType.TITLE_LENGTH,
            config_key="seo.title.optimal_length",
            new_value={"min": min_len, "max": max_len},
            source="seo_optimizer",
            evidence=self._recommendation_evidence(rec),
        )

    # Free-form analytics suggestions are untrusted text, not a curated tag
    # list. Reject fragments that don't look like tags before they land in
    # generation config. NOTE: this does NOT check region/blocklist — a proper
    # cross-region guard needs brand context this dormant applier lacks.
    _MAX_TAG_LEN = 30
    _MAX_TAG_WORDS = 4

    @classmethod
    def _sanitize_tags(cls, raw_tags: List[str]) -> List[str]:
        """Keep only tag-shaped tokens from free-form suggestion text."""
        clean = []
        for tag in raw_tags:
            tag = tag.strip().lower()
            if not tag or len(tag) > cls._MAX_TAG_LEN:
                continue
            if len(tag.split()) > cls._MAX_TAG_WORDS:
                continue
            clean.append(tag)
        return clean

    def _apply_tag_rec(self, rec: Dict) -> Optional[AppliedRecommendation]:
        """Apply tag recommendation."""
        suggestion = rec.get("suggestion", "")

        # Extract tags from suggestion (e.g., "Consider adding: tag1, tag2, tag3")
        if "adding:" in suggestion:
            tags_str = suggestion.split("adding:")[1].strip()
            new_tags = self._sanitize_tags(tags_str.split(","))
            if not new_tags:
                return None

            # Merge with existing recommended tags
            current_tags = self._get_config_value("seo.tags.recommended") or []
            merged_tags = list(set(current_tags + new_tags))[:15]

            return self._apply_config_change(
                rec_type=RecommendationType.TAG_RECOMMENDATION,
                config_key="seo.tags.recommended",
                new_value=merged_tags,
                source="seo_optimizer",
            )

        return None

    def _apply_thumbnail_style_rec(self, rec: Dict) -> Optional[AppliedRecommendation]:
        """Apply thumbnail style recommendation."""
        suggestion = rec.get("suggestion", "")

        style = None
        if "bold" in suggestion.lower():
            style = "bold"
        elif "minimal" in suggestion.lower():
            style = "minimal"
        elif "immersive" in suggestion.lower():
            style = "immersive"

        if not style:
            return None

        return self._apply_config_change(
            rec_type=RecommendationType.THUMBNAIL_STYLE,
            config_key="thumbnails.default_style",
            new_value=style,
            source="thumbnail_ab_tracker",
            evidence=self._recommendation_evidence(rec),
        )

    # A description template is content, not a knob: it is taken verbatim from
    # the recommendation's `template` field, never parsed out of free-form
    # suggestion prose, which would amount to inventing copy. The cap is
    # YouTube's description limit, the tightest of the platforms this feeds.
    _MAX_DESCRIPTION_TEMPLATE_LEN = 5000

    def _apply_description_rec(self, rec: Dict) -> Optional[AppliedRecommendation]:
        """Apply a description-template recommendation.

        Requires an explicit, non-empty `template` on the recommendation.
        A recommendation without one is refused rather than defaulted.
        """
        template = rec.get("template")

        if not isinstance(template, str) or not template.strip():
            logger.warning("description_template recommendation carries no template")
            return None

        template = template.strip()

        if len(template) > self._MAX_DESCRIPTION_TEMPLATE_LEN:
            logger.warning(
                f"Description template exceeds "
                f"{self._MAX_DESCRIPTION_TEMPLATE_LEN} chars; refusing"
            )
            return None

        return self._apply_config_change(
            rec_type=RecommendationType.DESCRIPTION_TEMPLATE,
            config_key="descriptions.default_template",
            new_value=template,
            source="seo_optimizer",
            evidence=self._recommendation_evidence(rec),
        )

    # 24-hour "HH:MM" only. A posting time drives scheduling, so a value that
    # does not parse unambiguously is refused rather than guessed at.
    _POSTING_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

    def _apply_posting_time_rec(self, rec: Dict) -> Optional[AppliedRecommendation]:
        """Apply a posting-time recommendation.

        Requires an explicit `posting_time` in 24-hour "HH:MM" form. Free-form
        suggestion text is not parsed: a recommendation without a valid time is
        refused rather than defaulted.
        """
        posting_time = rec.get("posting_time")

        if not isinstance(posting_time, str):
            logger.warning("posting_time recommendation carries no posting_time")
            return None

        posting_time = posting_time.strip()

        if not self._POSTING_TIME_RE.match(posting_time):
            logger.warning(f"Unparseable posting_time {posting_time!r}; refusing")
            return None

        return self._apply_config_change(
            rec_type=RecommendationType.POSTING_TIME,
            config_key="posting.default_time",
            new_value=posting_time,
            source="seo_optimizer",
            evidence=self._recommendation_evidence(rec),
        )

    # The only fields of an incoming recommendation that are real input
    # rather than the applier's own boilerplate: the observation it was
    # derived from and the string a value gets parsed out of.
    _EVIDENCE_KEYS: ClassVar[tuple] = ("content_id", "issue", "suggestion")

    @classmethod
    def _recommendation_evidence(cls, rec: Dict) -> Optional[Dict[str, Any]]:
        """Cite what the recommendation itself carried, or nothing at all.

        This is provenance, not a measurement, and `kind` says so: a
        recommendation reaching a handler is free-form text from
        SEOOptimizer, and it does not even name the content it was computed
        over (`generate_recommendations` attaches no `content_id`). Citing
        it still tells a reviewer which analysis produced the change, which
        `source="seo_optimizer"` never could. A recommendation carrying none
        of those fields yields None rather than a blob naming only itself.
        """
        cited = {
            key: rec[key]
            for key in cls._EVIDENCE_KEYS
            if isinstance(rec.get(key), str) and rec[key].strip()
        }

        if not cited:
            return None

        return {"kind": "recommendation", **cited}

    @staticmethod
    def _learned_config_evidence(
        learned_config: Dict, section: str
    ) -> Optional[Dict[str, Any]]:
        """Cite the learned-config section a nightly change was computed from.

        The section itself is the data — style performance, top tags, the
        measured title lengths — so two changes to the same key a month
        apart are told apart by their evidence, not by `source`, which is
        the same string both times. An empty or missing section has nothing
        to cite and yields None.
        """
        data = learned_config.get(section)

        if not isinstance(data, dict) or not data:
            return None

        evidence: Dict[str, Any] = {
            "kind": "learned_config",
            "section": section,
            "data": dict(data),
        }

        generated_at = learned_config.get("generated_at")
        if isinstance(generated_at, str) and generated_at.strip():
            evidence["config_generated_at"] = generated_at

        return evidence

    def _apply_config_change(
        self,
        rec_type: RecommendationType,
        config_key: str,
        new_value: Any,
        source: str,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> Optional[AppliedRecommendation]:
        """Apply a configuration change.

        `evidence` is what the change was computed from. A caller with
        nothing real to cite passes nothing: `None` records that no one
        wrote down what justified this, which is itself the finding.
        """
        # Get old value
        old_value = self._get_config_value(config_key)

        # Skip if no change
        if old_value == new_value:
            logger.debug(f"No change needed for {config_key}")
            return None

        # Create backup
        self._backup_config()

        # Apply change
        self._apply_config_value(config_key, new_value)

        # Create record
        rec_id = f"rec_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{rec_type.value[:4]}"

        applied = AppliedRecommendation(
            id=rec_id,
            recommendation_type=rec_type,
            applied_at=datetime.now().isoformat(),
            status=ApplyStatus.APPLIED,
            target_config=config_key,
            old_value=old_value,
            new_value=new_value,
            source=source,
            evidence=evidence,
        )

        self.applied_recommendations.append(applied)

        logger.info(f"Applied {rec_type.value}: {config_key} = {new_value}")

        return applied

    def _get_config_value(self, config_key: str) -> Any:
        """Get a value from the learned config."""
        config_file = self.config_dir / "learned_config.json"

        if not config_file.exists():
            return None

        with open(config_file) as f:
            config = json.load(f)

        # Navigate to key (e.g., "seo.title.optimal_length")
        parts = config_key.split(".")
        value = config

        for part in parts:
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                return None

        return value

    def _apply_config_value(self, config_key: str, new_value: Any):
        """Apply a value to the learned config."""
        config_file = self.config_dir / "learned_config.json"

        if config_file.exists():
            with open(config_file) as f:
                config = json.load(f)
        else:
            config = {}

        # Navigate and set value
        parts = config_key.split(".")
        current = config

        for part in parts[:-1]:
            if part not in current:
                current[part] = {}
            current = current[part]

        current[parts[-1]] = new_value

        # Save
        with open(config_file, "w") as f:
            json.dump(config, f, indent=2)

    def _backup_config(self):
        """Create backup of current config."""
        config_file = self.config_dir / "learned_config.json"

        if not config_file.exists():
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self.state_dir / "backups" / f"learned_config_{timestamp}.json"

        shutil.copy2(config_file, backup_path)

    def _determine_rec_type(self, rec: Dict) -> Optional[RecommendationType]:
        """Determine recommendation type from dict."""
        category = rec.get("category", "").lower()

        if category == "title":  # noqa: SIM116
            return RecommendationType.TITLE_LENGTH
        elif category == "tags":
            return RecommendationType.TAG_RECOMMENDATION
        elif category == "thumbnail":
            return RecommendationType.THUMBNAIL_STYLE
        elif category == "posting":
            return RecommendationType.POSTING_TIME
        elif category == "description":
            return RecommendationType.DESCRIPTION_TEMPLATE

        return None

    def _learned_section_allowed(
        self, section: str, rec_type: RecommendationType
    ) -> bool:
        """Gate one learned_config section, logging the refusal for an operator.

        A nightly run that changes nothing is otherwise silent, and the line is
        how someone finds out which flag stopped it.
        """
        if self._can_auto_apply(rec_type):
            return True

        flag = self._CONFIG_FLAGS.get(rec_type)
        reason = f"{flag} is off" if flag else "it has no auto-apply flag"
        logger.info(f"not applying learned '{section}': {reason}")

        return False

    def _can_auto_apply(self, rec_type: RecommendationType) -> bool:
        """Check if this recommendation type can be auto-applied.

        Both conditions must hold: a handler exists in the registry AND the
        config flag allows it. A flag on its own never green-lights a type the
        applier has no implementation for.
        """
        if rec_type not in self._handlers:
            return False

        flag = self._CONFIG_FLAGS.get(rec_type)

        return bool(flag) and bool(getattr(self.config, flag, False))

    def _load_state(self):
        """Load state from disk."""
        state_file = self.state_dir / "auto_applier_state.json"

        if state_file.exists():
            try:
                with open(state_file) as f:
                    data = json.load(f)

                known = {f.name for f in fields(AppliedRecommendation)}

                for rec_data in data.get("applied_recommendations", []):
                    # Convert enums
                    rec_data["recommendation_type"] = RecommendationType(
                        rec_data["recommendation_type"]
                    )
                    rec_data["status"] = ApplyStatus(rec_data["status"])

                    # An audit trail outlives the schema. Records written
                    # before `expected_impact` was deleted still carry it,
                    # and `AppliedRecommendation(**rec_data)` would raise
                    # TypeError into the handler below — discarding the rest
                    # of the history to preserve a field nobody read.
                    dropped = sorted(set(rec_data) - known)
                    if dropped:
                        logger.info(
                            f"dropping unknown audit fields {dropped} from "
                            f"{rec_data.get('id')}"
                        )
                        rec_data = {k: v for k, v in rec_data.items() if k in known}

                    self.applied_recommendations.append(
                        AppliedRecommendation(**rec_data)
                    )

            except (json.JSONDecodeError, TypeError, KeyError) as e:
                logger.warning(f"Could not load auto-applier state: {e}")

    def _save_state(self):
        """Save state to disk."""
        state_file = self.state_dir / "auto_applier_state.json"

        data = {
            "applied_recommendations": [
                {
                    **asdict(rec),
                    "recommendation_type": rec.recommendation_type.value,
                    "status": rec.status.value,
                }
                for rec in self.applied_recommendations
            ],
            "last_updated": datetime.now().isoformat(),
        }

        with open(state_file, "w") as f:
            json.dump(data, f, indent=2)
