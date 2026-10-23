"""DedupKey derivation and the normalize_text contract."""

from datetime import datetime, timezone

import pytest

from school.domain import (
    CourseRef,
    DedupKey,
    DriverKind,
    DriverRecord,
    ItemStatus,
    SourceKind,
    StudentRef,
    normalize_text,
)
from school.domain.dedup import normalize_text as normalize_from_module

NOW = datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)
SAM = StudentRef(id="sam", display_name="Sam")


def _record(
    *,
    title: str = "Workbook p. 42",
    course: str = "Math 8",
    due_at: datetime | None = None,
    student: StudentRef = SAM,
) -> DriverRecord:
    return DriverRecord(
        student=student,
        course=CourseRef(name=course),
        title=title,
        kind=DriverKind.ASSIGNMENT,
        status=ItemStatus.ASSIGNED,
        source_kind=SourceKind.HAC,
        scraped_at=NOW,
        due_at=due_at,
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Workbook p. 42", "workbook p 42"),
        ("  Unit   3,\tPart 1! ", "unit 3 part 1"),
        ("UNIT 3 PART 1", "unit 3 part 1"),
        ("Spoon River: m. 171–180", "spoon river m 171 180"),
        ("Café Quiz", "cafe quiz"),
        ("", ""),
        ("!!!", ""),
    ],
)
def test_normalize_text(raw: str, expected: str) -> None:
    assert normalize_text(raw) == expected
    assert normalize_from_module(raw) == expected


def test_key_ignores_formatting_and_time_of_day() -> None:
    morning = _record(
        title="Unit 3, Part 1!",
        course="MATH 8",
        due_at=datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc),
    )
    evening = _record(
        title="unit 3 part 1",
        course="Math 8",
        due_at=datetime(2026, 10, 23, 23, 59, tzinfo=timezone.utc),
    )
    assert DedupKey.from_record(morning) == DedupKey.from_record(evening)


def test_key_distinguishes_due_date_course_and_student() -> None:
    base = _record(due_at=datetime(2026, 10, 23, tzinfo=timezone.utc))
    other_day = _record(due_at=datetime(2026, 10, 24, tzinfo=timezone.utc))
    undated = _record()
    other_course = _record(course="Science")
    other_student = _record(student=StudentRef(id="owen", display_name="Owen"))
    keys = {DedupKey.from_record(r) for r in (base, other_day, undated, other_course, other_student)}
    assert len(keys) == 5


def test_key_to_storage_is_deterministic_and_distinct() -> None:
    key = DedupKey.from_record(
        _record(due_at=datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc))
    )
    assert key.to_storage() == key.to_storage()
    assert key.to_storage() == '["sam","math 8","workbook p 42","2026-10-23"]'
    undated = DedupKey.from_record(_record())
    assert undated.to_storage().endswith(",null]")
    assert undated.to_storage() != key.to_storage()
