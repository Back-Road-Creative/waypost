"""Opportunity rules: pure functions from observations to reviewable findings."""

from seo_manager.opportunities.contracts import EvidenceRef, Opportunity, VideoText
from seo_manager.opportunities.search_console import (
    SearchConsolePolicy,
    find_search_console_opportunities,
)
from seo_manager.opportunities.site_technical import (
    SiteTechnicalPolicy,
    find_site_technical_opportunities,
)
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
    "SearchConsolePolicy",
    "SearchDemandPolicy",
    "SiteTechnicalPolicy",
    "VideoText",
    "find_search_console_opportunities",
    "find_search_demand_opportunities",
    "find_site_technical_opportunities",
]
