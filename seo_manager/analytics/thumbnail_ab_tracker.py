"""
Thumbnail A/B Testing Tracker.

Tracks which thumbnail style was used for each video and measures thumbnail
CLICK-THROUGH (clicks / thumbnail impressions) to recommend the best style.

Only a rate measured against thumbnail impressions can say anything about a
thumbnail, and the Analytics API exposes none — `cardClickRate`, which
`youtube_sync` fetches, counts in-player card clicks by people ALREADY
watching. This module used to average whatever landed in its generic `ctr`
field and name a winner from it. Every rate now declares its provenance
(`metric_source`); anything else is refused with a reason.

Integrates with:
- YouTubeThumbnailGenerator (styles: minimal, bold, immersive)
- SEOManager (storage)
"""

import json
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from seo_manager.fsio import atomic_write_json

from . import evidence_age, metric_source
from .evidence_age import EvidenceState

logger = logging.getLogger(__name__)


class ThumbnailStyle(Enum):
    """Thumbnail styles from YouTubeThumbnailGenerator."""

    MINIMAL = "minimal"
    BOLD = "bold"
    IMMERSIVE = "immersive"
    UNKNOWN = "unknown"


@dataclass
class ThumbnailPerformance:
    """Performance data for a single thumbnail."""

    content_id: str
    video_id: str
    style: ThumbnailStyle
    created_at: str

    # Performance metrics (updated over time)
    impressions: int = 0
    clicks: int = 0
    views: int = 0
    # None means UNMEASURED. Defaulting to 0.0 fabricated a measurement that
    # read as "shown, never clicked". Records stored before provenance
    # existed load as UNKNOWN, which is never treated as thumbnail data.
    ctr: float | None = None
    ctr_source: str = metric_source.UNKNOWN
    avg_watch_time: float = 0.0

    # Test & Compare provenance. Studio's Test & Compare has no API,
    # so these are the operator's manual record of what was prepared and
    # which variant got uploaded -- never a measurement, never entering
    # the CTR analysis below.
    prepared_variants: list[str] | None = None
    manifest_path: str | None = None

    # Metadata
    last_updated: str | None = None


@dataclass
class StyleAnalysis:
    """Aggregated analysis for a thumbnail style."""

    style: ThumbnailStyle
    sample_size: int
    total_impressions: int
    total_clicks: int
    avg_ctr: float
    ctr_std_dev: float
    confidence_level: float  # 0.0-1.0 based on sample size

    # Comparison metrics
    vs_average_ctr: float  # Percentage better/worse than average
    is_top_performer: bool

    # Age of the evidence. `newest_sample_at` is the most recent
    # `last_updated` among the samples this analysis averaged -- when the
    # data was learned, never when a report was printed.
    newest_sample_at: str | None = None
    evidence_age_days: float | None = None
    evidence: EvidenceState = EvidenceState.UNDATED


@dataclass(frozen=True)
class StyleRecommendation:
    """A recommended style and the age of the evidence behind it.

    Frozen, and every field is required: `get_recommended_style` cannot hand
    back a bare winner, so no caller can print a "best" without the age and
    sample count in hand. That beats a staleness warning added to the report,
    which a future caller can skip.

    `style` is None exactly when `evidence` is NOTHING_MEASURED (nobody
    measured anything) or INSUFFICIENT (too few samples to name a winner) --
    two distinct outcomes, neither of them a zero-CTR winner. A STALE or
    UNDATED recommendation still carries its style: it is the last VALID
    answer, labelled, never dropped and never replaced by a fresh invention.
    """

    style: ThumbnailStyle | None
    confidence: float
    evidence: EvidenceState
    sample_size: int
    evidence_newest_at: str | None
    evidence_age_days: float | None
    reason: str | None

    @property
    def is_stale(self) -> bool:
        return self.evidence is EvidenceState.STALE

    def age_phrase(self) -> str:
        """Human-readable age of the evidence, for reports and logs."""
        return evidence_age.describe(self.evidence_newest_at, self.evidence_age_days)


