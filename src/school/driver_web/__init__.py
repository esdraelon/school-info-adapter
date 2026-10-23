"""Public-web source drivers: team-site hub crawler, agenda deck alt-text extractor, weekly-grid parser, tutorial-schedule parser.

Patterns: Adapter; Strategy (per-deck-template parsers, registry-selected);
pipeline stages as lambdas.
"""

from school.driver_web.agenda import (
    TEAM_TABLE_TEMPLATE,
    WEEKLY_GRID_TEMPLATE,
    AgendaDriver,
    DeckConfig,
    default_parser_registry,
)
from school.driver_web.fetching import (
    FetchError,
    Fetcher,
    fetch_url,
    urllib_fetcher,
)
from school.driver_web.grid_parser import WeeklyGridParser
from school.driver_web.hub import HubLink, extract_hub_links
from school.driver_web.parsers import DeckParserRegistry, SlideRecords
from school.driver_web.slides import SlideAlt, extract_slide_alts
from school.driver_web.table_parser import TeamTableParser
from school.driver_web.team_site import TeamSiteDriver
from school.driver_web.tutorials import parse_tutorials

__all__ = [
    "AgendaDriver",
    "DeckConfig",
    "DeckParserRegistry",
    "FetchError",
    "Fetcher",
    "HubLink",
    "SlideAlt",
    "SlideRecords",
    "TEAM_TABLE_TEMPLATE",
    "TeamSiteDriver",
    "TeamTableParser",
    "WEEKLY_GRID_TEMPLATE",
    "WeeklyGridParser",
    "default_parser_registry",
    "extract_hub_links",
    "extract_slide_alts",
    "fetch_url",
    "parse_tutorials",
    "urllib_fetcher",
]
