"""Tutorial parsing against the real Tutorial Schedule fixture."""

from datetime import date

from school.domain import ResourceKind, SourceKind
from school.driver_web.tutorials import parse_tutorials
from driver_web.conftest import (
    NOW,
    TUTORIAL_EMBED_URL,
    fixture_text,
    make_page,
)

REFERENCE = date(2026, 10, 22)


def _parse(reference: date = REFERENCE):
    return parse_tutorials(
        fixture_text("tutorial_embed.html"),
        deck_url=TUTORIAL_EMBED_URL,
        scraped_at=NOW,
        reference=reference,
    )


def test_current_week_sessions() -> None:
    resources, notes = _parse()
    assert notes == ()
    # ELA 5 (day-aligned) + Math 6 (day-less; one "No tutorials" cell)
    # + Social Studies 4 (Friday cell empty) + Science 4 (one
    # "No tutorials" cell) = 19.
    assert len(resources) == 19
    assert all(r.kind is ResourceKind.TUTORIAL for r in resources)
    assert all(r.source_kind is SourceKind.TEAM_SITE for r in resources)
    assert all(r.url == TUTORIAL_EMBED_URL for r in resources)
    assert all(r.student is None for r in resources)


def test_day_aligned_titles_name_teacher_and_time() -> None:
    resources, _notes = _parse()
    titles = {r.title for r in resources}
    assert (
        "ELA tutorials \u2014 Monday 10/19: Goodie PM (4:20-4:50)"
        in titles
    )
    assert (
        "Social Studies tutorials \u2014 Wednesday 10/21: "
        "PM (4:20 - 4:40) - Passwater 2110"
    ) in titles
    science = [
        r for r in resources if r.course and r.course.name == "Science"
    ]
    assert len(science) == 4


def test_ungroupable_section_falls_back_to_week_titles() -> None:
    resources, _notes = _parse()
    math = [r for r in resources if r.course and r.course.name == "Math"]
    assert len(math) == 6
    assert all("week of 10/19" in r.title for r in math)
    assert any("VanDyke (2119)" in r.title for r in math)


def test_slides_without_tables_or_day_headers() -> None:
    no_table = make_page(
        '<svg><g role="img" aria-label="Hello">'
        '<path d="m0 0l0 1l1 0l0 -1z"/></g></svg>'
    )
    resources, notes = parse_tutorials(
        no_table,
        deck_url=TUTORIAL_EMBED_URL,
        scraped_at=NOW,
        reference=REFERENCE,
    )
    assert resources == ()
    assert notes == ()
    headerless = make_page(
        '<svg><g role="img" aria-label="Schedule\nMonday notes\n\nELA'
        '\n\nGoodie PM">'
        '<path d="m0 0l0 1l1 0l0 -1z"/></g></svg>'
    )
    resources, notes = parse_tutorials(
        headerless,
        deck_url=TUTORIAL_EMBED_URL,
        scraped_at=NOW,
        reference=REFERENCE,
    )
    assert resources == ()
    assert any("without day headers" in note for note in notes)


def test_old_weeks_are_windowed_out() -> None:
    # The fixture holds weeks 10/19, 10/12, 10/5; with a November
    # reference even the newest is outside the 7-day window.
    resources, _notes = _parse(reference=date(2026, 11, 20))
    assert resources == ()
    # From 10/14, the 10/12 week (current) and 10/19 (next, already
    # published) are in window; the 10/5 week is not.
    resources, _notes = _parse(reference=date(2026, 10, 14))
    assert resources
    assert any("week of 10/12" in r.title or "10/12" in r.title
               for r in resources)
    assert all("10/5" not in r.title for r in resources)
