"""TeamTableParser against the real team agenda fixture."""

from datetime import date, datetime

from school.domain import CourseRef, DriverKind, ResourceKind, SourceKind
from school.driver_web.slides import SlideAlt, SlideText, extract_slide_alts
from school.driver_web.table_parser import (
    TeamTableParser,
    group_cells,
    resolve_days,
    split_table,
)
from driver_web.conftest import NOW, SAM, fixture_text

REFERENCE = date(2026, 10, 22)


def _parse(index: int = 0):
    slides = extract_slide_alts(fixture_text("team_agenda_embed.html"))
    return TeamTableParser().parse_slide(
        slides[index],
        course=None,
        student=SAM,
        source_kind=SourceKind.AGENDA,
        source_url="deck://team",
        scraped_at=NOW,
        reference=REFERENCE,
    )


def test_current_week_all_subjects() -> None:
    result = _parse(0)
    assert result.week_monday == date(2026, 10, 19)
    # ELA 5 + Social Studies 5 + Science 5; Math cells are stubs.
    assert len(result.drivers) == 15
    courses = {d.course.name for d in result.drivers}
    assert courses == {"ELA", "Social Studies", "Science"}


def test_specific_cells_and_due_grammar() -> None:
    result = _parse(0)
    by_title = {d.title: d for d in result.drivers}
    # SS Monday: "*GRADED* Quiz 8.4A", Due: 10/19 (FD 10/26) -> FD wins.
    quiz = by_title["Quiz 8.4A"]
    assert quiz.kind is DriverKind.QUIZ
    assert quiz.course.name == "Social Studies"
    assert quiz.due_at == datetime(2026, 10, 26)
    # ELA "Due: Friday" resolves to this week's Friday.
    ela = by_title["Read + Entry: What kind of person is my MC?"]
    assert ela.due_at == datetime(2026, 10, 23)
    # ELA Friday cell: "Due: Next week" -> next Friday.
    ela_friday = by_title["Read + Entry: Minor Characters\u2019 POV"]
    assert ela_friday.due_at == datetime(2026, 10, 30)
    # Science Tuesday: due text ends " - FD 10/27".
    science = by_title[
        "Plan an inquiry investigation that tests all 3 "
        "Newton\u2019s Laws of Motion"
    ]
    assert science.due_at == datetime(2026, 10, 27)


def test_math_stub_cells_emit_no_drivers() -> None:
    result = _parse(0)
    assert not any(d.course.name == "Math" for d in result.drivers)
    assert not any("Class Calendar" in d.title for d in result.drivers)


def test_slide_links_become_course_less_resources() -> None:
    result = _parse(0)
    assert len(result.resources) == 5
    by_title = {r.title: r for r in result.resources}
    webquest = by_title["Revolutionary War Webquest"]
    assert webquest.kind is ResourceKind.LINK
    assert webquest.course is None
    calendars = [
        r for r in result.resources if r.title == "Class Calendar"
    ]
    assert len(calendars) == 2  # Math 8 + Algebra I decks, deduped
    assert all(
        r.kind is ResourceKind.DOCUMENT for r in calendars
    )


def test_split_table_structure() -> None:
    slides = extract_slide_alts(fixture_text("team_agenda_embed.html"))
    table_text = max(slides[0].texts, key=lambda t: len(t.text)).text
    split = split_table(
        table_text, subjects=("ELA", "Math", "Social Studies", "Science")
    )
    assert [d[0] for d in split.days] == [
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
    ]
    assert [s.subject for s in split.sections] == [
        "ELA", "Math", "Social Studies", "Science",
    ]
    math = split.sections[1]
    assert len(math.paragraphs) == 10  # two tracks x five days
    assert group_cells(math.paragraphs) is not None
    assert group_cells(("a", "b", "c")) is None


def test_older_week_slide() -> None:
    result = _parse(1)
    assert result.week_monday == date(2026, 10, 12)
    assert len(result.drivers) > 0


def test_slide_without_table_block_is_skipped() -> None:
    slide = SlideAlt(
        texts=(SlideText(text="Just a title", rect=None),), links=()
    )
    result = TeamTableParser().parse_slide(
        slide,
        course=None,
        student=SAM,
        source_kind=SourceKind.AGENDA,
        source_url="deck://team",
        scraped_at=NOW,
        reference=REFERENCE,
    )
    assert result.week_monday is None
    assert any("no table block" in note for note in result.notes)


def test_ungroupable_section_aligns_by_order_with_a_note() -> None:
    table = (
        "\n\nMonday 10/19\n\nTuesday 10/20\n\nWednesday 10/21"
        "\n\nThursday 10/22\n\nFriday 10/23\n\nELA"
        "\n\nTopic: A\nAgenda: Do A\nDue: 10/19"
        "\n\nTopic: B\nAgenda: Do B\nDue: 10/20"
        "\n\nTopic: C\nAgenda: Do C\nDue: 10/21"
    )
    slide = SlideAlt(texts=(SlideText(text=table, rect=None),), links=())
    result = TeamTableParser().parse_slide(
        slide,
        course=None,
        student=SAM,
        source_kind=SourceKind.AGENDA,
        source_url="deck://team",
        scraped_at=NOW,
        reference=REFERENCE,
    )
    assert [d.title for d in result.drivers] == ["Do A", "Do B", "Do C"]
    assert any("aligned by order" in note for note in result.notes)


def test_resolve_days_without_headers() -> None:
    split = split_table("ELA\n\nTopic: X", subjects=("ELA",))
    assert split.days == ()
    assert resolve_days(split, reference=REFERENCE) == ()
