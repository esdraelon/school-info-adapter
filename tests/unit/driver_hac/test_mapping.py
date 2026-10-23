"""The HAC field-mapping layer: pure functions, table-driven."""

from datetime import date, datetime, timedelta, timezone

import pytest

from school.domain import (
    CourseRef,
    DriverKind,
    Found,
    ItemStatus,
    Missing,
    StudentRef,
)
from school.driver_hac.mapping import (
    extract_student_items,
    map_item,
    map_kind,
    map_status,
    parse_course_name,
    parse_due_at,
    score_text,
)
from driver_hac.conftest import NOW, SAM

AIDEN = StudentRef(id="aiden", display_name="Aiden")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Math 8 P4", CourseRef(name="Math 8", period="P4")),
        ("Science P2", CourseRef(name="Science", period="P2")),
        ("Social Studies Period 4", CourseRef(name="Social Studies", period="P4")),
        ("Math 8 - Period 3", CourseRef(name="Math 8", period="P3")),
        ("Wind Ensemble (P4)", CourseRef(name="Wind Ensemble", period="P4")),
        ("Wind Ensemble", CourseRef(name="Wind Ensemble")),
        ("AP 4", CourseRef(name="AP 4")),  # 'P' not at a token boundary
        ("Room 204", CourseRef(name="Room 204")),  # digits alone are not a period
        ("P4", CourseRef(name="P4")),  # a marker with no name is just a name
        ("  Choir ", CourseRef(name="Choir")),
    ],
)
def test_parse_course_name(raw: str, expected: CourseRef) -> None:
    assert parse_course_name(raw) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-10-23", datetime(2026, 10, 23, tzinfo=timezone.utc)),
        (
            "2026-10-23T15:30:00",
            datetime(2026, 10, 23, 15, 30, tzinfo=timezone.utc),
        ),
        (
            "2026-10-23T15:30:00Z",
            datetime(2026, 10, 23, 15, 30, tzinfo=timezone.utc),
        ),
        ("10/23/2026", datetime(2026, 10, 23, tzinfo=timezone.utc)),
        ("1/5/2027", datetime(2027, 1, 5, tzinfo=timezone.utc)),
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
        ({"score": 87.77}, "87.77"),
        ({"score": 0}, "0"),  # a zero score is still a score
        ({"score": "18/20"}, "18/20"),
        ({"score": " 9/10 "}, "9/10"),
        ({"grade": "A-"}, "A-"),  # fallback field name
        ({"score_text": "7 pts"}, "7 pts"),  # fallback field name
        ({"score": None, "grade": "B"}, "B"),  # None does not shadow fallback
        ({"score": ""}, None),
        ({"score": "   "}, None),
        ({"score": True}, None),  # booleans are not scores
        ({"score": [98]}, None),
        ({}, None),
    ],
)
def test_score_text(raw: dict[str, object], expected: str | None) -> None:
    assert score_text(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"score": 55}, ItemStatus.GRADED),
        ({"score": 0}, ItemStatus.GRADED),
        # a score outranks a missing flag: graded is the richer signal
        ({"score": 55, "missing": True}, ItemStatus.GRADED),
        ({"missing": True}, ItemStatus.MISSING),
        ({"missing": "true"}, ItemStatus.MISSING),
        ({"missing": "yes"}, ItemStatus.MISSING),
        ({"missing": False}, ItemStatus.ASSIGNED),
        ({"status": "missing"}, ItemStatus.MISSING),
        ({"status": "Missing"}, ItemStatus.MISSING),
        ({"status": " missing "}, ItemStatus.MISSING),
        ({"status": "assigned"}, ItemStatus.ASSIGNED),
        # HAC has no turned-in signal; never fabricate DONE
        ({"status": "turned_in"}, ItemStatus.ASSIGNED),
        ({"status": "graded"}, ItemStatus.ASSIGNED),  # no score, no grade
        ({}, ItemStatus.ASSIGNED),
    ],
)
def test_map_status(raw: dict[str, object], expected: ItemStatus) -> None:
    assert map_status(raw) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({}, DriverKind.ASSIGNMENT),  # absent: HAC classwork is assignments
        ({"kind": "quiz"}, DriverKind.QUIZ),
        ({"kind": "Project"}, DriverKind.PROJECT),
        ({"category": "practice"}, DriverKind.PRACTICE),  # fallback field
        ({"type": "test"}, DriverKind.TEST),  # fallback field
        ({"kind": "homework"}, DriverKind.OTHER),  # unknown word
        ({"kind": ""}, DriverKind.ASSIGNMENT),  # blank counts as absent
    ],
)
def test_map_kind(raw: dict[str, object], expected: DriverKind) -> None:
    assert map_kind(raw) is expected


def test_map_item_full() -> None:
    result = map_item(
        {
            "id": "a5",
            "course": "Social Studies Period 4",
            "title": "RTR Timeline Project",
            "due_date": "2026-10-23",
            "score": "18/20",
            "kind": "project",
        },
        student=SAM,
        scraped_at=NOW,
    )
    assert isinstance(result, Found)
    record = result.unwrap()
    assert record.student is SAM
    assert record.course == CourseRef(name="Social Studies", period="P4")
    assert record.title == "RTR Timeline Project"
    assert record.kind is DriverKind.PROJECT
    assert record.status is ItemStatus.GRADED
    assert record.score_text == "18/20"
    assert record.due_at == datetime(2026, 10, 23, tzinfo=timezone.utc)
    assert record.scraped_at == NOW
    assert record.source_ref == "a5"


