"""Domain model: DriverRecord, ResourceRecord, SourceRun, CourseRef, value types.

Patterns: frozen dataclasses; Lookup[T] union (Found / Missing) for
single-item lookups that can miss (convention C1).
"""

from school.domain.dedup import DedupKey, normalize_text
from school.domain.enums import (
    DriverKind,
    ItemStatus,
    ResourceKind,
    RunOutcome,
    SourceKind,
)
from school.domain.errors import MissingRecordError
from school.domain.lookup import Found, Lookup, Missing
from school.domain.records import (
    CourseRef,
    DriverRecord,
    ResourceRecord,
    SourceRun,
    StudentRef,
)

__all__ = [
    "CourseRef",
    "DedupKey",
    "DriverKind",
    "DriverRecord",
    "Found",
    "ItemStatus",
    "Lookup",
    "Missing",
    "MissingRecordError",
    "ResourceKind",
    "ResourceRecord",
    "RunOutcome",
    "SourceKind",
    "SourceRun",
    "StudentRef",
    "normalize_text",
]
