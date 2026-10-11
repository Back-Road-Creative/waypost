"""
SEO Optimizer - Analytics-Driven Metadata Optimization.

Learns from historical performance data to generate improved SEO metadata.
Analyzes top-performing content to identify successful patterns and apply
them to new content generation.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from ..core.metadata import ContentType, SEOMetadata
from . import metric_source

# Performance score = blend of normalized age-adjusted views and a CLICK RATE.
# That rate is NOT a thumbnail click-through rate and is not evidence that a
# title or thumbnail won anyone's click: the only rate this service can fetch
# is YouTube's cardClickRate — of the viewers ALREADY watching, how many
# clicked the in-player info card (metric_source.YOUTUBE_CARD_CLICK_RATE). It
# is a real engagement signal, and weighted above raw views because those
# conflate reach, recency and topic. The views term is views-per-day (see
# _views_per_day), not lifetime views, so an old video's stale total doesn't
# outrank a new video that is actually performing better right now.
#
# The rate is frequently absent (no analytics data, no youtube_sync run yet),
# and the card click rate is not a thumbnail click-through rate. When a
# performer's "ctr" key is missing or None, that term is dropped and the full
# weight goes to the views term instead of silently scoring it as 0 (which
# would understate performance for every video without the data).
_CLICK_RATE_WEIGHT = 0.6
_VIEWS_WEIGHT = 0.4

#: Reported as a pattern's `ctr_source` when the samples behind it were
#: measured against DIFFERENT denominators. It is not a `metric_source` tag
#: and never normalizes to one -- it is the absence of a single denominator,
#: which is a third answer alongside "this one" and "nobody declared".
#:
#: The module comment above has always said a card click rate is not evidence
#: a thumbnail won a click. Nothing enforced it: every rate went into one
#: `max_click_rate` base and one blended score whatever it was a rate OF, so a
#: Studio-CSV thumbnail CTR landing beside card rates would be normalized
#: against them and averaged into them. 0.04 of one thing and 0.06 of another
#: do not average to 0.05 of anything, so a mixed pattern reports no rate --
#: the same refusal applied to an absent one.
MIXED_DENOMINATORS = "mixed"


def _sole_source(sources) -> str:
    """The one denominator every rate here shares, else MIXED_DENOMINATORS.

    An empty set is UNKNOWN: nothing was measured, which is not a
    disagreement. UNKNOWN is its own group and never a wildcard -- "nobody
    declared" is not evidence of matching whatever else happens to be here.
    """
    distinct = set(sources)
    if not distinct:
        return metric_source.UNKNOWN
    if len(distinct) == 1:
        return distinct.pop()
    return MIXED_DENOMINATORS


# Title-correspondence decay. Historical analytics key to the title STRING
# that was live when the views accrued; a later title rewrite changes
# those strings, so a brand-new title must not be tuned against the CTR of a
# string that no longer exists. We cannot know which old titles survive, so we
# apply a pragmatic exponential age-decay: a sample's weight halves every
# _DECAY_HALF_LIFE_DAYS. Recent samples (titles likely still in their current
# form) dominate; ancient samples (likely-rewritten titles) fade toward zero
# without being hard-dropped. Performance is keyed by content_id (the video),
# never by title-string, so re-titling a video keeps its history attributable.
_DECAY_HALF_LIFE_DAYS = 180.0


def _age_decay_weight(last_updated: Optional[str]) -> float:
    """Exponential recency weight in (0, 1]; 1.0 when timestamp is unknown."""
    if not last_updated:
        return 1.0
    try:
        ts = datetime.fromisoformat(last_updated)
    except (ValueError, TypeError):
        return 1.0
    age_days = max(0.0, (datetime.now() - ts).total_seconds() / 86400.0)
    return 0.5 ** (age_days / _DECAY_HALF_LIFE_DAYS)


def _evidence_age_days(last_updated: Optional[str]) -> Optional[float]:
    """How old this sample's evidence is, or None when nothing says.

    Deliberately the same two guards as `_age_decay_weight` and deliberately
    the opposite answer to them. That function must return a number the score
    can be multiplied by, so an unreadable timestamp becomes the neutral 1.0 --
    which is a statement about what the ranking DID, not about what we know. A
    reader asking how old the evidence is gets `None`, because "no timestamp"
    is not "updated today"; a 0.0 here would be the same invented number
    `avg_ctr` stopped substituting for an absent rate.
    """
    if not last_updated:
        return None
    try:
        ts = datetime.fromisoformat(last_updated)
    except (ValueError, TypeError):
        return None
    return max(0.0, (datetime.now() - ts).total_seconds() / 86400.0)


def _views_per_day(performer: dict) -> float:
    """Views normalized by days-since-publish.

    Prefers the event's `published_at`. Where an event does not carry it,
    falls back to `first_updated` — the earliest
    performance-update timestamp tracker._get_top_performers has seen for
    this content_id, which is the closest available proxy for "when this
    video started accruing views" without the real publish date. If neither
    is present (older data with no timestamps at all), falls back to raw
    views so untimestamped content isn't penalized to zero.
    """
    views = performer.get("views", 0) or 0
    published = performer.get("published_at") or performer.get("first_updated")
    if not published:
        return views
    try:
        ts = datetime.fromisoformat(published)
    except (ValueError, TypeError):
        return views
    age_days = max(1.0, (datetime.now() - ts).total_seconds() / 86400.0)
    return views / age_days


@dataclass
class SEORecommendation:
    """A specific SEO improvement recommendation"""

    category: str  # 'title', 'description', 'tags', 'quality'
    issue: str
    suggestion: str

    #: How urgently the rule that produced this wants attention --
    #: 'high', 'medium' or 'low', fixed per rule when the rule was
    #: written. It is an author's ranking, not a measured effect on any
    #: metric, and is named accordingly: nothing here observes what
    #: acting on a recommendation did. There is deliberately no
    #: `confidence`; it was a per-rule literal (0.85/0.75/0.70/0.65)
    #: that nothing computed and no caller read, rendered to the
    #: operator as `Confidence: 85%` -- a statistic-shaped claim with
    #: no statistic behind it.
    priority: str


@dataclass
class PerformancePattern:
    """Identified pattern from successful content"""

    pattern_type: str  # 'tag', 'title_length', 'keyword', etc.
    value: any
    avg_views: float
    #: The click rate measured across this pattern's samples, or None when not
    #: one of them was measured. A rate is "frequently absent (no analytics
    #: data, no youtube_sync run yet)", so substituting 0 for absence -- which
    #: this did -- understated the normal case, exactly the way _perf_score
    #: refuses to. A measured 0.0 is evidence and still averages in.
    avg_ctr: Optional[float]
    sample_size: int
    #: How many of those `sample_size` samples actually carried a rate: the
    #: denominator behind `avg_ctr`, so a reader can tell "0.0 across three
    #: videos" from "nobody looked".
    ctr_sample_size: int = 0
    #: WHAT `avg_ctr` is a rate of -- a `metric_source` tag when every sample
    #: agreed, `MIXED_DENOMINATORS` when they did not (and then `avg_ctr` is
    #: None), `metric_source.UNKNOWN` when nothing was measured. A rate
    #: without this is a number without a question.
    ctr_source: str = metric_source.UNKNOWN
    #: Mean age in days of the samples that carried a timestamp, or None when
    #: not one of them did. The rate fields above exist because a number whose
    #: denominator you cannot see is not readable; `perf_score` had the same
    #: problem in a different unit. It is age-decayed before the caller ever
    #: sees it, so a pattern resting on 18-month-old evidence arrives already
    #: discounted to a quarter and is indistinguishable, in a sorted list,
    #: from a fresh pattern whose blend is genuinely that weak. This says
    #: which one it is.
    evidence_age_days: Optional[float] = None
    #: How many of those `sample_size` samples carried a timestamp: the
    #: denominator behind `evidence_age_days`, so "360 days" is legible as
    #: "across all four videos" or "across the one of four anybody dated".
    #: An undated sample is left out of that mean rather than counted as
    #: today -- the same refusal `ctr_sample_size` records for an absent rate.
    dated_sample_size: int = 0
    #: The mean recency weight actually applied to `perf_score` -- the
    #: multiplier, not a re-derivation of it, so the two cannot disagree.
    #: EVERY sample is in this mean, including undated ones, because every
    #: sample really was weighted: `_age_decay_weight` gives an unknown
    #: timestamp 1.0, and 1.0 is what the ranking used. That makes 1.0
    #: ambiguous on its own -- fresh, or undated -- and `evidence_age_days`
    #: is what tells those apart.
    age_decay_weight: float = 1.0
    # Age-decayed blend of views + click rate (see _CLICK_RATE_WEIGHT).
    # This — not avg_views — is the ranking key.
    perf_score: float = 0.0


class SEOOptimizer:
    """
    Analyzes historical performance to optimize SEO metadata.

    Uses analytics data to:
    1. Identify patterns in successful content
    2. Generate optimization recommendations
    3. Predict performance of new metadata
    4. Apply learned patterns to new content
    """

    def __init__(self, manager):
        """
        Initialize SEO optimizer.

        Args:
            manager: SEOManager instance with historical data
        """
        self.manager = manager
        self.patterns = None
        self._analyzed = False

    def analyze_patterns(
        self, min_views: int = 100
    ) -> Dict[str, List[PerformancePattern]]:
        """
        Analyze historical data to identify successful patterns.

        Args:
            min_views: Minimum views to consider content in analysis

        Returns:
            Dict of pattern categories with identified patterns
        """
        patterns = {
            "tags": [],
            "title_lengths": [],
            "keywords": [],
            "quality_scores": [],
        }

        # Get all content with analytics
        analytics = self.manager.analytics.get_summary()
        top_performers = analytics.get("top_performers", [])

        if not top_performers:
            self.patterns = patterns
            self._analyzed = True
            return patterns

        # Eligible performers (above the min-views floor, with metadata).
        eligible = []
        for performer in top_performers:
            if performer.get("views", 0) < min_views:
                continue
            metadata = self.manager.get_metadata(performer["content_id"])
            if not metadata:
                continue
            eligible.append((performer, metadata))

        # Normalization bases for the blended performance score.
        max_vpd = max((_views_per_day(p) for p, _ in eligible), default=0) or 1
        # One base PER DENOMINATOR, never one across all of them. Pooled, a
        # thumbnail CTR (typically an order of magnitude larger than a card
        # rate) would set the base for the card rates and crush them toward
        # zero -- a ranking artifact of nothing but which denominators
        # happened to be present in the sample.
        max_click_rate_by_source = defaultdict(float)
        for performer, _ in eligible:
            rate = performer.get("ctr")
            if rate is not None:
                source = metric_source.normalize(performer.get("ctr_source"))
                max_click_rate_by_source[source] = max(
                    max_click_rate_by_source[source], rate
                )

        def _perf_score(performer) -> float:
            """Age-decayed blend of views/day + click rate.

            The click rate is a general engagement signal, NOT evidence that a
            title or thumbnail won anyone's click — see the module comment.
            """
            vpd = _views_per_day(performer)
            rate = performer.get("ctr")
            if rate is None:
                # No click-rate data for this performer — renormalize to full
                # weight on the (age-normalized) views term rather than
                # scoring it as if the rate were 0.
                blend = vpd / max_vpd
            else:
                source = metric_source.normalize(performer.get("ctr_source"))
                base = max_click_rate_by_source.get(source) or 1
                blend = _VIEWS_WEIGHT * (vpd / max_vpd) + _CLICK_RATE_WEIGHT * (
                    rate / base
                )
            return _age_decay_weight(performer.get("last_updated")) * blend

        # Analyze tags — per-tag perf scores (click-rate-weighted + decayed).
        tag_performance = defaultdict(list)

        for performer, metadata in eligible:
            views = performer.get("views", 0)
            # None stays None all the way to the pattern. `or 0` here was the
            # substitution the score function four lines up exists to avoid.
            ctr = performer.get("ctr")
            measured = 0 if ctr is None else 1
            # UNKNOWN when there is no rate: a denominator is a fact about a
            # measurement, and there is no measurement to have one.
            source = (
                metric_source.UNKNOWN
                if ctr is None
                else metric_source.normalize(performer.get("ctr_source"))
            )
            score = _perf_score(performer)
            # Read back out of the score the one thing a caller cannot infer
            # from it. Same helper `_perf_score` multiplies by, called on the
            # same value, so the reported discount is the applied discount.
            decay = _age_decay_weight(performer.get("last_updated"))
            age_days = _evidence_age_days(performer.get("last_updated"))

            for tag in metadata.tags:
                tag_performance[tag].append(
                    {
                        "views": views,
                        "ctr": ctr,
                        "score": score,
                        "ctr_source": source,
                        "decay": decay,
                        "age_days": age_days,
                    }
                )

            patterns["title_lengths"].append(
                PerformancePattern(
                    pattern_type="title_length",
                    value=len(metadata.title),
                    avg_views=views,
                    avg_ctr=ctr,
                    sample_size=1,
                    ctr_sample_size=measured,
                    ctr_source=source,
                    evidence_age_days=age_days,
                    dated_sample_size=0 if age_days is None else 1,
                    age_decay_weight=decay,
                    perf_score=score,
                )
            )

            if metadata.quality_score:
                patterns["quality_scores"].append(
                    PerformancePattern(
                        pattern_type="quality_score",
                        value=metadata.quality_score,
                        avg_views=views,
                        avg_ctr=ctr,
                        sample_size=1,
                        ctr_sample_size=measured,
                        ctr_source=source,
                        evidence_age_days=age_days,
                        dated_sample_size=0 if age_days is None else 1,
                        age_decay_weight=decay,
                        perf_score=score,
                    )
                )

        # Aggregate tag patterns
        for tag, performances in tag_performance.items():
            n = len(performances)
            # Averaged over the samples that HAVE a rate, never over n: a video
            # nobody measured is not a video that scored zero.
            measured = [p for p in performances if p["ctr"] is not None]
            source = _sole_source(p["ctr_source"] for p in measured)
            # Averaging stops at the denominator. Where the samples disagree
            # there is no quantity to average, so the rate is withheld and
            # `ctr_source` says why -- views and sample_size survive, because
            # refusing the rate is not a reason to throw away the rest.
            rates = [p["ctr"] for p in measured] if source != MIXED_DENOMINATORS else []
            # Ages split the same way rates do: the mean runs over the samples
            # that carry one. Unlike a denominator, ages that disagree still
            # average to something real -- 90 days and 270 days ARE 180 days of
            # mean staleness -- so nothing is withheld here; `dated_sample_size`
            # is what warns a reader the mean rests on one video out of four.
            dated = [p["age_days"] for p in performances if p["age_days"] is not None]
            patterns["tags"].append(
                PerformancePattern(
                    pattern_type="tag",
                    value=tag,
                    avg_views=sum(p["views"] for p in performances) / n,
                    avg_ctr=(sum(rates) / len(rates)) if rates else None,
                    sample_size=n,
                    ctr_sample_size=len(rates),
                    ctr_source=source,
                    evidence_age_days=(sum(dated) / len(dated)) if dated else None,
                    dated_sample_size=len(dated),
                    age_decay_weight=sum(p["decay"] for p in performances) / n,
                    perf_score=sum(p["score"] for p in performances) / n,
                )
            )

        # Rank on the CTR-weighted, age-decayed performance score (not views).
        # Both lists, in place, for the same reason: `title_lengths` is
        # appended to in content-iteration order, and every reader of it asks
        # for the BEST patterns, never the first ones the analytics summary
        # happened to list. Leaving the order to the caller meant each reader
        # had to remember to sort, and `predict_performance` did not -- it
        # took `[:5]` and called them "the top patterns". Establishing the
        # order here is what makes that slice mean what it says, for this
        # reader and every future one, rather than fixing the one call site
        # that was caught. Nothing reads these lists expecting content order:
        # the daemon slices `tags[:20]`, the examples take only `len()`.
        for ranked in (patterns["tags"], patterns["title_lengths"]):
            ranked.sort(key=lambda x: x.perf_score, reverse=True)

        self.patterns = patterns
        self._analyzed = True
        return patterns

    def get_top_performing_tags(self, limit: int = 10) -> List[str]:
        """
        Get tags that correlate with high performance.

        Args:
            limit: Number of tags to return

        Returns:
            List of top-performing tags
        """
        if not self._analyzed:
            self.analyze_patterns()

        if not self.patterns or not self.patterns["tags"]:
            return []

        return [p.value for p in self.patterns["tags"][:limit]]

    def get_optimal_title_length(self) -> Tuple[int, int]:
        """
        Get optimal title length range based on top performers.

        Returns:
            Tuple of (min_length, max_length)
        """
        if not self._analyzed:
            self.analyze_patterns()

        if not self.patterns or not self.patterns["title_lengths"]:
            # Default to platform best practices
            return (50, 70)

        # Get the top 25% of performers by the CTR-weighted, age-decayed
        # performance score — NOT raw views, which would tune the
        # title length toward whatever happened to go viral regardless of how
        # well the title itself converted. `analyze_patterns` has already put
        # them in that order, so this slices rather than re-sorting, exactly
        # as `get_top_performing_tags` slices `tags`.
        by_score = self.patterns["title_lengths"]

        top_quarter = by_score[: max(1, len(by_score) // 4)]

        if not top_quarter:
            return (50, 70)

        lengths = [p.value for p in top_quarter]
        avg_length = sum(lengths) / len(lengths)

        # Return range around average
        return (int(avg_length - 10), int(avg_length + 10))

    def generate_recommendations(self, content_id: str) -> List[SEORecommendation]:
        """
        Generate specific recommendations for improving content SEO.

        Args:
            content_id: Content to analyze

        Returns:
            List of recommendations
        """
        if not self._analyzed:
            self.analyze_patterns()

        metadata = self.manager.get_metadata(content_id)
        if not metadata:
            return []

        recommendations = []

        # Check title length
        optimal_min, optimal_max = self.get_optimal_title_length()
        if len(metadata.title) < optimal_min:
            recommendations.append(
                SEORecommendation(
                    category="title",
                    issue=f"Title too short ({len(metadata.title)} chars)",
                    suggestion=f"Expand title to {optimal_min}-{optimal_max} chars for better CTR",
                    priority="high",
                )
            )
        elif len(metadata.title) > optimal_max + 10:
            recommendations.append(
                SEORecommendation(
                    category="title",
                    issue=f"Title too long ({len(metadata.title)} chars)",
                    suggestion=f"Shorten title to {optimal_min}-{optimal_max} chars",
                    priority="medium",
                )
            )

        # Check tags against top performers
        top_tags = set(self.get_top_performing_tags(15))
        current_tags = set(metadata.tags)
        missing_tags = top_tags - current_tags

        if missing_tags and len(current_tags) < 10:
            recommendations.append(
                SEORecommendation(
                    category="tags",
                    issue="Missing high-performing tags",
                    suggestion=f"Consider adding: {', '.join(list(missing_tags)[:5])}",
                    priority="medium",
                )
            )

        # Check quality score
        if metadata.quality_score and metadata.quality_score < 0.7:
            recommendations.append(
                SEORecommendation(
                    category="quality",
                    issue=f"Low quality score ({metadata.quality_score:.2f})",
                    suggestion="Improve title length, keyword density, and tag count",
                    priority="medium",
                )
            )

        # Check tag count
        if len(metadata.tags) < 5:
            recommendations.append(
                SEORecommendation(
                    category="tags",
                    issue=f"Too few tags ({len(metadata.tags)})",
                    suggestion="Add more relevant tags (target 8-12 tags)",
                    priority="medium",
                )
            )

        return recommendations

    def optimize_metadata(
        self, content_type: ContentType, topic: str, **kwargs
    ) -> SEOMetadata:
        """
        Generate optimized metadata using learned patterns.

        Args:
            content_type: Type of content
            topic: Content topic
            **kwargs: Additional generation parameters

        Returns:
            Optimized SEOMetadata
        """
        if not self._analyzed:
            self.analyze_patterns()

        # Get successful tags
        top_tags = self.get_top_performing_tags(8)

        # Merge with existing tags
        existing_tags = kwargs.get("existing_tags", [])
        if existing_tags:
            # Combine top tags with existing
            combined_tags = list(set(existing_tags + top_tags))
        else:
            combined_tags = top_tags

        kwargs["existing_tags"] = combined_tags[:12]  # Limit to 12

        # Feed the learned title-length range into generation. Previously this
        # value was computed and dropped on the floor, so every "optimized"
        # title was tuned on tags alone. `manager.generate` clamps it against
        # the platform's hard title cap and treats it as advisory (it can only
        # shorten, on a word boundary) - see BaseSEOGenerator._optimize_title.
        # An explicit caller-supplied range wins over the learned one.
        kwargs.setdefault("title_length_range", self.get_optimal_title_length())

        # Don't enhance topic here — the platform-specific generator
        # (e.g., YouTubeMetadataGenerator._generate_title) handles title
        # expansion with appropriate descriptors. Adding suffixes in both
        # the optimizer and generator causes duplication.

        # Generate metadata with enhancements
        metadata = self.manager.generate(
            content_type=content_type, topic=topic, **kwargs
        )

        return metadata

    def predict_performance(self, metadata: SEOMetadata) -> Dict[str, float]:
        """
        Predict performance metrics for given metadata.

        Args:
            metadata: Metadata to analyze

        Returns:
            Dict with predicted metrics (views, ctr, engagement_rate) plus
            `estimated_ctr_sample_size` (an int): how many of the patterns
            behind `estimated_ctr` carried a measured rate. Zero means the
            0.0 is a placeholder, not a prediction. The rate itself stays a
            float because callers format it with `:.2%`; the count is what
            says whether the number means anything.
        """
        if not self._analyzed:
            self.analyze_patterns()

        predictions = {
            "estimated_views": 0.0,
            "estimated_ctr": 0.0,
            "estimated_ctr_sample_size": 0,
            #: What `estimated_ctr` would be a rate OF. UNKNOWN means nothing
            #: was measured; MIXED_DENOMINATORS means things were, and they
            #: were not the same thing -- a different answer, and the screen
            #: has to be able to tell them apart.
            "estimated_ctr_source": metric_source.UNKNOWN,
            "confidence": 0.0,
        }

        if not self.patterns:
            return predictions

        # Base prediction on similar successful content
        score = 0.0
        factors = 0

        # Factor 1: Tag overlap with successful content
        top_tags = set(self.get_top_performing_tags(10))
        current_tags = set(metadata.tags)
        tag_overlap = len(top_tags & current_tags) / max(len(top_tags), 1)
        score += tag_overlap * 0.4
        factors += 1

        # Factor 2: Title length optimization
        optimal_min, optimal_max = self.get_optimal_title_length()
        title_len = len(metadata.title)
        if optimal_min <= title_len <= optimal_max:
            score += 0.3
        else:
            # Partial score based on how close
            deviation = min(abs(title_len - optimal_min), abs(title_len - optimal_max))
            score += max(0, 0.3 - (deviation / 100))
        factors += 1

        # Factor 3: Quality score
        if metadata.quality_score:
            score += metadata.quality_score * 0.3
            factors += 1

        # Normalize score
        if factors > 0:
            predictions["confidence"] = score / factors

        # Estimate views and click rate from the top patterns — the five
        # best-scoring, which is what this slice takes now that
        # `analyze_patterns` ranks the list. Both divide by the number of
        # patterns actually summed, never by a literal 5: with two patterns
        # the old form reported two fifths of what it measured.
        #
        # Note the two halves are not measured the same way: the selection is
        # by `perf_score`, which is age-decayed views-PER-DAY blended with a
        # click rate, while `avg_views` is an undecayed LIFETIME total. So the
        # best-scoring five are chosen on current momentum and then averaged
        # on career totals. Picking the right five is a prerequisite for that
        # mismatch mattering, not a fix for it.
        top_patterns = self.patterns["title_lengths"][:5]
        if top_patterns:
            avg_top_views = sum(p.avg_views for p in top_patterns) / len(top_patterns)
            predictions["estimated_views"] = avg_top_views * predictions["confidence"]

            measured = [p for p in top_patterns if p.avg_ctr is not None]
            source = _sole_source(p.ctr_source for p in measured)
            predictions["estimated_ctr_source"] = source
            # Same rule one level up: patterns measured against different
            # denominators have no common rate to predict.
            rates = (
                [p.avg_ctr for p in measured] if source != MIXED_DENOMINATORS else []
            )
            predictions["estimated_ctr_sample_size"] = len(rates)
            if rates:
                avg_top_ctr = sum(rates) / len(rates)
                predictions["estimated_ctr"] = avg_top_ctr * predictions["confidence"]

        return predictions

    def compare_metadata(
        self, metadata_a: SEOMetadata, metadata_b: SEOMetadata
    ) -> Dict:
        """
        Compare two metadata variants and predict which will perform better.

        Args:
            metadata_a: First variant
            metadata_b: Second variant

        Returns:
            Dict with comparison results
        """
        pred_a = self.predict_performance(metadata_a)
        pred_b = self.predict_performance(metadata_b)

        comparison = {
            "variant_a": {"title": metadata_a.title, "predictions": pred_a},
            "variant_b": {"title": metadata_b.title, "predictions": pred_b},
            "recommendation": None,
            "confidence_delta": abs(pred_a["confidence"] - pred_b["confidence"]),
        }

        if pred_a["estimated_views"] > pred_b["estimated_views"]:
            comparison["recommendation"] = "variant_a"
        else:
            comparison["recommendation"] = "variant_b"

        return comparison
