"""The HAC field-mapping layer: pure functions, table-driven.

Fixtures and tables encode the REAL cache schema (verified 2026-10-22;
see school.driver_hac.mapping's docstring): a per-student object with a
``courses`` list, course names carrying a "<code> - <period>" prefix,
items keyed name/category/date_assigned/date_due/max_points/score/notes.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from school.domain import (
    CourseRef,
    DriverKind,
    Found,
    ItemStatus,
    Missing,
)
from school.driver_hac.mapping import (
    extract_courses,
    map_course,
    map_item,
    map_kind,
    map_status,
    parse_course_name,
    parse_due_at,
    score_text,
)
from driver_hac.conftest import NOW, REFERENCE_DATE, SAM


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # The real prefix shape: code, period digit, display name.
        (
            "1801 - 1    Language Arts 8",
            CourseRef(name="Language Arts 8", period="P1"),
        ),
        (
            "4801ADV - 4    Social Studies 8 Advanced",
            CourseRef(name="Social Studies 8 Advanced", period="P4"),
        ),
        (
            "1801 - 1 Language Arts 8",  # single-space separators still parse
            CourseRef(name="Language Arts 8", period="P1"),
        ),
        # No prefix pattern: the whole collapsed name, period None.
        ("Wind Ensemble", CourseRef(name="Wind Ensemble")),
        ("  Wind   Ensemble  ", CourseRef(name="Wind Ensemble")),
        # A dash without a period digit is not the prefix.
        ("1801 - Language Arts 8", CourseRef(name="1801 - Language Arts 8")),
        # A two-digit "period" is not the real shape's single digit.
        ("1234 - 12    Gym", CourseRef(name="1234 - 12 Gym")),
        # Code + period but nothing after: not the prefix either.
        ("1801 - 1", CourseRef(name="1801 - 1")),
    ],
)
def test_parse_course_name(raw: str, expected: CourseRef) -> None:
    assert parse_course_name(raw) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("10/23/2026", datetime(2026, 10, 23, tzinfo=timezone.utc)),
        ("1/5/2027", datetime(2027, 1, 5, tzinfo=timezone.utc)),
        ("2026-10-23", datetime(2026, 10, 23, tzinfo=timezone.utc)),
        (
            "2026-10-23T15:30:00",
            datetime(2026, 10, 23, 15, 30, tzinfo=timezone.utc),
        ),
        (
            "2026-10-23T15:30:00Z",
            datetime(2026, 10, 23, 15, 30, tzinfo=timezone.utc),
        ),
        (date(2026, 10, 23), datetime(2026, 10, 23, tzinfo=timezone.utc)),
        (
            datetime(2026, 10, 23, 9, 0),  # naive: read as UTC
            datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc),
        ),
        (
            datetime(2026, 10, 23, 9, 0, tzinfo=timezone(timedelta(hours=-5))),
            datetime(2026, 10, 23, 9, 0, tzinfo=timezone(timedelta(hours=-5))),
        ),
        ("not a date", None),
        ("", None),
        ("   ", None),
        (None, None),
        (20261023, None),
        (["2026-10-23"], None),
    ],
)
def test_parse_due_at(value: object, expected: datetime | None) -> None:
    assert parse_due_at(value) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"score": 98}, "98"),
        ({"score": 98.0}, "98"),
        ({"score": 80.0}, "80"),
        ({"score": 87.77}, "87.77"),
        ({"score": 0.0}, "0"),  # a zero score is still a score
        ({"score": 0}, "0"),
        ({"score": "18/20"}, "18/20"),  # strings pass through (tolerance)
        ({"score": " 9/10 "}, "9/10"),
        ({"score": None}, None),  # the real cache's unscored marker
        ({}, None),  # a missing score key is just unscored
        ({"score": ""}, None),
        ({"score": "   "}, None),
        ({"score": True}, None),  # booleans are not scores
        ({"score": [98]}, None),
    ],
)
def test_score_text(raw: dict[str, object], expected: str | None) -> None:
    assert score_text(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # A present score is GRADED — even 0.0, even when past due:
        # the cache cannot distinguish earned-zero from missing-zero.
        ({"score": 55.0, "date_due": "10/20/2026"}, ItemStatus.GRADED),
        ({"score": 0.0, "date_due": "10/20/2026"}, ItemStatus.GRADED),
        ({"score": 80.0}, ItemStatus.GRADED),
        # Unscored + due strictly before the reference date -> MISSING.
        ({"score": None, "date_due": "10/21/2026"}, ItemStatus.MISSING),
        ({"date_due": "09/01/2026"}, ItemStatus.MISSING),  # score key absent
        # Unscored + due on/after the reference date -> ASSIGNED.
        ({"score": None, "date_due": "10/22/2026"}, ItemStatus.ASSIGNED),
        ({"score": None, "date_due": "10/23/2026"}, ItemStatus.ASSIGNED),
        # No due date (or an unparseable one) can never be MISSING.
        ({"score": None}, ItemStatus.ASSIGNED),
        ({"score": None, "date_due": "not a date"}, ItemStatus.ASSIGNED),
        ({}, ItemStatus.ASSIGNED),
    ],
)
def test_map_status(raw: dict[str, object], expected: ItemStatus) -> None:
    assert map_status(raw, reference_date=REFERENCE_DATE) is expected


def test_map_status_reference_date_is_the_boundary() -> None:
    raw = {"score": None, "date_due": "10/22/2026"}
    assert map_status(raw, reference_date=date(2026, 10, 22)) is (
        ItemStatus.ASSIGNED
    )
    assert map_status(raw, reference_date=date(2026, 10, 23)) is (
        ItemStatus.MISSING
    )


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        # Every category value observed in the real cache (2026-10-22):
        ("Summative (Product)", DriverKind.TEST),
        ("Assessments", DriverKind.TEST),
        ("Tests-Projects", DriverKind.TEST),  # 'test' outranks 'project'
        ("Daily Work-Quizzes", DriverKind.QUIZ),
        ("Weekly Assignments", DriverKind.ASSIGNMENT),
        ("Daily Practice", DriverKind.PRACTICE),
        ("Daily grade-assignments", DriverKind.ASSIGNMENT),
        ("Daily Work-Participation", DriverKind.ASSIGNMENT),
        ("Participation", DriverKind.ASSIGNMENT),
        ("Sectionals", DriverKind.ASSIGNMENT),
        ("Practice Records", DriverKind.PRACTICE),
        ("Performance", DriverKind.ASSIGNMENT),
        # Heuristic behavior beyond the observed set:
        ("Pop Quiz Friday", DriverKind.QUIZ),
        ("Group Project", DriverKind.PROJECT),
        ("Unit Test Review", DriverKind.TEST),
        # Absent/blank means ASSIGNMENT.
        (None, DriverKind.ASSIGNMENT),
        ("", DriverKind.ASSIGNMENT),
    ],
)
def test_map_kind(category: str | None, expected: DriverKind) -> None:
    assert map_kind(category) is expected


def _item(raw: dict[str, object]) -> object:
    return map_item(
        raw,
        course_name="1801 - 1    Language Arts 8",
        student=SAM,
        scraped_at=NOW,
        reference_date=REFERENCE_DATE,
    )


def test_map_item_full() -> None:
    result = _item(
        {
            "name": "Literary Essay Unit Assessment",
            "category": "Summative (Product)",
            "date_assigned": "09/29/2026",
            "date_due": "10/02/2026",
            "max_points": 100.0,
            "score": 80.0,
            "notes": "",
        }
    )
    assert isinstance(result, Found)
    record = result.unwrap()
    assert record.student is SAM
    assert record.course == CourseRef(name="Language Arts 8", period="P1")
    assert record.title == "Literary Essay Unit Assessment"
    assert record.kind is DriverKind.TEST
    assert record.status is ItemStatus.GRADED
    assert record.score_text == "80"
    assert record.due_at == datetime(2026, 10, 2, tzinfo=timezone.utc)
    assert record.scraped_at == NOW
    # Cache items have no id: dedup carries identity, not source_ref.
    assert record.source_ref is None


def test_map_item_unscored_past_due_is_missing() -> None:
    record = _item(
        {"name": "Vocabulary Quiz 4", "date_due": "10/20/2026", "score": None}
    ).unwrap()
    assert record.status is ItemStatus.MISSING
    assert record.score_text is None


def test_map_item_tolerates_missing_optional_keys() -> None:
    """Only ``name`` is required; every other real key may be absent."""
    record = _item({"name": "Warmups"}).unwrap()
    assert record.due_at is None
    assert record.kind is DriverKind.ASSIGNMENT
    assert record.status is ItemStatus.ASSIGNED
    assert record.score_text is None


def test_map_item_numeric_title_is_text() -> None:
    record = _item({"name": 42}).unwrap()
    assert record.title == "42"


@pytest.mark.parametrize(
    ("raw", "reason_part"),
    [
        ("a string, not an object", "not an object"),
        (["a", "list"], "not an object"),
        ({"category": "Daily Practice"}, "no title"),
        ({"name": "  "}, "no title"),
    ],
)
def test_map_item_unmappable(raw: object, reason_part: str) -> None:
    result = map_item(
        raw,
        course_name="Wind Ensemble",
        student=SAM,
        scraped_at=NOW,
        reference_date=REFERENCE_DATE,
    )
    assert isinstance(result, Missing)
    assert reason_part in result.reason


def _course(raw: object) -> tuple[list[str], list[str]]:
    mapping = map_course(
        raw,
        student=SAM,
        scraped_at=NOW,
        reference_date=REFERENCE_DATE,
    )
    return [r.title for r in mapping.records], list(mapping.skipped)


def test_map_course_maps_items_under_the_course_name() -> None:
    titles, skipped = _course(
        {
            "name": "4801ADV - 4    Social Studies 8 Advanced",
            "average": 88.5,
            "assignments": [
                {"name": "Timeline Project", "score": 87.77},
                {"name": "Map Skills Check", "score": 0.0},
            ],
        }
    )
    assert titles == ["Timeline Project", "Map Skills Check"]
    assert skipped == []


def test_map_course_counts_bad_items_as_skipped() -> None:
    titles, skipped = _course(
        {
            "name": "3301 - 4    Mathematics 8",
            "assignments": [{"name": "Good"}, {"category": "x"}, "stray"],
        }
    )
    assert titles == ["Good"]
    assert len(skipped) == 2
    assert any("no title" in reason for reason in skipped)
    assert any("not an object" in reason for reason in skipped)


def test_map_course_not_an_object() -> None:
    titles, skipped = _course("junk")
    assert titles == []
    assert len(skipped) == 1
    assert "not an object" in skipped[0]


@pytest.mark.parametrize(
    "raw",
    [
        {"name": "Math 8"},  # assignments key absent
        {"name": "Math 8", "assignments": {"not": "a list"}},
        {"assignments": None},
    ],
)
def test_map_course_without_assignments_list(raw: object) -> None:
    titles, skipped = _course(raw)
    assert titles == []
    assert len(skipped) == 1
    assert "no assignments list" in skipped[0]


def test_map_course_with_no_name_skips_each_item() -> None:
    titles, skipped = _course(
        {"name": "   ", "assignments": [{"name": "A"}, {"name": "B"}]}
    )
    assert titles == []
    assert skipped == ["item in a course with no name"] * 2


def _found_courses(payload: object) -> list[object]:
    result = extract_courses(payload)
    assert isinstance(result, Found)
    return result.unwrap()


def test_extract_courses_real_shape() -> None:
    payload = {
        "student_id": None,  # present in the real cache; ignored
        "courses": [{"name": "Wind Ensemble", "assignments": []}],
    }
    assert _found_courses(payload) == [
        {"name": "Wind Ensemble", "assignments": []}
    ]


def test_extract_courses_empty_list_is_found() -> None:
    assert _found_courses({"courses": []}) == []


def test_extract_courses_preserves_non_object_entries() -> None:
    payload = {"courses": [{"name": "x"}, "stray", 7]}
    assert _found_courses(payload) == [{"name": "x"}, "stray", 7]


@pytest.mark.parametrize(
    ("payload", "reason_part"),
    [
        ({"student_id": None}, "no 'courses' list"),
        ({"courses": {"not": "a list"}}, "no 'courses' list"),
        ({"courses": None}, "no 'courses' list"),
        ([{"name": "x"}], "not an object"),
        (42, "not an object"),
        ("cache?", "not an object"),
    ],
)
def test_extract_courses_missing(payload: object, reason_part: str) -> None:
    result = extract_courses(payload)
    assert isinstance(result, Missing)
    assert reason_part in result.reason
