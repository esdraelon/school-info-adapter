"""WeeklyGridParser against the real Math/Algebra deck fixtures."""

from datetime import date, datetime, timezone

from school.domain import (
    CourseRef,
    DriverKind,
    Found,
    Missing,
    ResourceKind,
    SourceKind,
)
from school.driver_web.grid_parser import WeeklyGridParser
from school.driver_web.parsers import DeckParserRegistry, kind_for_text
from school.driver_web.slides import (
    Rect,
    SlideAlt,
    SlideLink,
    SlideText,
    extract_slide_alts,
)
from driver_web.conftest import MATH_DECK_URL, NOW, SAM, fixture_text

REFERENCE = date(2026, 10, 22)
MATH = CourseRef(name="Math 8")


def _parse(fixture: str, index: int, course: CourseRef = MATH):
    slides = extract_slide_alts(fixture_text(fixture))
    return WeeklyGridParser().parse_slide(
        slides[index],
        course=course,
        student=SAM,
        source_kind=SourceKind.AGENDA,
        source_url=MATH_DECK_URL,
        scraped_at=NOW,
        reference=REFERENCE,
    ), slides[index]


def test_current_week_slide() -> None:
    result, slide = _parse("math_agenda_pub.html", 0)
    assert result.week_monday == date(2026, 10, 19)
    assert result.notes == ()
    assert len(result.drivers) == 10
    # Every link on the slide surfaces as exactly one resource.
    assert len(result.resources) == len(slide.links) == 14
    by_title = {d.title: d for d in result.drivers}
    topic = by_title["Area & Perimeter"]
    assert topic.due_at == datetime(2026, 10, 20)
    assert topic.kind is DriverKind.ASSIGNMENT
    assert topic.course == MATH
    assert topic.student == SAM
    page8 = by_title[
        "Indiv. Practice: Student workbook Page 8 (key) (due 10/21)"
    ]
    assert page8.kind is DriverKind.PRACTICE
    assert page8.due_at == datetime(2026, 10, 21)
    page11 = by_title[
        "Indiv. Practice: Student workbook Page 11 (key) (due 10/26)"
    ]
    assert page11.due_at == datetime(2026, 10, 26)


def test_notes_lines_pair_with_cell_links() -> None:
    result, _slide = _parse("math_agenda_pub.html", 0)
    notes = {
        r.title: r for r in result.resources if r.kind is ResourceKind.NOTES
    }
    assert set(notes) == {
        "Area/Perimeter", "3D Shapes",
        "Volume of cylinders", "Volume of cones",
    }
    assert notes["Area/Perimeter"].url == (
        "https://drive.google.com/file/d/"
        "1BkPPWP2CKYo-K5kSZxsAQPPGHOH30Via/view?usp=sharing"
    )
    keys = [
        r for r in result.resources if r.kind is ResourceKind.DOCUMENT
    ]
    assert len(keys) == 6  # 2 links in each of 3 "(key)" practice cells


def test_quiz_week_cross_validates_with_hac() -> None:
    """The 10/12 week's Friday cell is the Pythagorean quiz HAC shows:
    quiz on Fri 10/16, retake final deadline FD 10/26 — the FD wins."""
    result, _slide = _parse("math_agenda_pub.html", 1)
    assert result.week_monday == date(2026, 10, 12)
    quizzes = [d for d in result.drivers if d.kind is DriverKind.QUIZ]
    assert len(quizzes) == 1
    quiz = quizzes[0]
    assert quiz.title == "Quiz: Pythagorean Theorem"
    assert quiz.due_at == datetime(2026, 10, 26)
    # Mon/Tue were "No School": no drivers are dated those days.
    assert len(result.drivers) == 5
    assert not any(
        d.due_at in (datetime(2026, 10, 12), datetime(2026, 10, 13))
        for d in result.drivers
    )


def test_blank_template_slide_is_skipped_with_a_note() -> None:
    slides = extract_slide_alts(fixture_text("math_agenda_pub.html"))
    result = WeeklyGridParser().parse_slide(
        slides[11],
        course=MATH,
        student=SAM,
        source_kind=SourceKind.AGENDA,
        source_url=MATH_DECK_URL,
        scraped_at=NOW,
        reference=REFERENCE,
    )
    assert result.week_monday is None
    assert result.drivers == ()
    assert result.resources == ()
    assert any("skipped" in note for note in result.notes)