class ThumbnailABTracker:
    """
    Tracks and analyzes thumbnail style performance.

    Workflow:
    1. Record which style is used when generating thumbnails
    2. Update with click-through data that declares its provenance
    3. Analyze which style performs best (thumbnail-sourced rates only)
    4. Recommend a style with the age of the evidence behind it, or report
       why no recommendation is possible

    Example:
        tracker = ThumbnailABTracker(state_dir=Path("./data/thumbnails"))

        # Record usage
        tracker.record_thumbnail(
            content_id="journey-001",
            video_id="abc123xyz",
            style=ThumbnailStyle.BOLD
        )

        # Update with analytics — the rate carries where it came from
        tracker.update_performance("journey-001", ctr=0.08, views=1000,
            ctr_source=metric_source.YOUTUBE_THUMBNAIL_IMPRESSION_CTR)

        # The winner and the age of its evidence arrive together; `style`
        # is None, with a reason, when there is no such data.
        rec = tracker.get_recommended_style()
        print(rec.style, rec.evidence, rec.age_phrase())
    """

    MIN_SAMPLE_SIZE = 5  # Minimum videos per style for reliable comparison
    MIN_IMPRESSIONS = 1000  # Minimum thumbnail impressions for a usable rate

    def __init__(
        self, state_dir: Path | None = None, min_sample_size: int = MIN_SAMPLE_SIZE
    ):
        """
        Initialize the tracker.

        Args:
            state_dir: Directory for persistent state
            min_sample_size: Minimum samples needed for style recommendation
        """
        self.state_dir = state_dir or Path("./data/thumbnail_ab")
        self.min_sample_size = min_sample_size

        # Ensure state directory exists
        self.state_dir.mkdir(parents=True, exist_ok=True)

        # Load existing data
        self.thumbnails: dict[str, ThumbnailPerformance] = {}
        self._load_state()

    def record_thumbnail(
        self,
        content_id: str,
        video_id: str,
        style: ThumbnailStyle,
        thumbnail_path: str | None = None,
    ) -> ThumbnailPerformance:
        """
        Record that a thumbnail was generated with a specific style.

        Args:
            content_id: Content/journey ID
            video_id: YouTube video ID
            style: Which thumbnail style was used
            thumbnail_path: Optional path to the thumbnail file

        Returns:
            ThumbnailPerformance record
        """
        thumbnail = ThumbnailPerformance(
            content_id=content_id,
            video_id=video_id,
            style=style,
            created_at=datetime.now().isoformat(),
        )

        self.thumbnails[content_id] = thumbnail
        self._save_state()

        logger.info(f"Recorded thumbnail: {content_id} -> style={style.value}")

        return thumbnail

    def record_thumbnail_from_path(
        self, content_id: str, video_id: str, thumbnail_path: Path
    ) -> ThumbnailPerformance:
        """
        Record thumbnail and infer style from filename.

        Expects filenames like: variation_bold.png, variation_minimal.png

        Args:
            content_id: Content/journey ID
            video_id: YouTube video ID
            thumbnail_path: Path to thumbnail file

        Returns:
            ThumbnailPerformance record
        """
        # Infer style from filename
        filename = thumbnail_path.stem.lower()

        if "minimal" in filename:
            style = ThumbnailStyle.MINIMAL
        elif "bold" in filename:
            style = ThumbnailStyle.BOLD
        elif "immersive" in filename:
            style = ThumbnailStyle.IMMERSIVE
        else:
            style = ThumbnailStyle.UNKNOWN
            logger.warning(f"Could not infer style from filename: {filename}")

        return self.record_thumbnail(content_id, video_id, style, str(thumbnail_path))

    def record_test_compare_selection(
        self,
        content_id: str,
        video_id: str,
        selected_style: ThumbnailStyle,
        prepared_styles: list[ThumbnailStyle] | None = None,
        manifest_path: str | None = None,
    ) -> ThumbnailPerformance:
        """
        Manually record which variant the operator uploaded into Studio's
        Test & Compare.

        Test & Compare has no API: nothing here observed the upload, so
        this is the operator stating it after the fact -- never inferred,
        never a measurement. It goes through the existing
        `record_thumbnail` path (style + video_id), so the ordinary
        Analytics read/report path (`update_performance`,
        `get_style_analysis`, `get_recommended_style`) picks the record up
        exactly like any other recorded thumbnail. `prepared_styles` and
        `manifest_path` are provenance only -- they never enter the CTR
        analysis.

        Args:
            content_id: Content/journey ID
            video_id: YouTube video ID
            selected_style: The variant actually uploaded to Test & Compare
            prepared_styles: The 3 styles that were prepared as candidates
                (e.g. from `YouTubeThumbnailGenerator.
                prepare_test_and_compare_variants`)
            manifest_path: Path to that preparation's manifest.json, if any

        Returns:
            ThumbnailPerformance record
        """
        thumbnail = self.record_thumbnail(content_id, video_id, selected_style)
        thumbnail.prepared_variants = (
            [s.value for s in prepared_styles] if prepared_styles else None
        )
        thumbnail.manifest_path = manifest_path
        self._save_state()

        logger.info(
            f"Recorded Test & Compare selection: {content_id} -> "
            f"{selected_style.value} (manual record; Studio has no API)"
        )

        return thumbnail

    def get_test_compare_outcome(self, content_id: str) -> ThumbnailPerformance | None:
        """
        Read the recorded outcome for one Test & Compare experiment.

        This reuses the same stored record `update_performance` already
        populates from Analytics (via `youtube_sync` /
        `batch_update_from_analytics`) -- no second Analytics read path is
        added. `ctr` stays `None`, not `0.0`, until a sync has actually
        written a rate for this content_id, and any non-None rate still
        carries its `ctr_source` -- the same card-click-rate-is-not-a-
        thumbnail-CTR refusal applies here as everywhere else in this
        module (see `_usable` / Metric identity).

        Args:
            content_id: Content/journey ID

        Returns:
            The ThumbnailPerformance record, or None if nothing was ever
            recorded for this content_id.
        """
        return self.thumbnails.get(content_id)

    def update_performance(
        self,
        content_id: str,
        impressions: int | None = None,
        clicks: int | None = None,
        views: int | None = None,
        ctr: float | None = None,
        ctr_source: str | None = None,
        avg_watch_time: float | None = None,
    ) -> ThumbnailPerformance | None:
        """
        Update performance metrics for a thumbnail.

        Args:
            content_id: Content/journey ID
            impressions: Number of impressions
            clicks: Number of clicks
            views: Video view count
            ctr: Click rate (0.0-1.0), or None for unmeasured
            ctr_source: `metric_source` tag for the denominator `ctr` was
                measured against. Omitted records UNKNOWN, which the style
                analysis refuses rather than assume it is thumbnail data.
            avg_watch_time: Average watch time in seconds

        Returns:
            Updated ThumbnailPerformance or None if not found
        """
        if content_id not in self.thumbnails:
            logger.warning(f"No thumbnail record for: {content_id}")
            return None

        thumbnail = self.thumbnails[content_id]

        if impressions is not None:
            thumbnail.impressions = impressions

        if clicks is not None:
            thumbnail.clicks = clicks

        if views is not None:
            thumbnail.views = views

        # Rate and provenance move together — no branch stores one without
        # the other, so a rate with an unrecorded denominator cannot exist.
        if ctr is not None:
            thumbnail.ctr = ctr
            thumbnail.ctr_source = metric_source.normalize(ctr_source)
        elif impressions and clicks:
            thumbnail.ctr = clicks / impressions
            thumbnail.ctr_source = metric_source.normalize(ctr_source)

        if avg_watch_time is not None:
            thumbnail.avg_watch_time = avg_watch_time

        thumbnail.last_updated = datetime.now().isoformat()

        self._save_state()

        return thumbnail

    def batch_update_from_analytics(self, analytics_data: dict[str, dict]) -> int:
        """
        Batch update performance from analytics data.

        Args:
            analytics_data: Dict of {content_id: {ctr, views, impressions, ...}}

        Returns:
            Number of records updated
        """
        updated = 0

        for content_id, data in analytics_data.items():
            if content_id in self.thumbnails:
                self.update_performance(
                    content_id=content_id,
                    impressions=data.get("impressions"),
                    clicks=data.get("clicks"),
                    views=data.get("views"),
                    ctr=data.get("ctr"),
                    ctr_source=data.get("ctr_source"),
                    avg_watch_time=data.get("avg_watch_time"),
                )
                updated += 1

        logger.info(f"Updated performance for {updated} thumbnails")
        return updated

    def _usable(self, thumbnail: ThumbnailPerformance) -> bool:
        """Admissible as evidence about a THUMBNAIL? Three refusals, no
        defaults: the rate exists (None is unmeasured, not zero), it was
        measured against thumbnail impressions, and there are enough of them.
        """
        return (
            thumbnail.ctr is not None
            and metric_source.is_thumbnail_click_through(thumbnail.ctr_source)
            and thumbnail.impressions >= self.MIN_IMPRESSIONS
        )

    def missing_data_reason(self) -> str | None:
        """Why there is no thumbnail click-through data, or None if there is."""
        if any(self._usable(t) for t in self.thumbnails.values()):
            return None

        have = sorted(
            {t.ctr_source for t in self.thumbnails.values() if t.ctr is not None}
        )
        if not have:
            return "no thumbnail click-through data: no click rate recorded at all"
        return (
            f"no thumbnail click-through data: the rates on record ({', '.join(have)})"
            " are measured against something other than thumbnail impressions;"
            " the Analytics API exposes no impressionClickThroughRate"
        )

    def get_style_analysis(self) -> dict[ThumbnailStyle, StyleAnalysis]:
        """
        Analyze thumbnail CLICK-THROUGH performance by style. A rate that is
        missing, or not measured against thumbnail impressions, is excluded
        outright — never averaged in, never counted as zero.

        Returns:
            Dict mapping style to StyleAnalysis (empty when none qualifies)
        """
        # Group by style
        style_data: dict[ThumbnailStyle, list[ThumbnailPerformance]] = defaultdict(list)

        for thumbnail in self.thumbnails.values():
            if thumbnail.style != ThumbnailStyle.UNKNOWN and self._usable(thumbnail):
                style_data[thumbnail.style].append(thumbnail)

        # Overall average, over admissible records only
        all_ctrs = [t.ctr for t in self.thumbnails.values() if self._usable(t)]
        overall_avg_ctr = sum(all_ctrs) / len(all_ctrs) if all_ctrs else 0

        # Analyze each style
        analyses = {}

        for style, valid_thumbnails in style_data.items():
            # Calculate metrics
            ctrs = [t.ctr for t in valid_thumbnails]
            avg_ctr = sum(ctrs) / len(ctrs)

            # Standard deviation
            variance = sum((c - avg_ctr) ** 2 for c in ctrs) / len(ctrs)
            std_dev = variance**0.5

            # Total impressions and clicks
            total_impressions = sum(t.impressions for t in valid_thumbnails)
            total_clicks = sum(t.clicks for t in valid_thumbnails)

            # Confidence based on sample size
            confidence = min(1.0, len(valid_thumbnails) / (self.min_sample_size * 2))

            # vs average
            vs_avg = (
                (avg_ctr - overall_avg_ctr) / overall_avg_ctr * 100
                if overall_avg_ctr > 0
                else 0
            )

            # The age of THIS style's samples, not of the report.
            newest_at = evidence_age.newest(t.last_updated for t in valid_thumbnails)
            age = evidence_age.age_days(newest_at)

            analyses[style] = StyleAnalysis(
                style=style,
                sample_size=len(valid_thumbnails),
                total_impressions=total_impressions,
                total_clicks=total_clicks,
                avg_ctr=avg_ctr,
                ctr_std_dev=std_dev,
                confidence_level=confidence,
                vs_average_ctr=vs_avg,
                is_top_performer=False,  # Will be set below
                newest_sample_at=newest_at,
                evidence_age_days=age,
                evidence=evidence_age.classify(age),
            )

        # Mark top performer
        if analyses:
            top_style = max(analyses.keys(), key=lambda s: analyses[s].avg_ctr)
            analyses[top_style].is_top_performer = True

        return analyses

    def get_recommended_style(self) -> StyleRecommendation:
        """
        Recommend a thumbnail style with the age of the evidence behind it.

        A style is named only from thumbnail click-through data; with none,
        `style` is None and `reason` says what is missing. The old
        `(BOLD, 0.0)` fallback was a guess dressed as a finding. The reason
        is a return value, not a log line, so no caller can render a result
        without confronting it -- and neither the style nor the age can be
        obtained without the other, because they arrive in one frozen value.

        Evidence past `STALE_AFTER_DAYS` still yields its style: that is the
        last valid recommendation, marked stale with its age. Dropping it
        and fabricating a new "best" are both wrong.

        Returns:
            StyleRecommendation
        """
        analyses = self.get_style_analysis()

        if not analyses:
            reason = self.missing_data_reason()
            logger.info(reason)
            return StyleRecommendation(
                style=None,
                confidence=0.0,
                evidence=EvidenceState.NOTHING_MEASURED,
                sample_size=0,
                evidence_newest_at=None,
                evidence_age_days=None,
                reason=reason,
            )

        # Check if we have enough data for reliable recommendation
        max_sample = max(a.sample_size for a in analyses.values())

        if max_sample < self.min_sample_size:
            reason = (
                f"insufficient thumbnail click-through samples "
                f"({max_sample}/{self.min_sample_size} needed)"
            )
            logger.info(reason)
            best_so_far = max(analyses.values(), key=lambda a: a.avg_ctr)
            return StyleRecommendation(
                style=None,
                confidence=0.0,
                evidence=EvidenceState.INSUFFICIENT,
                sample_size=max_sample,
                evidence_newest_at=best_so_far.newest_sample_at,
                evidence_age_days=best_so_far.evidence_age_days,
                reason=reason,
            )

        # Find top performer with confidence
        top_analysis = max(analyses.values(), key=lambda a: a.avg_ctr)

        # Adjust confidence based on statistical significance
        # Check if top performer is significantly better than others
        other_ctrs = [
            a.avg_ctr for a in analyses.values() if a.style != top_analysis.style
        ]

        if other_ctrs:
            second_best_ctr = max(other_ctrs)
            ctr_difference = top_analysis.avg_ctr - second_best_ctr

            # Is difference significant? (more than 1 std dev)
            if ctr_difference > top_analysis.ctr_std_dev:
                # Statistically significant difference
                confidence = min(0.95, top_analysis.confidence_level * 1.2)
            else:
                # Not significant - lower confidence
                confidence = top_analysis.confidence_level * 0.7
        else:
            confidence = top_analysis.confidence_level * 0.5

        recommendation = StyleRecommendation(
            style=top_analysis.style,
            confidence=confidence,
            evidence=top_analysis.evidence,
            sample_size=top_analysis.sample_size,
            evidence_newest_at=top_analysis.newest_sample_at,
            evidence_age_days=top_analysis.evidence_age_days,
            reason=evidence_age.stale_reason(
                top_analysis.evidence_age_days, top_analysis.sample_size
            ),
        )

        logger.info(
            f"Recommended style: {top_analysis.style.value} "
            f"(CTR: {top_analysis.avg_ctr:.2%}, confidence: {confidence:.0%}, "
            f"{recommendation.age_phrase()})"
        )

        return recommendation

    def get_style_performance_summary(self) -> dict[str, dict]:
        """
        Style -> its rate AND the age of the samples behind it, for config
        files. A bare rate written into config outlives the data it came
        from; the age travels with it so a reader can tell.

        Returns:
            Dict mapping style name to
            {avg_ctr, sample_size, last_updated, age_days, evidence}
        """
        return {
            style.value: {
                "avg_ctr": a.avg_ctr,
                "sample_size": a.sample_size,
                "last_updated": a.newest_sample_at,
                "age_days": a.evidence_age_days,
                "evidence": a.evidence.value,
            }
            for style, a in self.get_style_analysis().items()
        }

    def generate_report(self) -> str:
        """
        Generate a human-readable A/B test report.

        Returns:
            Formatted report string
        """
        analyses = self.get_style_analysis()
        rec = self.get_recommended_style()

        lines = [
            "=" * 60,
            "THUMBNAIL A/B TEST REPORT",
            "=" * 60,
            # When this text was printed. NOT when anything was measured --
            # every number below carries its own age.
            f"Generated: {datetime.now().isoformat()} "
            "(report print time, not data time)",
            f"Total thumbnails tracked: {len(self.thumbnails)}",
            "",
            "STYLE PERFORMANCE:",
            "-" * 40,
        ]

        for style in [
            ThumbnailStyle.MINIMAL,
            ThumbnailStyle.BOLD,
            ThumbnailStyle.IMMERSIVE,
        ]:
            if style in analyses:
                a = analyses[style]
                indicator = " 🏆" if a.is_top_performer else ""
                lines.append(
                    f"  {style.value.upper()}{indicator}\n"
                    f"    Sample size: {a.sample_size}\n"
                    f"    Avg CTR: {a.avg_ctr:.2%}\n"
                    f"    vs Average: {a.vs_average_ctr:+.1f}%\n"
                    f"    Confidence: {a.confidence_level:.0%}\n"
                    f"    Evidence: {a.evidence.value.upper()}, "
                    f"{evidence_age.describe(a.newest_sample_at, a.evidence_age_days)}"
                )
            else:
                lines.append(f"  {style.value.upper()}: No data")

        lines.extend(["", "RECOMMENDATION:", "-" * 40])

        if rec.style is None:
            # Render the gap, not a style nothing measured.
            lines.append("  NO THUMBNAIL CLICK-THROUGH DATA — no style recommended")
            lines.append(f"  Evidence: {rec.evidence.value.upper()}")
            lines.append(f"  Reason: {rec.reason}")
            lines.append("=" * 60)
            return "\n".join(lines)

        lines.extend(
            [
                f"  Use {rec.style.value.upper()} style",
                f"  Confidence: {rec.confidence:.0%}",
                f"  Evidence: {rec.evidence.value.upper()}, "
                f"{rec.sample_size} samples, {rec.age_phrase()}",
            ]
        )
        if rec.reason:
            lines.append(f"  ⚠️  {rec.reason}")
        lines.append("")

        confidence = rec.confidence
        if confidence < 0.5:
            lines.append("  ⚠️  Low confidence - continue A/B testing")
        elif confidence < 0.8:
            lines.append("  📊 Moderate confidence - recommend more data")
        else:
            lines.append("  ✓ High confidence - style is validated")

        lines.append("=" * 60)

        return "\n".join(lines)

    def _load_state(self):
        """Load state from disk."""
        state_file = self.state_dir / "thumbnail_ab_state.json"

        if state_file.exists():
            try:
                with open(state_file, encoding="utf-8") as f:
                    data = json.load(f)

                for content_id, thumb_data in data.get("thumbnails", {}).items():
                    # Convert style string to enum
                    thumb_data["style"] = ThumbnailStyle(thumb_data["style"])
                    self.thumbnails[content_id] = ThumbnailPerformance(**thumb_data)

                logger.debug(f"Loaded {len(self.thumbnails)} thumbnail records")

            except (json.JSONDecodeError, TypeError, KeyError) as e:
                logger.warning(f"Could not load thumbnail state: {e}")

    def _save_state(self):
        """Save state to disk."""
        state_file = self.state_dir / "thumbnail_ab_state.json"

        data = {
            "thumbnails": {
                content_id: {
                    **asdict(thumb),
                    "style": thumb.style.value,  # Convert enum to string
                }
                for content_id, thumb in self.thumbnails.items()
            },
            "last_updated": datetime.now().isoformat(),
        }

        atomic_write_json(state_file, data)
