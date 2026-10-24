"""
Analytics Tracker for SEO performance metrics.

Tracks:
- Metadata generation events
- Performance metrics (views, clicks, impressions)
- Quality trends
- Top performers
"""

import uuid
from datetime import datetime

from ..core.events import EventType, SEOEvent
from ..core.metadata import SEOMetadata
from ..storage.event_store import SEOEventStore
from . import metric_source

#: What a ``ctr`` is a ratio OF. ``ctr_source`` says what the rate was measured
#: against (card clicks vs thumbnail impressions); this says how the number in
#: hand was arrived at. A rate recomputed from counts this service holds and a
#: rate a platform reported are different quantities, so a screen showing them
#: in one column has to be able to say which it is looking at. ``ctr_basis`` is
#: ``None`` exactly when ``ctr`` is -- nothing was measured.
CTR_BASIS_RECOMPUTED = "clicks_over_impressions"
CTR_BASIS_REPORTED = "reported_rate"

#: How many rows ``_get_top_performers`` slices off the sorted aggregate. A
#: reader of ``get_summary`` sees only this many rows; ``performance_metrics``
#: reports this constant alongside ``total_content_with_performance`` so a
#: caller never has to guess the slice size to say "N of M".
TOP_PERFORMERS_CAP = 10


def _round_or_none(value: float | None, digits: int) -> float | None:
    """Round a possibly-unknown average. None survives as None: an average
    nobody measured must not come out of the rounding as 0.0."""
    return None if value is None else round(value, digits)


