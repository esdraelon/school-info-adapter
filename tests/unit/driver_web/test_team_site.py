"""TeamSiteDriver: hub fetch -> resources, with run accounting."""

from collections import Counter

from school.domain import ResourceKind, RunOutcome, SourceKind
from school.driver_web import TeamSiteDriver
from school.spi import DriverRegistry, ScrapeContext, SourceDriver
from driver_web.conftest import (
    HUB_URL,
    NOW,
    SAM,
    failing_fetcher,
    fixture_text,
    make_fetcher,
)


def _driver(pages: dict[str, str]) -> TeamSiteDriver:
    return TeamSiteDriver(hub_url=HUB_URL, fetcher=make_fetcher(pages))


def _context() -> ScrapeContext:
    return ScrapeContext(student=SAM, now=NOW)


def test_driver_satisfies_the_protocol() -> None:
    driver = _driver({})
    assert isinstance(driver, SourceDriver)
    assert driver.kind is SourceKind.TEAM_SITE


def test_scrape_success_maps_hub_links_to_shared_resources() -> None:
    driver = _driver({HUB_URL: fixture_text("team_site_home.html")})
    result = driver.scrape(_context())
    run = result.run
    assert run.outcome is RunOutcome.SUCCESS
    assert run.source_kind is SourceKind.TEAM_SITE
    assert run.started_at == NOW
    assert run.finished_at == NOW
    # 14 hub links minus the hub's own self-link.
    assert run.found == 13
    assert len(result.resources) == 13
    assert result.drivers == []
    kinds = Counter(r.kind for r in result.resources)
    assert kinds == {
        ResourceKind.NOTES: 1,
        ResourceKind.TUTORIAL: 1,
        ResourceKind.DOCUMENT: 4,
        ResourceKind.LINK: 7,
    }
    for record in result.resources:
        assert record.student is None  # hub resources are shared
        assert record.course is None
        assert record.source_kind is SourceKind.TEAM_SITE
        assert record.scraped_at == NOW
    titles = {r.title for r in result.resources}
    assert "2026 - 2027 8th Grade Team Weekly Agenda" in titles
    assert "Google Doc" in titles  # unlabelled link -> fallback title
    assert "Sarah VanDyke" in titles


def test_scrape_fetch_failure_is_failure_not_exception() -> None:
    driver = TeamSiteDriver(hub_url=HUB_URL, fetcher=failing_fetcher)
    result = driver.scrape(_context())
    assert result.run.outcome is RunOutcome.FAILURE
    assert result.run.found == 0
    assert result.resources == []
    assert result.run.message is not None
    assert "cannot fetch" in result.run.message


def test_scrape_linkless_page_is_partial() -> None:
    driver = _driver({HUB_URL: "<html><body>no links here</body></html>"})
    result = driver.scrape(_context())
    assert result.run.outcome is RunOutcome.PARTIAL
    assert result.resources == []
    assert result.run.message is not None
    assert "no outbound links" in result.run.message


def test_driver_runs_through_the_registry() -> None:
    driver = _driver({HUB_URL: fixture_text("team_site_home.html")})
    registry = DriverRegistry({SourceKind.TEAM_SITE: driver})
    resolved = registry.driver_for(SourceKind.TEAM_SITE).unwrap()
    assert isinstance(resolved, TeamSiteDriver)
    assert resolved.scrape(_context()).run.outcome is RunOutcome.SUCCESS
