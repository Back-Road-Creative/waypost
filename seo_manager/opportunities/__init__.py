"""Opportunity rules: pure functions from observations to reviewable findings."""

from seo_manager.opportunities.contracts import EvidenceRef, Opportunity, VideoText
from seo_manager.opportunities.youtube_search import (
    DEFAULT_POLICY,
    RULE,
    SearchDemandPolicy,
    find_search_demand_opportunities,
)

__all__ = [
    "DEFAULT_POLICY",
    "RULE",
    "EvidenceRef",
    "Opportunity",
    "SearchDemandPolicy",
    "VideoText",
    "find_search_demand_opportunities",
]
