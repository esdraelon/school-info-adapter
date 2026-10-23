"""Domain records -> JSON-ready dicts for the MCP wire.

Pattern: pure serializer functions (no I/O, no state). Conventions:
datetimes are ISO-8601 in UTC with a ``Z`` suffix (naive values are
taken as UTC, matching the store's convention); enums serialize by
*name* (``GRADED``, ``HAC``, ``NOTES``) so the wire vocabulary is the
domain vocabulary, not storage values; None stays None (JSON null) —
absence is never disguised as an empty string.
"""

from datetime import datetime, timezone

from school.domain import (
    CourseRef,
    DriverRecord,
    ResourceRecord,
    StudentRef,
)
from school.mcp_server.services import SourceHealth

JsonDict = dict[str, object]


def _format_datetime(value: datetime) -> str:
    """ISO-8601 UTC text with a Z suffix (naive is taken as UTC)."""
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def student_to_dict(student: StudentRef) -> JsonDict:
    return {"id": student.id, "display_name": student.display_name}


def course_to_dict(course: CourseRef) -> JsonDict:
    return {
        "name": course.name,
        "period": course.period,
        "teacher": course.teacher,
    }


def driver_to_dict(record: DriverRecord) -> JsonDict:
    """A DriverRecord as a wire dict, every domain field present."""
    return {
        "student": student_to_dict(record.student),
        "course": course_to_dict(record.course),
        "title": record.title,
        "kind": record.kind.name,
        "status": record.status.name,
        "due_at": (
            _format_datetime(record.due_at) if record.due_at is not None else None
        ),
        "score_text": record.score_text,
        "source_kind": record.source_kind.name,
        "source_ref": record.source_ref,
        "scraped_at": _format_datetime(record.scraped_at),
    }


def resource_to_dict(record: ResourceRecord) -> JsonDict:
    """A ResourceRecord as a wire dict; shared records keep nulls."""
    return {
        "student": (
            student_to_dict(record.student) if record.student is not None else None
        ),
        "course": (
            course_to_dict(record.course) if record.course is not None else None
        ),
        "title": record.title,
        "kind": record.kind.name,
        "url": record.url,
        "source_kind": record.source_kind.name,
        "scraped_at": _format_datetime(record.scraped_at),
    }


def source_health_to_dict(health: SourceHealth) -> JsonDict:
    """A SourceHealth as a wire dict; never-run sources keep nulls."""
    return {
        "source_kind": health.source_kind.name,
        "last_outcome": (
            health.last_outcome.name if health.last_outcome is not None else None
        ),
        "last_finished_at": (
            _format_datetime(health.last_finished_at)
            if health.last_finished_at is not None
            else None
        ),
        "age_seconds": health.age_seconds,
        "message": health.message,
    }