def test_algebra_deck_uses_the_same_template() -> None:
    result, _slide = _parse(
        "algebra_agenda_pub.html", 0, course=CourseRef(name="Algebra I")
    )
    assert result.week_monday == date(2026, 10, 19)
    assert len(result.drivers) == 10
    assert all(d.course.name == "Algebra I" for d in result.drivers)


def test_slide_without_headers_is_skipped() -> None:
    empty = SlideAlt(texts=(), links=())
    result = WeeklyGridParser().parse_slide(
        empty,
        course=MATH,
        student=SAM,
        source_kind=SourceKind.AGENDA,
        source_url=MATH_DECK_URL,
        scraped_at=NOW,
        reference=REFERENCE,
    )
    assert result.week_monday is None
    assert result.drivers == ()


def _rect(x: float, y: float) -> Rect:
    return Rect(x=x, y=y, width=180.0, height=40.0)


def _headers() -> list[SlideText]:
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    return [
        SlideText(text=f"{name} 10/{19 + i}", rect=_rect(i * 192 + 6, 58))
        for i, name in enumerate(names)
    ]


def _synthetic_parse(slide: SlideAlt):
    return WeeklyGridParser().parse_slide(
        slide,
        course=MATH,
        student=SAM,
        source_kind=SourceKind.AGENDA,
        source_url=MATH_DECK_URL,
        scraped_at=NOW,
        reference=REFERENCE,
    )


def test_synthetic_empty_columns_and_rectless_blocks() -> None:
    slide = SlideAlt(
        texts=tuple(
            _headers()
            + [
                SlideText(text="Only Monday topic", rect=_rect(6, 108)),
                SlideText(text="Ghost block", rect=None),
            ]
        ),
        links=(),
    )
    result = _synthetic_parse(slide)
    assert result.week_monday == date(2026, 10, 19)
    assert [d.title for d in result.drivers] == ["Only Monday topic"]


def test_synthetic_orphan_link_surfaces_at_course_level() -> None:
    slide = SlideAlt(
        texts=tuple(
            _headers()
            + [SlideText(text="Topic A", rect=_rect(6, 108))]
        ),
        links=(
            SlideLink(
                url="https://example.com/far",
                label="Far away",
                rect=Rect(x=5000.0, y=300.0, width=10.0, height=10.0),
            ),
        ),
    )
    result = _synthetic_parse(slide)
    assert len(result.resources) == 1
    orphan = result.resources[0]
    assert orphan.kind is ResourceKind.LINK
    assert orphan.title == "Far away"
    assert orphan.course == MATH


def test_synthetic_extra_blocks_fold_into_practice() -> None:
    wed = 2 * 192 + 6
    slide = SlideAlt(
        texts=tuple(
            _headers()
            + [
                SlideText(text="Wed topic", rect=_rect(wed, 108)),
                SlideText(text="Practice one", rect=_rect(wed, 318)),
                SlideText(text="Practice two", rect=_rect(wed, 400)),
            ]
        ),
        links=(),
    )
    result = _synthetic_parse(slide)
    titles = [d.title for d in result.drivers]
    assert "Wed topic" in titles
    folded = next(t for t in titles if "Practice one" in t)
    assert "Practice two" in folded


def test_synthetic_notes_line_without_link_is_dropped() -> None:
    slide = SlideAlt(
        texts=tuple(
            _headers()
            + [
                SlideText(
                    text="Topic B\nNotes: Unlinked notes",
                    rect=_rect(6, 108),
                )
            ]
        ),
        links=(),
    )
    result = _synthetic_parse(slide)
    assert [d.title for d in result.drivers] == ["Topic B"]
    assert result.resources == ()


def test_kind_for_text_rule_table() -> None:
    assert kind_for_text("Quiz: Pythagorean Theorem") is DriverKind.QUIZ
    assert kind_for_text("Unit Assessment") is DriverKind.TEST
    assert kind_for_text("Timeline Project") is DriverKind.PROJECT
    assert kind_for_text("Student workbook Page 8") is DriverKind.PRACTICE
    assert kind_for_text("Read chapter 4") is DriverKind.ASSIGNMENT


def test_registry_selects_parsers_by_template_id() -> None:
    registry = DeckParserRegistry({"weekly-grid-v1": WeeklyGridParser()})
    found = registry.parser_for("weekly-grid-v1")
    assert isinstance(found, Found)
    assert isinstance(found.unwrap(), WeeklyGridParser)
    missing = registry.parser_for("no-such-template")
    assert isinstance(missing, Missing)
    assert "no-such-template" in missing.reason
    assert registry.template_ids() == ("weekly-grid-v1",)
