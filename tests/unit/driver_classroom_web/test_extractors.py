"""Extractors: fixture-tabled against the exact strings captured in
docs/FIXTURES.md (reference clock: Thu 2026-10-22 18:00 UTC, matching
the rest of the suite).
"""

from datetime import date, datetime, time, timezone

import pytest

from school.domain import DriverKind, ItemStatus, SourceKind, StudentRef
from school.driver_classroom_web.extractors import (
    extract_todo,
    kind_for_title,
    parse_done_tail,
    parse_due_text,
    parse_row_name,
    record_for_row,
    RawRow,
)
from school.driver_classroom_web.pages import Row
from school.driver_classroom_web.tabs import TodoTab
from tests.unit.driver_classroom_web.fakes import FakeGroup, FakeTodoPage

REFERENCE = datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)
SAM = StudentRef(id="sam", display_name="Sam")
COURSES = [
    "Math 8",
    "Goodie - 8th Grade ELA",
    "Gholizadeh 8th Science",
    "26-27 Wind Ensemble (TMEA Honor Band)",
]


def at(day: date, clock: time) -> datetime:
    return datetime.combine(day, clock, tzinfo=timezone.utc)


class TestParseDueText:
    @pytest.mark.parametrize(
        "tail,expected",
        [
            ("Today, 11:59 PM", at(date(2026, 10, 22), time(23, 59))),
            ("Today, 9:00 AM", at(date(2026, 10, 22), time(9, 0))),
            ("Tomorrow, 9:00 AM", at(date(2026, 10, 23), time(9, 0))),
            ("Tomorrow, 11:59 PM", at(date(2026, 10, 23), time(23, 59))),
            ("Yesterday, 11:59 PM", at(date(2026, 10, 21), time(23, 59))),
            # Weekday + time (Missing tab near-term form): the Monday
            # nearest Thu Oct 22 is Oct 19; Friday is Oct 23.
            ("Monday, 11:59 PM", at(date(2026, 10, 19), time(23, 59))),
            ("Friday, 9:00 AM", at(date(2026, 10, 23), time(9, 0))),
            # Date-only forms land at end-of-day.
            ("Wednesday, Oct 28", at(date(2026, 10, 28), time(23, 59))),
            ("Wednesday, Dec 9", at(date(2026, 12, 9), time(23, 59))),
            ("Friday, Oct 9", at(date(2026, 10, 9), time(23, 59))),
            ("Wednesday, Oct 7", at(date(2026, 10, 7), time(23, 59))),
            ("Saturday, Oct 3", at(date(2026, 10, 3), time(23, 59))),
            ("Friday, Sep 25", at(date(2026, 9, 25), time(23, 59))),
        ],
    )
    def test_due_forms(self, tail: str, expected: datetime) -> None:
        parse = parse_due_text(tail, reference=REFERENCE)
        assert parse.due_at == expected
        assert parse.posted_on is None

    def test_posted_form_is_no_due_with_a_posted_date(self) -> None:
        parse = parse_due_text("Posted Monday, Aug 24", reference=REFERENCE)
        assert parse.due_at is None
        assert parse.posted_on == date(2026, 8, 24)

    def test_unrecognized_tail_degrades_to_no_due(self) -> None:
        parse = parse_due_text("whenever, honestly", reference=REFERENCE)
        assert parse.due_at is None
        assert parse.posted_on is None

    def test_noon_and_midnight_edges(self) -> None:
        parse = parse_due_text("Today, 12:00 PM", reference=REFERENCE)
        assert parse.due_at == at(date(2026, 10, 22), time(12, 0))
        parse = parse_due_text("Today, 12:00 AM", reference=REFERENCE)
        assert parse.due_at == at(date(2026, 10, 22), time(0, 0))


class TestParseDoneTail:
    def test_grade_text_extracted(self) -> None:
        assert (
            parse_done_tail("87.77 points out of possible 100 Not turned in")
            == "87.77 points out of possible 100"
        )
        assert (
            parse_done_tail("100 points out of possible 100 Turned in")
            == "100 points out of possible 100"
        )

    def test_status_only_tail_has_no_grade(self) -> None:
        assert parse_done_tail("Turned in") is None
        assert parse_done_tail("Done late") is None
        assert parse_done_tail("") is None


class TestParseRowName:
    def test_assigned_row(self) -> None:
        title, course, tail = parse_row_name(
            "Spoon River m. 171-180 26-27 Wind Ensemble "
            "(TMEA Honor Band) Today, 11:59 PM",
            COURSES,
        )
        assert title == "Spoon River m. 171-180"
        assert course == "26-27 Wind Ensemble (TMEA Honor Band)"
        assert tail == "Today, 11:59 PM"

    def test_posted_row(self) -> None:
        title, course, tail = parse_row_name(
            "8th Grade ELA Portfolio Goodie - 8th Grade ELA "
            "Posted Monday, Aug 24",
            COURSES,
        )
        assert title == "8th Grade ELA Portfolio"
        assert course == "Goodie - 8th Grade ELA"
        assert tail == "Posted Monday, Aug 24"

    def test_done_row(self) -> None:
        title, course, tail = parse_row_name(
            "10/21 Scale Run Through with Chromatic (Wind Ensemble) "
            "26-27 Wind Ensemble (TMEA Honor Band) "
            "87.77 points out of possible 100 Not turned in",
            COURSES,
        )
        assert title == "10/21 Scale Run Through with Chromatic (Wind Ensemble)"
        assert course == "26-27 Wind Ensemble (TMEA Honor Band)"
        assert tail == "87.77 points out of possible 100 Not turned in"

    def test_longest_course_wins(self) -> None:
        title, course, tail = parse_row_name(
            "Homework Wind Ensemble Today, 9:00 AM",
            ["Ensemble", "Wind Ensemble"],
        )
        assert (title, course, tail) == (
            "Homework",
            "Wind Ensemble",
            "Today, 9:00 AM",
        )

    def test_unknown_course_keeps_the_whole_head_as_title(self) -> None:
        title, course, tail = parse_row_name(
            "Mystery Work Unknown Class Tomorrow, 9:00 AM", COURSES
        )
        assert title == "Mystery Work Unknown Class"
        assert course == ""
        assert tail == "Tomorrow, 9:00 AM"

    def test_no_tail_at_all(self) -> None:
        title, course, tail = parse_row_name("Plain Title Math 8", COURSES)
        assert (title, course, tail) == ("Plain Title", "Math 8", "")


