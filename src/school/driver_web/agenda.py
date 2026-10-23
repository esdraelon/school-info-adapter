"""AgendaDriver: the per-subject agenda decks as a SourceDriver.

Pattern: Strategy (SourceDriver) with Registry-selected deck parsers
(C2). Configuration is constructor-injected: a Fetcher plus the deck
list (subject, fetch URL, template id) — in production built from the
discovery registry (see tests' fixtures/deck_registry.json) by the
composition root.

Run accounting, mirroring the other drivers but per deck:
- every deck fetches and parses -> SUCCESS
- some decks fail while others produce -> PARTIAL (good decks' records
  still flow; the message names each failure)
- every deck fails (or none are configured) -> FAILURE
Failures are returned in the run, never raised.

Resources are deduped across the run by (url, title, course): decks
repeat the same workbook/notes links week after week, and one scrape
should report each distinct resource once.
"""

from dataclasses import dataclass

from school.domain import (
    CourseRef,
    DriverRecord,
    Found,
    ResourceRecord,
    RunOutcome,
    SourceKind,
    SourceRun,
)
from school.driver_web.fetching import FetchError, Fetcher
from school.driver_web.grid_parser import WeeklyGridParser
from school.driver_web.parsers import DeckParserRegistry
from school.driver_web.slides import extract_slide_alts
from school.driver_web.table_parser import TeamTableParser
from school.spi import ScrapeContext, ScrapeResult

WEEKLY_GRID_TEMPLATE = "weekly-grid-v1"
TEAM_TABLE_TEMPLATE = "team-table-v1"


@dataclass(frozen=True, kw_only=True)
class DeckConfig:
    """One deck to scrape: display subject, fetch URL, template id."""

    subject: str
    url: str
    template_id: str


def default_parser_registry() -> DeckParserRegistry:
    """The registry with every production deck parser registered."""
    return DeckParserRegistry(
        {
            WEEKLY_GRID_TEMPLATE: WeeklyGridParser(),
            TEAM_TABLE_TEMPLATE: TeamTableParser(),
        }
    )


class AgendaDriver:
    """SourceDriver strategy over the configured agenda decks."""

    def __init__(
        self,
        *,
        fetcher: Fetcher,
        decks: tuple[DeckConfig, ...],
        parsers: DeckParserRegistry,
    ) -> None:
        self._fetcher = fetcher
        self._decks = decks
        self._parsers = parsers

    @property
    def kind(self) -> SourceKind:
        return SourceKind.AGENDA

    def scrape(self, context: ScrapeContext) -> ScrapeResult:
        """Scrape every configured deck once, accounting per deck."""
        if not self._decks:
            return self._result(
                context,
                outcome=RunOutcome.FAILURE,
                message="no decks configured",
            )
        drivers: list[DriverRecord] = []
        resources: list[ResourceRecord] = []
        seen_resources: set[tuple[str, str, str]] = set()
        notes: list[str] = []
        decks_ok = 0
        decks_failed = 0
        for deck in self._decks:
            outcome = self._scrape_deck(
                deck, context, drivers, resources, seen_resources
            )
            if isinstance(outcome, str):
                decks_failed += 1
                notes.append(f"{deck.subject}: {outcome}")
            else:
                decks_ok += 1
                notes.extend(
                    f"{deck.subject}: {note}" for note in outcome
                )
        if decks_failed == 0:
            run_outcome = RunOutcome.SUCCESS
        elif decks_ok > 0:
            run_outcome = RunOutcome.PARTIAL
        else:
            run_outcome = RunOutcome.FAILURE
        return self._result(
            context,
            outcome=run_outcome,
            found=len(drivers) + len(resources),
            message="; ".join(notes) if notes else None,
            drivers=drivers,
            resources=resources,
        )

    def _scrape_deck(
        self,
        deck: DeckConfig,
        context: ScrapeContext,
        drivers: list[DriverRecord],
        resources: list[ResourceRecord],
        seen_resources: set[tuple[str, str, str]],
    ) -> list[str] | str:
        """Scrape one deck into the accumulators.

        Returns the deck's notes list on success, or a failure string.
        """
        match self._parsers.parser_for(deck.template_id):
            case Found(value=parser):
                pass
            case _:
                return f"unknown template {deck.template_id!r}"
        try:
            page = self._fetcher(deck.url)
        except FetchError as exc:
            return str(exc)
        slides = extract_slide_alts(page)
        if not slides:
            return "no slides extracted from the deck page"
        notes: list[str] = []
        parsed_any = False
        for slide in slides:
            records = parser.parse_slide(
                slide,
                course=CourseRef(name=deck.subject),
                student=context.student,
                source_kind=SourceKind.AGENDA,
                source_url=deck.url,
                scraped_at=context.now,
                reference=context.now.date(),
            )
            notes.extend(records.notes)
            if records.week_monday is None:
                continue
            parsed_any = True
            drivers.extend(records.drivers)
            for record in records.resources:
                key = (
                    record.url,
                    record.title,
                    record.course.name if record.course else "",
                )
                if key not in seen_resources:
                    seen_resources.add(key)
                    resources.append(record)
        if not parsed_any:
            return "no parseable week in the deck"
        return notes

    def _result(
        self,
        context: ScrapeContext,
        *,
        outcome: RunOutcome,
        found: int = 0,
        message: str | None = None,
        drivers: list[DriverRecord] | None = None,
        resources: list[ResourceRecord] | None = None,
    ) -> ScrapeResult:
        run = SourceRun(
            source_kind=SourceKind.AGENDA,
            started_at=context.now,
            finished_at=context.now,
            outcome=outcome,
            found=found,
            message=message,
        )
        return ScrapeResult(
            run=run, drivers=drivers or [], resources=resources or []
        )

