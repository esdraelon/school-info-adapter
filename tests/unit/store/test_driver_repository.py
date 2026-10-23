"""DriverRepository round-trips against a real in-memory SQLite (C7)."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from school.domain import (
    CourseRef,
    DedupKey,
    DriverKind,
    DriverRecord,
    Found,
    ItemStatus,
    Missing,
    SourceKind,
    StudentRef,
)
from school.store import ConnectionFactory, DriverRepository

UTC = timezone.utc
CDT = timezone(timedelta(hours=-5))
NOW = datetime(2026, 10, 22, 18, 0, tzinfo=UTC)
SAM = StudentRef(id="sam", display_name="Sam")
MATH = CourseRef(name="Math 8", period="P3", teacher="VanDyke")


def _record(**overrides: object) -> DriverRecord:
    kwargs: dict[str, object] = {
        "student": SAM,
        "course": MATH,
        "title": "Workbook p. 42",
        "kind": DriverKind.ASSIGNMENT,
        "status": ItemStatus.ASSIGNED,
        "source_kind": SourceKind.HAC,
        "scraped_at": NOW,
        "due_at": datetime(2026, 10, 23, 23, 59, tzinfo=UTC),
        "score_text": None,
        "source_ref": "hac:12345",
    }
    kwargs.update(overrides)
    return DriverRecord(**kwargs)  # type: ignore[arg-type]


def test_round_trip_full_fidelity(connection_factory: ConnectionFactory) -> None:
    repo = DriverRepository(connection_factory)
    record = _record(status=ItemStatus.GRADED, score_text="87.77 points out of possible 100")
    repo.upsert_driver(record)
    result = repo.get_driver(DedupKey.from_record(record))
    assert isinstance(result, Found)
    assert result.unwrap() == record


def test_round_trip_none_fields(connection_factory: ConnectionFactory) -> None:
    repo = DriverRepository(connection_factory)
    record = _record(due_at=None, score_text=None, source_ref=None)
    repo.upsert_driver(record)
    fetched = repo.get_driver(DedupKey.from_record(record)).unwrap()
    assert fetched.due_at is None
    assert fetched.score_text is None
    assert fetched.source_ref is None
    assert fetched.course.period == "P3"


def test_non_utc_datetimes_round_trip_by_instant(
    connection_factory: ConnectionFactory,
) -> None:
    repo = DriverRepository(connection_factory)
    record = _record(due_at=datetime(2026, 10, 23, 23, 59, tzinfo=CDT))
    repo.upsert_driver(record)
    fetched = repo.get_driver(DedupKey.from_record(record)).unwrap()
    assert fetched.due_at == record.due_at  # same instant
    assert fetched.due_at is not None
    assert fetched.due_at.tzinfo == UTC  # stored normalized to UTC


def test_naive_datetimes_are_taken_as_utc(
    connection_factory: ConnectionFactory,
) -> None:
    repo = DriverRepository(connection_factory)
    record = _record(
        scraped_at=datetime(2026, 10, 22, 18, 0),
        due_at=datetime(2026, 10, 23, 9, 0),
    )
    repo.upsert_driver(record)
    fetched = repo.get_driver(DedupKey.from_record(record)).unwrap()
    assert fetched.scraped_at == datetime(2026, 10, 22, 18, 0, tzinfo=UTC)
    assert fetched.due_at == datetime(2026, 10, 23, 9, 0, tzinfo=UTC)


def test_get_missing_key(connection_factory: ConnectionFactory) -> None:
    repo = DriverRepository(connection_factory)
    result = repo.get_driver(DedupKey.from_record(_record()))
    assert isinstance(result, Missing)
    assert "no driver stored" in result.reason


def test_upsert_updates_in_place(connection_factory: ConnectionFactory) -> None:
    repo = DriverRepository(connection_factory)
    original = _record()
    repo.upsert_driver(original)
    updated = replace(
        original,
        status=ItemStatus.GRADED,
        score_text="9/10",
        scraped_at=NOW + timedelta(hours=1),
    )
    repo.upsert_driver(updated)
    assert repo.list_drivers() == [updated]


def test_upsert_empty_batch_is_a_no_op(
    connection_factory: ConnectionFactory,
) -> None:
    repo = DriverRepository(connection_factory)
    repo.upsert_drivers([])
    assert repo.list_drivers() == []


def test_list_filters(connection_factory: ConnectionFactory) -> None:
    repo = DriverRepository(connection_factory)
    sam_math = _record()
    sam_science = _record(course=CourseRef(name="Science"), title="Lab report")
    owen_math = _record(
        student=StudentRef(id="owen", display_name="Owen"), title="Quiz"
    )
    repo.upsert_drivers([sam_math, sam_science, owen_math])
    assert len(repo.list_drivers()) == 3
    # Same due_at for Sam's two -> title order decides ("Lab report" first).
    assert repo.list_drivers(student_id="sam") == [sam_science, sam_math]
    assert repo.list_drivers(course_name="Science") == [sam_science]
    assert repo.list_drivers(student_id="sam", course_name="Math 8") == [sam_math]
    assert repo.list_drivers(student_id="nobody") == []


def test_list_orders_dated_before_undated(
    connection_factory: ConnectionFactory,
) -> None:
    repo = DriverRepository(connection_factory)
    undated = _record(title="Posted reading", due_at=None)
    dated = _record(title="Dated quiz", due_at=datetime(2026, 10, 30, tzinfo=UTC))
    repo.upsert_drivers([undated, dated])
    assert repo.list_drivers() == [dated, undated]
