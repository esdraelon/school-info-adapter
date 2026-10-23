"""Shared fixtures for the web driver tests.

Everything runs on the captured fixtures in ./fixtures (see
fixtures/CAPTURE.md for provenance) — the network is never touched:
fetchers are dict-backed fakes raising FetchError for unmapped URLs.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from school.domain import StudentRef
from school.driver_web.fetching import FetchError, Fetcher

FIXTURES = Path(__file__).parent / "fixtures"

SAM = StudentRef(id="sam", display_name="Sam")
NOW = datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)

HUB_URL = "https://sites.google.com/leanderisd.org/8th-grade-team-site/home"
MATH_DECK_URL = (
    "https://docs.google.com/presentation/d/e/"
    "2PACX-1vTNlVp2y1IQY8QJE4-7I1zFgDIqVPj1y8gmj3Lilr9jqjlrMWQ-"
    "r58TmlkxdrjdPw0ffzrfbICyG2Dx/pub"
)
ALGEBRA_DECK_URL = (
    "https://docs.google.com/presentation/d/e/"
    "2PACX-1vTkEKmZWu94U5AMGkPLIIQxGjgPSKGrlE-bVqV6GoRWgMuJiuUrydfmUN_"
    "ENo59fgn_pcGRrSpAyNyk/pub"
)
TEAM_AGENDA_URL = (
    "https://docs.google.com/presentation/d/"
    "1kwfNVKRRRI6_KFrVQ84WWu_fhvEzyacgoHExKfbPp_4/embed"
)
TUTORIAL_URL = (
    "https://docs.google.com/presentation/d/"
    "1U__2wWHfuv-_61KnL3b05FXNgEL_S5atP3q7XrPipp8/present"
)
TUTORIAL_EMBED_URL = (
    "https://docs.google.com/presentation/d/"
    "1U__2wWHfuv-_61KnL3b05FXNgEL_S5atP3q7XrPipp8/embed"
)


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_json(name: str) -> dict[str, object]:
    data: dict[str, object] = json.loads(fixture_text(name))
    return data


def make_fetcher(pages: dict[str, str]) -> Fetcher:
    """A Fetcher serving ``pages`` by exact URL; FetchError otherwise."""

    def fetch(url: str) -> str:
        try:
            return pages[url]
        except KeyError:
            raise FetchError(f"no fixture page for {url}") from None

    return fetch


def failing_fetcher(url: str) -> str:
    raise FetchError(f"cannot fetch {url}: boom")