class TestKindForTitle:
    @pytest.mark.parametrize(
        "title,kind",
        [
            ("Fractions Quiz", DriverKind.QUIZ),
            ("Unit 3 Test", DriverKind.TEST),
            ("Chapter Assessment", DriverKind.TEST),
            ("RTR Timeline Project", DriverKind.PROJECT),
            ("Workbook Check", DriverKind.PRACTICE),
            ("Newton Practice Questions", DriverKind.PRACTICE),
            ("Spoon River Reading", DriverKind.ASSIGNMENT),
        ],
    )
    def test_rules(self, title: str, kind: DriverKind) -> None:
        assert kind_for_title(title) is kind


def _fake_page() -> FakeTodoPage:
    return FakeTodoPage(
        courses=COURSES,
        groups={
            "This week": FakeGroup(
                rows=[
                    Row(
                        name="Fractions Quiz Math 8 Tomorrow, 9:00 AM",
                        href="/c/math/d/1",
                    )
                ],
                hidden_rows=[
                    Row(
                        name="Workbook Practice Math 8 Friday, Oct 23",
                        href="/c/math/d/2",
                    )
                ],
            ),
            "No due date": FakeGroup(
                rows=[
                    Row(
                        name="8th Grade ELA Portfolio Goodie - 8th "
                        "Grade ELA Posted Monday, Aug 24",
                        href="/c/ela/d/3",
                    )
                ]
            ),
        },
    )


class TestExtractTodo:
    def test_expands_groups_and_reads_hidden_rows(self) -> None:
        page = _fake_page()
        rows = extract_todo(page, TodoTab.ASSIGNED, reference=REFERENCE)
        assert page.expanded == ["This week", "No due date"]
        assert [row.title for row in rows] == [
            "Fractions Quiz",
            "Workbook Practice",
            "8th Grade ELA Portfolio",
        ]
        first = rows[0]
        assert first.group == "This week"
        assert first.course == "Math 8"
        assert first.tail == "Tomorrow, 9:00 AM"
        assert first.href == "/c/math/d/1"


class TestRecordForRow:
    def _row(self, **overrides: object) -> RawRow:
        fields: dict[str, object] = {
            "group": "This week",
            "title": "Fractions Quiz",
            "course": "Math 8",
            "tail": "Tomorrow, 9:00 AM",
            "href": "/c/math/d/1",
        }
        fields.update(overrides)
        return RawRow(**fields)  # type: ignore[arg-type]

    def test_assigned_row_maps_with_due(self) -> None:
        record = record_for_row(
            self._row(), tab=TodoTab.ASSIGNED, student=SAM, scraped_at=REFERENCE
        )
        assert record.status is ItemStatus.ASSIGNED
        assert record.due_at == at(date(2026, 10, 23), time(9, 0))
        assert record.kind is DriverKind.QUIZ
        assert record.source_kind is SourceKind.CLASSROOM_WEB
        assert record.source_ref == "/c/math/d/1"
        assert record.scraped_at == REFERENCE
        assert record.course.name == "Math 8"

    def test_posted_row_maps_without_due(self) -> None:
        record = record_for_row(
            self._row(tail="Posted Monday, Aug 24"),
            tab=TodoTab.ASSIGNED,
            student=SAM,
            scraped_at=REFERENCE,
        )
        assert record.status is ItemStatus.ASSIGNED
        assert record.due_at is None

    def test_missing_row_maps_as_missing_with_past_due(self) -> None:
        record = record_for_row(
            self._row(tail="Friday, Oct 9"),
            tab=TodoTab.MISSING,
            student=SAM,
            scraped_at=REFERENCE,
        )
        assert record.status is ItemStatus.MISSING
        assert record.due_at == at(date(2026, 10, 9), time(23, 59))

    def test_done_row_with_grade_is_graded(self) -> None:
        record = record_for_row(
            self._row(tail="87.77 points out of possible 100 Not turned in"),
            tab=TodoTab.DONE,
            student=SAM,
            scraped_at=REFERENCE,
        )
        assert record.status is ItemStatus.GRADED
        assert record.score_text == "87.77 points out of possible 100"
        assert record.due_at is None

    def test_done_row_without_grade_is_done(self) -> None:
        record = record_for_row(
            self._row(tail="Done late"),
            tab=TodoTab.DONE,
            student=SAM,
            scraped_at=REFERENCE,
        )
        assert record.status is ItemStatus.DONE
        assert record.score_text is None
