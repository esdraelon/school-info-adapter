"""TeamSiteDriver: the 8th Grade Team Site hub as a SourceDriver.

Pattern: Strategy (SourceDriver). The scrape fetches the (public) hub
page through the injected Fetcher and turns its outbound links into
ResourceRecords — the hub is a link directory, so links *are* the data:
the agenda/tutorial decks it points at, the team Doc/Sheet/Drive files,
and the individual teacher sites.

Resources are shared (``student`` is None) and course-less. The hub's
self-link is dropped. Run accounting mirrors the other drivers: fetch
failure -> FAILURE (returned, never raised); a fetched page yielding
zero links -> PARTIAL (the real hub always has links, so zero means
markup drift, not an empty source).
"""

from school.domain import (
    ResourceRecord,
    RunOutcome,
    SourceKind,
    SourceRun,
)
from school.driver_web.fetching import FetchError, Fetcher
from school.driver_web.hub import extract_hub_links, fallback_title
from school.spi import ScrapeContext, ScrapeResult


class TeamSiteDriver:
    """SourceDriver strategy over the team-site hub page."""

    def __init__(self, *, hub_url: str, fetcher: Fetcher) -> None:
        self._hub_url = hub_url
        self._fetcher = fetcher

    @property
    def kind(self) -> SourceKind:
        return SourceKind.TEAM_SITE

    def scrape(self, context: ScrapeContext) -> ScrapeResult:
        """Fetch the hub once and map its links to resources."""
        try:
            html = self._fetcher(self._hub_url)
        except FetchError as exc:
            return self._result(
                context, outcome=RunOutcome.FAILURE, message=str(exc)
            )
        links = [
            link
            for link in extract_hub_links(html)
            if link.url.rstrip("/") != self._hub_url.rstrip("/")
        ]
        resources = [
            ResourceRecord(
                title=link.label or fallback_title(link.url),
                kind=link.kind,
                url=link.url,
                source_kind=SourceKind.TEAM_SITE,
                scraped_at=context.now,
            )
            for link in links
        ]
        if not resources:
            return self._result(
                context,
                outcome=RunOutcome.PARTIAL,
                message=(
                    f"hub page at {self._hub_url} yielded no outbound "
                    f"links (markup drift?)"
                ),
            )
        return self._result(
            context,
            outcome=RunOutcome.SUCCESS,
            found=len(resources),
            resources=resources,
        )

    def _result(
        self,
        context: ScrapeContext,
        *,
        outcome: RunOutcome,
        found: int = 0,
        message: str | None = None,
        resources: list[ResourceRecord] | None = None,
    ) -> ScrapeResult:
        run = SourceRun(
            source_kind=SourceKind.TEAM_SITE,
            started_at=context.now,
            finished_at=context.now,
            outcome=outcome,
            found=found,
            message=message,
        )
        return ScrapeResult(run=run, resources=resources or [])
