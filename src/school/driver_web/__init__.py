"""Public-web source drivers: team-site hub crawler, agenda deck alt-text extractor, weekly-grid parser, tutorial-schedule parser.

Patterns: Adapter; Strategy (per-deck-template parsers, registry-selected);
pipeline stages as lambdas.
"""

from school.driver_web.fetching import (
    FetchError,
    Fetcher,
    fetch_url,
    urllib_fetcher,
)
from school.driver_web.hub import HubLink, extract_hub_links
from school.driver_web.team_site import TeamSiteDriver

__all__ = [
    "FetchError",
    "Fetcher",
    "HubLink",
    "TeamSiteDriver",
    "extract_hub_links",
    "fetch_url",
    "urllib_fetcher",
]
