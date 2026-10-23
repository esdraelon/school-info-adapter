"""Serializers: domain records -> wire dicts, field by field."""

from datetime import datetime, timedelta, timezone

from school.domain import (
    DriverKind,
    ItemStatus,
    ResourceKind,
    RunOutcome,
    SourceKind,
)
from school.mcp_server.serializers import (
    driver_to_dict,
    resource_to_dict,
    source_health_to_dict,
)
from school.mcp_server.services import SourceHealth
from mcp_server.conftest import MATH, NOW, SAM, make_driver, make_resource


def test_driver_full_record_field_by_field() -> None:
    record = make_driver(
        title="Timeline Project",
        kind=DriverKind.PROJECT,
        status=ItemStatus.GRADED,
        due_at=datetime(
            2026, 10, 23, 12, 0, tzinfo=timezone(timedelta(hours=2))
        ),
        score_text="87.77",
        source_kind=SourceKind.HAC,
        source_ref="cache:courses[1].assignments[0]",
    )
    assert driver_to_dict(record) == {
        "student": {"id": "sam", "display_name": "Sam"},
        "course": {"name": "Math 8", "period": "3", "teacher": "VanDyke"},
        "title": "Timeline Project",
        "kind": "PROJECT",
        "status": "GRADED",
        # +02:00 input is normalized to UTC with a Z suffix.
        "due_at": "2026-10-23T10:00:00Z",
        "score_text": "87.77",
        "source_kind": "HAC",
        "source_ref": "cache:courses[1].assignments[0]",
        "scraped_at": "2026-10-22T18:00:00Z",
    }


def test_driver_absent_fields_stay_null() -> None:
    record = make_driver(title="Undated item", due_at=None)
    result = driver_to_dict(record)
    assert result["due_at"] is None
    assert result["score_text"] is None
    assert result["source_ref"] is None
    assert result["status"] == "ASSIGNED"
    assert result["kind"] == "ASSIGNMENT"
    assert result["source_kind"] == "AGENDA"


def test_driver_naive_datetimes_are_taken_as_utc() -> None:
    record = make_driver(
        title="Naive",
        due_at=datetime(2026, 10, 26, 9, 30),
        scraped_at=datetime(2026, 10, 22, 8, 15),
    )
    result = driver_to_dict(record)
    assert result["due_at"] == "2026-10-26T09:30:00Z"
    assert result["scraped_at"] == "2026-10-22T08:15:00Z"


def test_resource_owned_record() -> None:
    record = make_resource(title="Volume notes", url="https://x.test/n")
    assert resource_to_dict(record) == {
        "student": {"id": "sam", "display_name": "Sam"},
        "course": {"name": "Math 8", "period": "3", "teacher": "VanDyke"},
        "title": "Volume notes",
        "kind": "NOTES",
        "url": "https://x.test/n",
        "source_kind": "AGENDA",
        "scraped_at": "2026-10-22T18:00:00Z",
    }


def test_resource_shared_record_keeps_nulls() -> None:
    record = make_resource(
        title="Tutorial schedule",
        url="https://x.test/t",
        student=None,
        course=None,
        kind=ResourceKind.TUTORIAL,
        source_kind=SourceKind.TEAM_SITE,
    )
    result = resource_to_dict(record)
    assert result["student"] is None
    assert result["course"] is None
    assert result["kind"] == "TUTORIAL"
    assert result["source_kind"] == "TEAM_SITE"


def test_source_health_full_and_never_run() -> None:
    full = SourceHealth(
        source_kind=SourceKind.HAC,
        last_outcome=RunOutcome.SUCCESS,
        last_finished_at=NOW,
        age_seconds=1800.0,
        message=None,
    )
    assert source_health_to_dict(full) == {
        "source_kind": "HAC",
        "last_outcome": "SUCCESS",
        "last_finished_at": "2026-10-22T18:00:00Z",
        "age_seconds": 1800.0,
        "message": None,
    }
    never = SourceHealth(source_kind=SourceKind.CLASSROOM_WEB)
    assert source_health_to_dict(never) == {
        "source_kind": "CLASSROOM_WEB",
        "last_outcome": None,
        "last_finished_at": None,
        "age_seconds": None,
        "message": None,
    }