def test_map_item_alternate_field_names() -> None:
    result = map_item(
        {"class": "Math 8", "assignment": "Workbook p. 42", "due": "10/24/2026"},
        student=SAM,
        scraped_at=NOW,
    )
    record = result.unwrap()
    assert record.course.name == "Math 8"
    assert record.title == "Workbook p. 42"
    assert record.due_at == datetime(2026, 10, 24, tzinfo=timezone.utc)
    assert record.status is ItemStatus.ASSIGNED
    assert record.score_text is None
    assert record.source_ref is None


def test_map_item_numeric_title_is_text() -> None:
    record = map_item(
        {"course": "Math 8", "title": 42}, student=SAM, scraped_at=NOW
    ).unwrap()
    assert record.title == "42"


def test_map_item_minimal() -> None:
    record = map_item(
        {"course": "Choir", "title": "Warmups"}, student=SAM, scraped_at=NOW
    ).unwrap()
    assert record.due_at is None
    assert record.kind is DriverKind.ASSIGNMENT
    assert record.status is ItemStatus.ASSIGNED


@pytest.mark.parametrize(
    ("raw", "reason_part"),
    [
        ("a string, not an object", "not an object"),
        (["a", "list"], "not an object"),
        ({"title": "No course"}, "no course name"),
        ({"course": "   ", "title": "Blank course"}, "no course name"),
        ({"course": "ELA"}, "no title"),
        ({"course": "ELA", "title": "  "}, "no title"),
    ],
)
def test_map_item_unmappable(raw: object, reason_part: str) -> None:
    result = map_item(raw, student=SAM, scraped_at=NOW)
    assert isinstance(result, Missing)
    assert reason_part in result.reason


def _found_items(payload: object, student_id: str = "sam") -> list[object]:
    result = extract_student_items(payload, student_id)
    assert isinstance(result, Found)
    return result.unwrap()


def test_extract_from_student_keyed_payload() -> None:
    payload = {"sam": [{"title": "x"}], "owen": [{"title": "y"}]}
    assert _found_items(payload) == [{"title": "x"}]


def test_extract_from_students_wrapper() -> None:
    payload = {"students": {"sam": {"classwork": [{"title": "x"}]}}}
    assert _found_items(payload) == [{"title": "x"}]


def test_extract_student_node_as_bare_list() -> None:
    payload = {"students": {"sam": [{"title": "x"}, {"title": "z"}]}}
    assert _found_items(payload) == [{"title": "x"}, {"title": "z"}]


@pytest.mark.parametrize("key", ["classwork", "assignments", "items"])
def test_extract_item_list_key_fallbacks(key: str) -> None:
    payload = {"sam": {key: [{"title": "x"}]}}
    assert _found_items(payload) == [{"title": "x"}]


def test_extract_preserves_non_object_entries_for_skip_counting() -> None:
    payload = {"sam": [{"title": "x"}, "stray", 7]}
    assert _found_items(payload) == [{"title": "x"}, "stray", 7]


def test_extract_student_absent() -> None:
    result = extract_student_items({"owen": []}, "sam")
    assert isinstance(result, Missing)
    assert "'sam' not in cache" in result.reason
    result = extract_student_items({"students": {"owen": []}}, "sam")
    assert isinstance(result, Missing)


def test_extract_node_without_item_list() -> None:
    result = extract_student_items({"sam": {"notes": []}}, "sam")
    assert isinstance(result, Missing)
    assert "no item list" in result.reason


def test_extract_node_of_wrong_type() -> None:
    result = extract_student_items({"sam": "junk"}, "sam")
    assert isinstance(result, Missing)
    assert "is a str" in result.reason


def test_extract_students_wrapper_of_wrong_type() -> None:
    result = extract_student_items({"students": [1, 2]}, "sam")
    assert isinstance(result, Missing)
    assert "not in cache" in result.reason


def test_extract_from_bare_list_without_markers() -> None:
    payload = [{"title": "x"}, {"title": "y"}]
    assert _found_items(payload) == payload


def test_extract_from_bare_list_with_markers_filters() -> None:
    payload = [
        {"student": "sam", "title": "x"},
        {"student_id": "owen", "title": "y"},
        {"title": "unmarked"},  # unmarked entries drop out once markers exist
    ]
    assert _found_items(payload) == [{"student": "sam", "title": "x"}]


def test_extract_from_bare_list_markers_but_no_match() -> None:
    result = extract_student_items([{"student": "owen", "title": "y"}], "sam")
    assert isinstance(result, Missing)
    assert "not in cache" in result.reason


def test_extract_from_scalar_payload() -> None:
    result = extract_student_items(42, "sam")
    assert isinstance(result, Missing)
    assert "not an object or list" in result.reason
