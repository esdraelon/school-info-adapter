"""AgendaDriver: per-deck run accounting + registry-driven parsing."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfoNotFoundError

import pytest

from school.domain import DriverKind, RunOutcome, SourceKind
from school.driver_web import (
    AgendaDriver,
    DeckConfig,
    DeckParserRegistry,
    WeeklyGridParser,
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


def test_parser_exception_fails_only_that_deck() -> None:
    """M7 FIX 1: a parser raising on one deck is contained — the deck
    becomes a failure note naming the exception class, the run is
    PARTIAL, and the good deck's records still flow."""

    class ExplodingParser:
        def parse_slide(self, slide: SlideAlt, **kwargs: object) \
                -> SlideRecords:
            raise RuntimeError("kaboom: pathological slide")

    registry = DeckParserRegistry(
        {
            "weekly-grid-v1": WeeklyGridParser(),
            "explodes-v0": ExplodingParser(),
        }
    )
    bad = DeckConfig(
        subject="Exploding",
        url=ALGEBRA_DECK_URL,
        template_id="explodes-v0",
    )
    pages = {
        ALGEBRA_DECK_URL: fixture_text("algebra_agenda_pub.html"),
        MATH_DECK_URL: fixture_text("math_agenda_pub.html"),
    }
    driver = AgendaDriver(
        fetcher=make_fetcher(pages), decks=(bad, MATH), parsers=registry
    )
    result = driver.scrape(_context())
    assert result.run.outcome is RunOutcome.PARTIAL
    # The good deck's records are all here; nothing leaked from the
    # exploding deck.
    assert len(result.drivers) == 84
    assert {d.course.name for d in result.drivers} == {"Math 8"}
    message = result.run.message
    assert message is not None
    assert "Exploding" in message
    assert "RuntimeError" in message


def test_driver_runs_through_the_registry() -> None:
    driver = _driver({MATH_DECK_URL: fixture_text("math_agenda_pub.html")})
    registry = DriverRegistry({SourceKind.AGENDA: driver})
    resolved = registry.driver_for(SourceKind.AGENDA).unwrap()
    assert isinstance(resolved, AgendaDriver)
    assert resolved.scrape(_context()).run.outcome is RunOutcome.SUCCESS


# A one-week synthetic deck whose Monday cell carries an "FD 1/1"
# item: 1/1 sits almost exactly at its year midpoint in early July,
# so which 1/1 the parser infers turns on the reference DATE — the
# day-boundary probe for the timezone tests below.
_BOUNDARY_SLIDE = (
    "<svg>"
    '<g role="img" aria-label="Monday 6/29">'
    '<path d="m 40 40l0 20l 120 0l0 -20z"/></g>'
    '<g role="img" aria-label="Tuesday 6/30">'
    '<path d="m 240 40l0 20l 120 0l0 -20z"/></g>'
    '<g role="img" aria-label="Wednesday 7/1">'
    '<path d="m 440 40l0 20l 120 0l0 -20z"/></g>'
    '<g role="img" aria-label="Thursday 7/2">'
    '<path d="m 640 40l0 20l 120 0l0 -20z"/></g>'
    '<g role="img" aria-label="Friday 7/3">'
    '<path d="m 840 40l0 20l 120 0l0 -20z"/></g>'
    '<g role="img" aria-label="Year Midpoint Quiz&#xa;FD 1/1">'
    '<path d="m 40 100l0 30l 120 0l0 -30z"/></g>'
    "</svg>"
)

# 2026-07-03T01:30Z is 20:30 CDT on 7/2 — the evening boundary.
_EVENING = datetime(2026, 7, 3, 1, 30, tzinfo=timezone.utc)


def _boundary_driver(
    timezone_name: str = "America/Chicago",
) -> AgendaDriver:
    return AgendaDriver(
        fetcher=make_fetcher({MATH_DECK_URL: make_page(_BOUNDARY_SLIDE)}),
        decks=(MATH,),
        parsers=default_parser_registry(),
        timezone_name=timezone_name,
    )


def test_reference_date_uses_the_school_timezone() -> None:
    """M7.1 FIX B: against the school reference date (7/2) the FD
    1/1 cell infers 2026-01-01 (182 days back beats 183 forward);
    a UTC reference date (7/3) would flip it to 2027-01-01."""
    driver = _boundary_driver()
    result = driver.scrape(ScrapeContext(student=SAM, now=_EVENING))
    (quiz,) = result.drivers
    assert quiz.title == "Year Midpoint Quiz"
    assert quiz.due_at == datetime(2026, 1, 1)


def test_explicit_utc_timezone_keeps_the_utc_reference_date() -> None:
    """The instant is explicit: with timezone_name="UTC" the same
    evening scrape derives against 7/3 and the FD flips a year."""
    driver = _boundary_driver(timezone_name="UTC")
    result = driver.scrape(ScrapeContext(student=SAM, now=_EVENING))
    (quiz,) = result.drivers
    assert quiz.due_at == datetime(2027, 1, 1)


def test_unknown_timezone_fails_at_construction() -> None:
    with pytest.raises(ZoneInfoNotFoundError):
        _boundary_driver(timezone_name="Mars/Olympus_Mons")
