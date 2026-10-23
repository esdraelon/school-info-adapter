"""TeamSiteDriver: the 8th Grade Team Site hub as a SourceDriver.

Pattern: Strategy (SourceDriver), scrape as a two-stage composition:
(1) fetch the (public) hub page through the injected Fetcher and turn
its outbound links into ResourceRecords — the hub is a link directory,
so links *are* the data; (2) when the hub links a Tutorial Schedule
deck, fetch its embed form and parse the week's sessions into TUTORIAL
resources (see school.driver_web.tutorials). Stage 2 failing degrades
the run to PARTIAL — the hub resources still flow.

Hub resources are shared (``student`` is None) and course-less; the
hub's self-link is dropped. Run accounting mirrors the other drivers:
fetch failure -> FAILURE (returned, never raised); a fetched page
yielding zero links -> PARTIAL (the real hub always has links, so
zero means markup drift, not an empty source).
"""

from school.domain import (
    ResourceKind,
    ResourceRecord,
    RunOutcome,
    SourceKind,
    SourceRun,
)
from school.driver_web.fetching import FetchError, Fetcher
from school.driver_web.hub import HubLink, extract_hub_links, fallback_title
from school.driver_web.tutorials import parse_tutorials
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
        """Fetch the hub once; map its links (+ tutorial sessions)."""
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
        notes: list[str] = []
        if not resources:
            notes.append(
                f"hub page at {self._hub_url} yielded no outbound "
                f"links (markup drift?)"
            )
        sessions, tutorial_notes = self._tutorial_sessions(links, context)
        resources.extend(sessions)
        notes.extend(tutorial_notes)
        return self._result(
            context,
            outcome=RunOutcome.PARTIAL if notes else RunOutcome.SUCCESS,
            found=len(resources),
            message="; ".join(notes) if notes else None,
            resources=resources,
        )

    def _tutorial_sessions(
        self, links: list[HubLink], context: ScrapeContext
    ) -> tuple[list[ResourceRecord], list[str]]:
        """Stage 2: parse the hub's Tutorial Schedule deck, if linked.

        Any failure is a note (the caller degrades to PARTIAL); the
        sessions are simply absent.
        """
        deck = next(
            (
                link
                for link in links
                if link.kind is ResourceKind.TUTORIAL
                and "presentation" in link.url
            ),
            None,
        )
        if deck is None:
            return [], []
        embed_url = deck.url
        if embed_url.endswith("/present"):
            embed_url = embed_url[: -len("/present")] + "/embed"
        try:
            page = self._fetcher(embed_url)
        except FetchError as exc:
            return [], [f"tutorial schedule fetch failed: {exc}"]
        sessions, notes = parse_tutorials(
            page,
            deck_url=embed_url,
            scraped_at=context.now,
            reference=context.now.date(),
        )
        return list(sessions), list(notes)

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
