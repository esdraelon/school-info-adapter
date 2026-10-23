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
Failures are returned in the run, never raised. That includes parser
exceptions (M7 hardening): a parser blowing up on pathological future
HTML fails only its own deck — the parse runs into deck-local buffers
and merges into the run only on success, so a deck contributes whole
or not at all, and the failure note names the exception class.

Resources are deduped across the run by (url, title, course): decks
repeat the same workbook/notes links week after week, and one scrape
should report each distinct resource once.

Dates in the decks are school-local, so the reference date handed to
the parsers is the scrape instant read in the configured school
timezone (``SCHOOL_TIMEZONE`` via the composition root), NOT its UTC
date — an evening scrape must not resolve the decks' dates against
tomorrow (M7.1, mirroring the HAC driver's M7 fix). The parsers
themselves take plain dates and are timezone-agnostic.
"""

from dataclasses import dataclass
from zoneinfo import ZoneInfo

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

#: The school's timezone when none is configured: deck dates are
#: Leander ISD local dates (mirrors the HAC driver's default).
DEFAULT_TIMEZONE_NAME = "America/Chicago"


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
        timezone_name: str = DEFAULT_TIMEZONE_NAME,
    ) -> None:
        self._fetcher = fetcher
        self._decks = decks
        self._parsers = parsers
        # Resolved eagerly: an unknown timezone name fails at wiring
        # time (ZoneInfoNotFoundError), never mid-scrape.
        self._timezone = ZoneInfo(timezone_name)

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
        # Parse into deck-local buffers, merged into the run only when
        # the whole deck parses: a parser exception part-way through a
        # deck must not leak half a deck into the run (see module doc).
        deck_drivers: list[DriverRecord] = []
        deck_resources: list[ResourceRecord] = []
        deck_seen: set[tuple[str, str, str]] = set()
        notes: list[str] = []
        parsed_any = False
        try:
            slides = extract_slide_alts(page)
            if not slides:
                return "no slides extracted from the deck page"
            for slide in slides:
                records = parser.parse_slide(
                    slide,
                    course=CourseRef(name=deck.subject),
                    student=context.student,
                    source_kind=SourceKind.AGENDA,
                    source_url=deck.url,
                    scraped_at=context.now,
                    reference=context.now.astimezone(
                        self._timezone
                    ).date(),
                )
                notes.extend(records.notes)
                if records.week_monday is None:
                    continue
                parsed_any = True
                deck_drivers.extend(records.drivers)
                for record in records.resources:
                    key = (
                        record.url,
                        record.title,
                        record.course.name if record.course else "",
                    )
                    if key not in seen_resources and key not in deck_seen:
                        deck_seen.add(key)
                        deck_resources.append(record)
        except Exception as exc:  # noqa: BLE001 - containment is the point
            return f"deck parse raised {type(exc).__name__}: {exc}"
        if not parsed_any:
            return "no parseable week in the deck"
        drivers.extend(deck_drivers)
        resources.extend(deck_resources)
        seen_resources |= deck_seen
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

