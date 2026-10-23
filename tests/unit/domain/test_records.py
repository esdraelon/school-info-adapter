"""Domain records: keyword construction, defaults, immutability (C4)."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from school.domain import (
    CourseRef,
    DriverKind,
    DriverRecord,
    ItemStatus,
    ResourceKind,
    ResourceRecord,
    RunOutcome,
    SourceKind,
    SourceRun,
    StudentRef,
)

NOW = datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)
SAM = StudentRef(id="sam", display_name="Sam")


def _driver(**overrides: object) -> DriverRecord:
    kwargs: dict[str, object] = {
        "student": SAM,
        "course": CourseRef(name="Math 8"),
        "title": "Workbook p. 42",
        "kind": DriverKind.ASSIGNMENT,
        "status": ItemStatus.ASSIGNED,
        "source_kind": SourceKind.HAC,
        "scraped_at": NOW,
    }
    kwargs.update(overrides)
    return DriverRecord(**kwargs)  # type: ignore[arg-type]


def test_driver_record_defaults_optional_fields_to_none() -> None:
    record = _driver()
    assert record.due_at is None
    assert record.score_text is None
    assert record.source_ref is None


def test_driver_record_keeps_score_as_source_text() -> None:
    record = _driver(
        status=ItemStatus.GRADED,
        score_text="87.77 points out of possible 100",
    )
    assert record.score_text == "87.77 points out of possible 100"


def test_records_are_frozen() -> None:
    record = _driver()
    with pytest.raises(FrozenInstanceError):
        record.title = "changed"  # type: ignore[misc]


def test_construction_is_keyword_only() -> None:
    with pytest.raises(TypeError):
        StudentRef("sam", "Sam")  # type: ignore[call-arg]


def test_course_ref_defaults() -> None:
    course = CourseRef(name="Math 8")
    assert course.period is None
    assert course.teacher is None
    full = CourseRef(name="Math 8", period="P3", teacher="VanDyke")
    assert (full.period, full.teacher) == ("P3", "VanDyke")


def test_resource_record_may_be_shared_and_courseless() -> None:
    resource = ResourceRecord(
        title="Tutorial schedule",
        kind=ResourceKind.TUTORIAL,
        url="https://example.test/tutorials",
        source_kind=SourceKind.TEAM_SITE,
        scraped_at=NOW,
    )
    assert resource.student is None
    assert resource.course is None


def test_source_run_defaults() -> None:
    run = SourceRun(
        source_kind=SourceKind.AGENDA,
        started_at=NOW,
        finished_at=NOW,
        outcome=RunOutcome.SUCCESS,
    )
    assert (run.found, run.new, run.updated) == (0, 0, 0)
    assert run.message is None
