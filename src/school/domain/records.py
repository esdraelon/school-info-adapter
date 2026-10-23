"""Normalized records: the currency every source driver produces (C10).

Pattern: frozen dataclasses with keyword-only construction (C4) — immutable
value objects, safe to share between merge, store, and MCP layers without
defensive copies. Every record carries ``scraped_at``: freshness is data,
never inferred (C10).

Scores are deliberately kept as the source's own text (``score_text``):
sources express them inconsistently ("87.77 points out of possible 100",
"9/10", letter grades), and inventing a numeric model here would lose
information the digest may want verbatim.
"""

from dataclasses import dataclass
from datetime import datetime

from school.domain.enums import (
    DriverKind,
    ItemStatus,
    ResourceKind,
    RunOutcome,
    SourceKind,
)


@dataclass(frozen=True, kw_only=True)
class StudentRef:
    """A student, identified by a stable id (e.g. HAC/Classroom account id)."""

    id: str
    display_name: str


@dataclass(frozen=True, kw_only=True)
class CourseRef:
    """A class/course as a source names it; period/teacher when known."""

    name: str
    period: str | None = None
    teacher: str | None = None


@dataclass(frozen=True, kw_only=True)
class DriverRecord:
    """One due item (a "Driver"): what it is, which class, when it's due.

    Produced by a single source; cross-source dedup/merging happens in
    school.merge. ``due_at`` is None for posted-but-undated items.
    """

    student: StudentRef
    course: CourseRef
    title: str
    kind: DriverKind
    status: ItemStatus
    source_kind: SourceKind
    scraped_at: datetime
    due_at: datetime | None = None
    score_text: str | None = None
    source_ref: str | None = None


@dataclass(frozen=True, kw_only=True)
class ResourceRecord:
    """One piece of material (a "Resource"): notes, tutorials, links, docs.

    ``student`` is None for resources shared across students (e.g. a team
    site's tutorial schedule); ``course`` is None when a resource is not
    tied to one class.
    """

    title: str
    kind: ResourceKind
    url: str
    source_kind: SourceKind
    scraped_at: datetime
    student: StudentRef | None = None
    course: CourseRef | None = None


@dataclass(frozen=True, kw_only=True)
class SourceRun:
    """The log of one scrape of one source (C10: every scrape writes one).

    Counts are from the source's point of view: how many records it found,
    and of those how many were new / updated relative to the store at
    write time. ``message`` carries failure detail or a partial-run note.
    """

    source_kind: SourceKind
    started_at: datetime
    finished_at: datetime
    outcome: RunOutcome
    found: int = 0
    new: int = 0
    updated: int = 0
    message: str | None = None
