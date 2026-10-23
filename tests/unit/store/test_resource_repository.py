"""ResourceRepository round-trips against a real in-memory SQLite (C7)."""

from datetime import datetime, timedelta, timezone

from school.domain import (
    CourseRef,
    ResourceKind,
    ResourceRecord,
    SourceKind,
    StudentRef,
)
from school.store import ConnectionFactory, ResourceRepository

NOW = datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)
SAM = StudentRef(id="sam", display_name="Sam")
MATH = CourseRef(name="Math 8", period="P3", teacher="VanDyke")


def _resource(**overrides: object) -> ResourceRecord:
    kwargs: dict[str, object] = {
        "title": "Pythagorean theorem notes",
        "kind": ResourceKind.NOTES,
        "url": "https://example.test/notes",
        "source_kind": SourceKind.AGENDA,
        "scraped_at": NOW,
        "student": SAM,
        "course": MATH,
    }
    kwargs.update(overrides)
    return ResourceRecord(**kwargs)  # type: ignore[arg-type]


def test_round_trip_full_fidelity(connection_factory: ConnectionFactory) -> None:
    repo = ResourceRepository(connection_factory)
    record = _resource()
    repo.upsert_resource(record)
    assert repo.list_resources() == [record]


def test_shared_resource_round_trip(connection_factory: ConnectionFactory) -> None:
    repo = ResourceRepository(connection_factory)
    shared = _resource(
        title="Tutorial schedule",
        kind=ResourceKind.TUTORIAL,
        student=None,
        course=None,
    )
    repo.upsert_resource(shared)
    fetched = repo.list_resources()
    assert fetched == [shared]
    assert fetched[0].student is None
    assert fetched[0].course is None
    # Shared rows match only unfiltered queries (documented semantics).
    assert repo.list_resources(student_id="sam") == []
    assert repo.list_resources(course_name="Math 8") == []


def test_upsert_same_natural_key_updates(
    connection_factory: ConnectionFactory,
) -> None:
    repo = ResourceRepository(connection_factory)
    original = _resource()
    repo.upsert_resource(original)
    fresher = _resource(scraped_at=NOW + timedelta(hours=2))
    repo.upsert_resource(fresher)
    assert repo.list_resources() == [fresher]


def test_upsert_empty_batch_is_a_no_op(
    connection_factory: ConnectionFactory,
) -> None:
    repo = ResourceRepository(connection_factory)
    repo.upsert_resources([])
    assert repo.list_resources() == []


def test_list_filters(connection_factory: ConnectionFactory) -> None:
    repo = ResourceRepository(connection_factory)
    math_notes = _resource()
    science_notes = _resource(
        title="Lab safety",
        course=CourseRef(name="Science"),
        url="https://example.test/lab",
    )
    owen_notes = _resource(
        title="Owen notes",
        student=StudentRef(id="owen", display_name="Owen"),
        url="https://example.test/owen",
    )
    repo.upsert_resources([math_notes, science_notes, owen_notes])
    assert repo.list_resources(student_id="sam") == [science_notes, math_notes]
    assert repo.list_resources(course_name="Math 8") == [owen_notes, math_notes]
    assert repo.list_resources(student_id="sam", course_name="Math 8") == [
        math_notes
    ]
    assert repo.list_resources(student_id="nobody") == []
