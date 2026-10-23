"""AgendaDriver: per-deck run accounting + registry-driven parsing."""

from school.domain import DriverKind, RunOutcome, SourceKind
from school.driver_web import (
    AgendaDriver,
    DeckConfig,
    DeckParserRegistry,
    default_parser_registry,
)
from school.driver_web.parsers import SlideRecords
from school.driver_web.slides import SlideAlt
from school.spi import DriverRegistry, ScrapeContext, SourceDriver
from driver_web.conftest import (
    ALGEBRA_DECK_URL,
    MATH_DECK_URL,
    NOW,
    SAM,
    TEAM_AGENDA_URL,
    failing_fetcher,
    fixture_text,
    make_fetcher,
    make_page,
)

MATH = DeckConfig(
    subject="Math 8", url=MATH_DECK_URL, template_id="weekly-grid-v1"
)
TEAM = DeckConfig(
    subject="Team Agenda", url=TEAM_AGENDA_URL, template_id="team-table-v1"
)


def _context() -> ScrapeContext:
    return ScrapeContext(student=SAM, now=NOW)


def _driver(pages: dict[str, str], decks=(MATH,)) -> AgendaDriver:
    return AgendaDriver(
        fetcher=make_fetcher(pages),
        decks=decks,
        parsers=default_parser_registry(),
    )


def test_driver_satisfies_the_protocol() -> None:
    driver = _driver({})
    assert isinstance(driver, SourceDriver)
    assert driver.kind is SourceKind.AGENDA


def test_math_deck_full_run() -> None:
    driver = _driver({MATH_DECK_URL: fixture_text("math_agenda_pub.html")})
    result = driver.scrape(_context())
    run = result.run
    assert run.outcome is RunOutcome.SUCCESS
    assert run.source_kind is SourceKind.AGENDA
    # 11 parseable weeks (the 12th slide is a blank template; its skip
    # note rides in the message even on success).
    assert len(result.drivers) == 84
    quiz = next(
        d for d in result.drivers if d.title == "Quiz: Pythagorean Theorem"
    )
    assert quiz.kind is DriverKind.QUIZ
    assert run.found == len(result.drivers) + len(result.resources)
    assert run.message is not None and "skipped" in run.message


def test_combined_run_dedupes_resources_across_decks() -> None:
    pages = {
        MATH_DECK_URL: fixture_text("math_agenda_pub.html"),
        ALGEBRA_DECK_URL: fixture_text("algebra_agenda_pub.html"),
        TEAM_AGENDA_URL: fixture_text("team_agenda_embed.html"),
    }
    algebra = DeckConfig(
        subject="Algebra I",
        url=ALGEBRA_DECK_URL,
        template_id="weekly-grid-v1",
    )
    driver = _driver(pages, decks=(MATH, algebra, TEAM))
    result = driver.scrape(_context())
    assert result.run.outcome is RunOutcome.SUCCESS
    keys = [
        (r.url, r.title, r.course.name if r.course else "")
        for r in result.resources
    ]
    assert len(keys) == len(set(keys))  # deduped across the whole run
    courses = {d.course.name for d in result.drivers}
    assert {"Math 8", "Algebra I", "ELA", "Science"} <= courses


def test_partial_when_one_deck_fails() -> None:
    pages = {MATH_DECK_URL: fixture_text("math_agenda_pub.html")}
    driver = _driver(pages, decks=(MATH, TEAM))  # TEAM not in pages
    result = driver.scrape(_context())
    assert result.run.outcome is RunOutcome.PARTIAL
    assert len(result.drivers) == 84  # good deck's records still flow
    assert result.run.message is not None
    assert "Team Agenda" in result.run.message


def test_all_decks_failing_is_failure() -> None:
    driver = AgendaDriver(
        fetcher=failing_fetcher,
        decks=(MATH,),
        parsers=default_parser_registry(),
    )
    result = driver.scrape(_context())
    assert result.run.outcome is RunOutcome.FAILURE
    assert result.drivers == []
    assert result.run.found == 0


def test_no_decks_configured_is_failure() -> None:
    driver = _driver({}, decks=())
    result = driver.scrape(_context())
    assert result.run.outcome is RunOutcome.FAILURE
    assert result.run.message is not None
    assert "no decks configured" in result.run.message


def test_unknown_template_fails_that_deck() -> None:
    bogus = DeckConfig(
        subject="Bogus", url=MATH_DECK_URL, template_id="nope-v9"
    )
    driver = _driver(
        {MATH_DECK_URL: fixture_text("math_agenda_pub.html")},
        decks=(bogus,),
    )
    result = driver.scrape(_context())
    assert result.run.outcome is RunOutcome.FAILURE
    assert result.run.message is not None
    assert "unknown template" in result.run.message


def test_second_parser_injected_without_code_change() -> None:
    """C2 proof: a new template id + stub parser, selected by config."""

    class StubParser:
        def parse_slide(self, slide: SlideAlt, **kwargs: object) \
                -> SlideRecords:
            return SlideRecords(week_monday=kwargs["reference"])  # type: ignore[arg-type]

    registry = DeckParserRegistry({"stub-v0": StubParser()})
    deck = DeckConfig(
        subject="Stub", url=MATH_DECK_URL, template_id="stub-v0"
    )
    driver = AgendaDriver(
        fetcher=make_fetcher(
            {MATH_DECK_URL: fixture_text("math_agenda_pub.html")}
        ),
        decks=(deck,),
        parsers=registry,
    )
    result = driver.scrape(_context())
    assert result.run.outcome is RunOutcome.SUCCESS
    assert result.drivers == []


def test_deck_page_without_slides_fails_the_deck() -> None:
    driver = _driver({MATH_DECK_URL: "<html><body>not a deck</body></html>"})
    result = driver.scrape(_context())
    assert result.run.outcome is RunOutcome.FAILURE
    assert result.run.message is not None
    assert "no slides extracted" in result.run.message


def test_deck_with_only_a_blank_slide_fails_the_deck() -> None:
    driver = _driver({MATH_DECK_URL: make_page("<svg></svg>")})
    result = driver.scrape(_context())
    assert result.run.outcome is RunOutcome.FAILURE
    assert result.run.message is not None
    assert "no parseable week" in result.run.message


def test_driver_runs_through_the_registry() -> None:
    driver = _driver({MATH_DECK_URL: fixture_text("math_agenda_pub.html")})
    registry = DriverRegistry({SourceKind.AGENDA: driver})
    resolved = registry.driver_for(SourceKind.AGENDA).unwrap()
    assert isinstance(resolved, AgendaDriver)
    assert resolved.scrape(_context()).run.outcome is RunOutcome.SUCCESS
