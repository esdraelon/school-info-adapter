"""M7 failure drill D2: deck format drift.

One of the math deck's slides has its accessibility layer replaced by
garbage — the shape a Slides format change would take. The agenda
driver must skip that week WITH a note, keep parsing the other weeks,
and finish SUCCESS with the drift announced in the run message:
visible degradation, never silent data loss.
"""

import re

from school.domain import RunOutcome
from school.driver_web import AgendaDriver, DeckConfig, default_parser_registry
from school.spi import ScrapeContext
from driver_web.conftest import (
    MATH_DECK_URL,
    NOW,
    SAM,
    fixture_text,
    make_fetcher,
)

MATH = DeckConfig(
    subject="Math 8", url=MATH_DECK_URL, template_id="weekly-grid-v1"
)

_ASSIGN_RE = re.compile(r"SK_svgData\s*=\s*'((?:[^'\\]|\\.)*)'", re.DOTALL)

_GARBAGE_SLIDE = (
    '<svg><g role="img" aria-label="### drifted beyond recognition ###">'
    '<path d="m 10 10l0 20l 100 0l0 -20z"/></g></svg>'
)


def _escape(svg: str) -> str:
    """The pub pages' JS-string escaping (mirrors conftest.make_page)."""
    return (
        svg.replace("\\", "\\\\")
        .replace("<", "\\x3c")
        .replace(">", "\\x3e")
        .replace('"', "\\x22")
        .replace("&", "\\x26")
        .replace("/", "\\/")
    )


def _corrupt_slide(page: str, index: int) -> str:
    """Replace one slide's SVG payload with the garbage slide."""
    match = list(_ASSIGN_RE.finditer(page))[index]
    return (
        page[: match.start(1)]
        + _escape(_GARBAGE_SLIDE)
        + page[match.end(1) :]
    )


def test_deck_format_drift_skips_one_week_visibly() -> None:
    # Slide 3 is an older week; the Pythagorean quiz week (slide 1)
    # is one of the weeks that must survive.
    page = _corrupt_slide(fixture_text("math_agenda_pub.html"), 3)
    driver = AgendaDriver(
        fetcher=make_fetcher({MATH_DECK_URL: page}),
        decks=(MATH,),
        parsers=default_parser_registry(),
    )
    result = driver.scrape(ScrapeContext(student=SAM, now=NOW))
    assert result.run.outcome is RunOutcome.SUCCESS
    # One week is lost (the intact deck yields 84 drivers) — but the
    # other weeks still parse, including the Pythagorean quiz week.
    assert 0 < len(result.drivers) < 84
    assert any(
        d.title == "Quiz: Pythagorean Theorem" for d in result.drivers
    )
    # ...and the loss is announced in the run, not silent.
    assert result.run.message is not None
    assert "no Monday week anchor" in result.run.message