class AnalyticsTracker:
    """
    Tracks SEO performance and generates analytics reports.

    Integrates with event store for historical analysis.
    """

    def __init__(self, store: SEOEventStore, site_id: str):
        """
        Initialize analytics tracker.

        Args:
            store: SEOEventStore instance
            site_id: Site identifier for scoping
        """
        self.store = store
        self.site_id = site_id

    def track_generation(self, metadata: SEOMetadata) -> str:
        """
        Track metadata generation event.

        Stores quality metrics for analysis.

        Args:
            metadata: Generated SEO metadata

        Returns:
            Event ID
        """
        event = SEOEvent(
            event_id=str(uuid.uuid4()),
            event_type=EventType.METADATA_GENERATED,
            site_id=self.site_id,
            content_id=metadata.content_id,
            timestamp=metadata.generated_at,
            data={
                "title_length": len(metadata.title),
                "description_length": len(metadata.description),
                "tag_count": len(metadata.tags),
                "quality_score": metadata.quality_score,
                "keyword_density": metadata.keyword_density,
                "readability_score": metadata.readability_score,
                "validation_status": metadata.validation_status,
                "fixes_applied": metadata.fixes_applied,
                "platform": metadata.platform.value,
                "content_type": metadata.content_type.value,
            },
        )

        return self.store.append_event(event)

    def track_performance(
        self,
        content_id: str,
        views: int | None = None,
        clicks: int | None = None,
        impressions: int | None = None,
        engagement_rate: float | None = None,
        **kwargs,
    ) -> str:
        """
        Track content performance metrics from platform analytics.

        Args:
            content_id: Content identifier
            views: View count
            clicks: Click-through count
            impressions: SERP/feed impressions
            engagement_rate: Engagement percentage
            **kwargs: Platform-specific metrics. Pass ``ctr_source`` (a
                ``metric_source`` tag) beside any ``ctr`` so consumers can
                tell which denominator the rate was measured against.

        Returns:
            Event ID
        """
        # Calculate CTR if we have data
        ctr = None
        if impressions and clicks:
            ctr = clicks / impressions

        event = SEOEvent(
            event_id=str(uuid.uuid4()),
            event_type=EventType.PERFORMANCE_UPDATED,
            site_id=self.site_id,
            content_id=content_id,
            timestamp=datetime.now().isoformat(),
            data={
                "views": views,
                "clicks": clicks,
                "impressions": impressions,
                "engagement_rate": engagement_rate,
                "ctr": ctr,
                **kwargs,
            },
        )

        return self.store.append_event(event)

    def get_summary(
        self, start_date: str | None = None, end_date: str | None = None
    ) -> dict:
        """
        Get analytics summary for date range.

        Args:
            start_date: ISO timestamp for range start
            end_date: ISO timestamp for range end

        Returns:
            Dict with aggregated metrics
        """
        # Query events
        events = self.store.query_by_site(
            site_id=self.site_id, start_date=start_date, end_date=end_date
        )

        # Separate by event type
        generation_events = [
            e for e in events if e.event_type == EventType.METADATA_GENERATED
        ]

        performance_events = [
            e for e in events if e.event_type == EventType.PERFORMANCE_UPDATED
        ]

        # Aggregate generation metrics. Each average is over the events that
        # actually reported the field -- an event nobody scored is not a 0.00
        # score, and counting it in the divisor drags the mean exactly as a
        # real zero would.
        avg_quality_score = self._mean_of_reported(generation_events, "quality_score")
        avg_title_length = self._mean_of_reported(generation_events, "title_length")
        avg_tag_count = self._mean_of_reported(generation_events, "tag_count")

        # Aggregate performance metrics
        total_views = sum(e.data.get("views") or 0 for e in performance_events)
        total_clicks = sum(e.data.get("clicks") or 0 for e in performance_events)
        total_impressions = sum(
            e.data.get("impressions") or 0 for e in performance_events
        )

        # The site-wide rate goes through the same resolver as the per-content
        # one, so a period with no impressions reads as unmeasured here
        # too rather than as the measurement "everyone shown this declined to
        # click". No explicit rate is folded in: a reported rate is measured
        # against its own denominator, and pooling across denominators is what
        # the optimizer refuses.
        pooled = self._resolve_rate(
            {
                "clicks": total_clicks,
                "impressions": total_impressions,
                "_explicit_ctr": None,
            }
        )

        return {
            "period": {"start_date": start_date, "end_date": end_date},
            "content_metrics": {
                "total_content_generated": len(generation_events),
                "avg_quality_score": _round_or_none(avg_quality_score, 2),
                "avg_title_length": _round_or_none(avg_title_length, 1),
                "avg_tag_count": _round_or_none(avg_tag_count, 1),
            },
            "performance_metrics": {
                "total_views": total_views,
                "total_clicks": total_clicks,
                "total_impressions": total_impressions,
                "avg_ctr": pooled["ctr"],
                "avg_ctr_basis": pooled["ctr_basis"],
                # The population `top_performers` is sliced from, and the cap
                # that slice applied -- so a reader of the ten rows below can
                # say "ten of N" instead of just "ten".
                "total_content_with_performance": len(
                    self._aggregate(performance_events)
                ),
                "top_performers_cap": TOP_PERFORMERS_CAP,
            },
            "top_performers": self._get_top_performers(performance_events),
        }

    def get_content_performance(self, content_id: str) -> dict:
        """Performance for one piece of content, on the same rate contract as
        ``get_summary``'s top-performers list.

        Both readers aggregate the same PERFORMANCE_UPDATED events, so both go
        through ``_aggregate`` and ``_resolve_rate``. This one used to
        compute ``clicks / impressions if impressions else 0`` and dropped
        ``ctr_source`` entirely, so the two readers disagreed about the same
        video: the list said "unmeasured", this said "0%, source unstated".
        A 0 is a measurement -- it says every viewer shown this declined to
        click -- and "we have no impressions for it" is not that.

        The absent case carries the rate keys too, with the same names and the
        same ``None``s. A screen has to render "we have never heard anything
        about this" without a KeyError.

        Args:
            content_id: Content identifier

        Returns:
            Dict with performance data
        """
        events = self.store.get_by_content_id(
            site_id=self.site_id, content_id=content_id
        )

        performance_events = [
            e for e in events if e.event_type == EventType.PERFORMANCE_UPDATED
        ]

        aggregate = self._resolve_rate(
            self._aggregate(performance_events).get(content_id)
            or self._blank_aggregate(content_id)
        )
        read = {
            "content_id": content_id,
            "total_views": aggregate["views"],
            "total_clicks": aggregate["clicks"],
            "total_impressions": aggregate["impressions"],
            "ctr": aggregate["ctr"],
            "ctr_source": aggregate["ctr_source"],
            "ctr_basis": aggregate["ctr_basis"],
            "first_updated": aggregate["first_updated"],
            "last_updated": aggregate["last_updated"],
            "data_points": aggregate["data_points"],
        }
        if not performance_events:
            read["error"] = "No performance data available"
        return read

    @staticmethod
    def _mean_of_reported(events: list[SEOEvent], key: str) -> float | None:
        """Mean of ``key`` over the events that reported it, else None.

        None is "nobody told us", which is not the measurement 0 -- the same
        distinction ``_resolve_rate`` makes for an unmeasured rate."""
        reported = [e.data.get(key) for e in events if e.data.get(key) is not None]
        if not reported:
            return None
        return sum(reported) / len(reported)

    @staticmethod
    def _blank_aggregate(content_id: str) -> dict:
        """A content with nothing measured yet. Every reader starts here, so
        "no events at all" and "events carrying no rate" come out the same
        shape -- the caller never has to tell them apart by a missing key."""
        return {
            "content_id": content_id,
            "views": 0,
            "clicks": 0,
            "impressions": 0,
            # How many performance updates this aggregate is made of. A rate
            # from one sample and a rate from fifty are both "the rate"; only
            # this says which you are looking at.
            "data_points": 0,
            # Most-recent performance-update timestamp. The optimizer
            # age-decays each content's contribution against this so a
            # stale sample (whose title may have since been rewritten)
            # fades out — see the title-correspondence note in
            # seo_optimizer.analyze_patterns.
            "last_updated": None,
            # The other end of that window. `last_updated` says when we
            # last heard about this content; without the oldest sample nothing
            # says how long we had been hearing it, so 900 views over two
            # years and 900 in a week are the same row. `data_points` counts
            # samples, not elapsed time. Taken from the same timestamps, never
            # derived, and `None` when no sample carried one.
            "first_updated": None,
            # Last explicitly-supplied ctr (e.g. the cardClickRate
            # proxy from youtube_sync), used when there are no
            # impression counts to recompute from.
            "_explicit_ctr": None,
            # Which denominator that rate was measured against, so a
            # consumer can tell card-overlay clicks from thumbnail
            # click-through. An event declaring nothing reads as
            # UNKNOWN, never as thumbnail data (see metric_source).
            "ctr_source": metric_source.UNKNOWN,
        }

    @classmethod
    def _aggregate(cls, performance_events: list[SEOEvent]) -> dict[str, dict]:
        """Roll the events up per content_id, rate unresolved.

        Shared by both readers of these events rather than written twice
        -- when the two copies disagreed, the same video reported a different
        click rate depending on which screen asked."""
        by_content: dict[str, dict] = {}

        for event in performance_events:
            cp = by_content.setdefault(
                event.content_id, cls._blank_aggregate(event.content_id)
            )
            cp["views"] += event.data.get("views") or 0
            cp["clicks"] += event.data.get("clicks") or 0
            cp["impressions"] += event.data.get("impressions") or 0
            cp["data_points"] += 1
            if event.data.get("ctr") is not None:
                cp["_explicit_ctr"] = event.data["ctr"]
                cp["ctr_source"] = metric_source.normalize(event.data.get("ctr_source"))
            ts = event.timestamp
            if ts and (cp["last_updated"] is None or ts > cp["last_updated"]):
                cp["last_updated"] = ts
            # Compared, not assumed from read order: events reach this loop in
            # store order, which is neither sorted nor guaranteed stable.
            if ts and (cp["first_updated"] is None or ts < cp["first_updated"]):
                cp["first_updated"] = ts

        return by_content

    @staticmethod
    def _resolve_rate(aggregate: dict) -> dict:
        """Settle one aggregate's ``ctr``, and say what it is a ratio OF.

        Prefer the aggregated clicks/impressions ratio; else the last
        explicitly-supplied rate; else None. None means unmeasured — never a
        0, which reads as the real measurement "nobody clicked".

        ``ctr_basis`` is the denominator, which ``ctr_source`` does not give:
        a rate recomputed from counts we hold and a rate a platform handed us
        are different quantities, and a column showing them together without
        saying which is the pooling this service already refuses across metric
        sources. It rounds to 4 dp, the precision this reader has always
        used, so the two readers agree to the digit rather than approximately.
        """
        if aggregate["impressions"]:
            aggregate["ctr"] = round(aggregate["clicks"] / aggregate["impressions"], 4)
            aggregate["ctr_basis"] = CTR_BASIS_RECOMPUTED
        elif aggregate["_explicit_ctr"] is not None:
            aggregate["ctr"] = aggregate["_explicit_ctr"]
            aggregate["ctr_basis"] = CTR_BASIS_REPORTED
        else:
            aggregate["ctr"] = None
            aggregate["ctr_basis"] = None
        aggregate.pop("_explicit_ctr", None)
        return aggregate

    def _get_top_performers(self, performance_events: list[SEOEvent]) -> list[dict]:
        """
        Get top ``TOP_PERFORMERS_CAP`` content by views.

        Args:
            performance_events: List of performance update events

        Returns:
            List of top performers with metrics
        """
        top_content = sorted(
            self._aggregate(performance_events).values(),
            key=lambda x: x["views"],
            reverse=True,
        )[:TOP_PERFORMERS_CAP]

        return [self._resolve_rate(content) for content in top_content]
