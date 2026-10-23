"""Deck parser contract + registry. Patterns: Strategy + Registry (C2).

A deck template (weekly grid, team table, ...) is a strategy
implementing ``DeckSlideParser``: one slide's accessibility layer in,
typed records out. ``DeckParserRegistry`` maps template id -> parser;
the AgendaDriver selects through it from deck config, so adding or
swapping a template is a wiring change, never a logic change.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol, runtime_checkable

from school.domain import (
    CourseRef,
    DriverKind,
    DriverRecord,
    Found,
    Lookup,
    Missing,
    ResourceRecord,
    SourceKind,
    StudentRef,
)
from school.driver_web.slides import SlideAlt


@dataclass(frozen=True, kw_only=True)
class SlideRecords:
    """One slide's parse output.

    ``week_monday`` is the slide's week anchor (None when the slide
    carries no parseable week — a template/blank slide — in which case
    ``notes`` says why and the record lists are empty).
    """

    drivers: tuple[DriverRecord, ...] = ()
    resources: tuple[ResourceRecord, ...] = ()
    week_monday: date | None = None
    notes: tuple[str, ...] = ()


@runtime_checkable
class DeckSlideParser(Protocol):
    """Strategy: parse one slide of a deck into records."""

    def parse_slide(
        self,
        slide: SlideAlt,
        *,
        course: CourseRef | None,
        student: StudentRef,
        source_kind: SourceKind,
        source_url: str,
        scraped_at: datetime,
        reference: date,
    ) -> SlideRecords:
        """Parse ``slide``. ``course`` is the deck-level default course;
        table-style parsers may override it per section. ``reference``
        is the scrape's calendar date, for year inference (never the
        wall clock)."""
        ...


class DeckParserRegistry:
    """An immutable template-id -> parser mapping with Lookup access."""

    def __init__(self, parsers: dict[str, DeckSlideParser]) -> None:
        self._parsers: dict[str, DeckSlideParser] = dict(parsers)

    def parser_for(self, template_id: str) -> Lookup[DeckSlideParser]:
        """The parser registered for ``template_id``, or Missing."""
        parser = self._parsers.get(template_id)
        if parser is None:
            return Missing(
                reason=f"no deck parser registered for template "
                f"{template_id!r}"
            )
        return Found(value=parser)

    def template_ids(self) -> tuple[str, ...]:
        """Registered template ids, deterministically ordered."""
        return tuple(sorted(self._parsers))


_KIND_RULES: tuple[tuple[re.Pattern[str], DriverKind], ...] = (
    (re.compile(r"\bquiz\b", re.IGNORECASE), DriverKind.QUIZ),
    (re.compile(r"\btest\b|\bassessment\b", re.IGNORECASE), DriverKind.TEST),
    (re.compile(r"\bproject\b", re.IGNORECASE), DriverKind.PROJECT),
    (
        re.compile(r"\bworkbook\b|\bpractice\b", re.IGNORECASE),
        DriverKind.PRACTICE,
    ),
)


def kind_for_text(text: str) -> DriverKind:
    """A due item's kind from its text, via the ordered rule table."""
    for pattern, kind in _KIND_RULES:
        if pattern.search(text):
            return kind
    return DriverKind.ASSIGNMENT
