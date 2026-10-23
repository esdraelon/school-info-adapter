"""Query services: the read model behind the MCP tools.

Pattern: Query service over injected repositories. This is where every
read-side decision lives — filters the repositories cannot express, the
own-plus-shared union for resources, staleness math for source health —
so tool handlers stay pure argument translation (see server.py). The
store already holds the *merged* view (the scrape flow merges before
upserting), so nothing here re-merges.

Constructed with the three repositories and an injected clock (C2/C3);
``now`` for health is still passed explicitly by the caller so health
answers are reproducible in tests.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from school.domain import (
    DriverRecord,
    Found,
    ItemStatus,
    ResourceKind,
    ResourceRecord,
    RunOutcome,
    SourceKind,
)
from school.store import DriverRepository, ResourceRepository, RunLogRepository


@dataclass(frozen=True, kw_only=True)
class SourceHealth:
    """One source's freshness, derived from its latest run (C10).

    All fields but ``source_kind`` are None when the source has never
    completed a run: "never scraped" is a distinct fact from "scraped
    and failed", and consumers must be able to tell them apart.
    """

    source_kind: SourceKind
    last_outcome: RunOutcome | None = None
    last_finished_at: datetime | None = None
    age_seconds: float | None = None
    message: str | None = None


class QueryServices:
    """Read queries over the merged store. Pattern: Query service."""

    def __init__(
        self,
        *,
        drivers: DriverRepository,
        resources: ResourceRepository,
        runs: RunLogRepository,
        clock: Callable[[], datetime],
    ) -> None:
        self._drivers = drivers
        self._resources = resources
        self._runs = runs
        self._clock = clock

    def now(self) -> datetime:
        """The injected clock's current time (handlers' ``now``)."""
        return self._clock()

    def list_drivers(
        self,
        student_id: str,
        *,
        course_name: str | None = None,
        status: ItemStatus | None = None,
        due_on_or_before: datetime | None = None,
        due_on_or_after: datetime | None = None,
        limit: int = 200,
    ) -> list[DriverRecord]:
        """Drivers for one student, richest filters last.

        The repository expresses student/course; status, the due
        window, and the limit are applied here. A due window excludes
        undated records — an item with no due date cannot be shown to
        fall inside a window, and quietly including it would lie.
        """
        records = self._drivers.list_drivers(
            student_id=student_id, course_name=course_name
        )
        if status is not None:
            records = [r for r in records if r.status is status]
        if due_on_or_before is not None or due_on_or_after is not None:
            records = [
                r
                for r in records
                if r.due_at is not None
                and (due_on_or_after is None or r.due_at >= due_on_or_after)
                and (due_on_or_before is None or r.due_at <= due_on_or_before)
            ]
        return records[: max(limit, 0)]

    def list_resources(
        self,
        student_id: str | None = None,
        *,
        course_name: str | None = None,
        kind: ResourceKind | None = None,
        limit: int = 200,
    ) -> list[ResourceRecord]:
        """Resources visible to a student: their own plus shared ones.

        The repository matches filters exactly (its docstring puts the
        union decision on the caller) — this is that caller. A resource
        is visible when it is shared (no student) or owned by
        ``student_id``, and when it is courseless or matches
        ``course_name``; with ``student_id`` unset, every resource is
        visible. The set is small (one family's materials), so the
        union is computed over one unfiltered repository read.
        """
        records = [
            record
            for record in self._resources.list_resources()
            if (student_id is None or _student_visible(record, student_id))
            and (
                course_name is None
                or record.course is None
                or record.course.name == course_name
            )
        ]
        if kind is not None:
            records = [r for r in records if r.kind is kind]
        return records[: max(limit, 0)]

    def source_health(self, now: datetime) -> list[SourceHealth]:
        """Per-kind freshness from the run log, in SourceKind order.

        ``now`` must be tz-aware (run stamps are stored aware); the age
        is simply ``now - last_finished_at`` — staleness as arithmetic
        over facts, never inferred (C10).
        """
        health: list[SourceHealth] = []
        for kind in SourceKind:
            match self._runs.latest_run(kind):
                case Found(value=run):
                    health.append(
                        SourceHealth(
                            source_kind=kind,
                            last_outcome=run.outcome,
                            last_finished_at=run.finished_at,
                            age_seconds=(
                                now - run.finished_at
                            ).total_seconds(),
                            message=run.message,
                        )
                    )
                case _:
                    health.append(SourceHealth(source_kind=kind))
        return health


def _student_visible(record: ResourceRecord, student_id: str) -> bool:
    return record.student is None or record.student.id == student_id
