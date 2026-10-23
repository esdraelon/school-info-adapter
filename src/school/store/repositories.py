"""Repositories over the SQLite store. Pattern: Repository.

Each repository is constructed with an injected ConnectionFactory and
opens a short-lived connection per operation — the store is written by
intermittent scrapers and read by an intermittent MCP, so there is no
long-lived connection to manage. Absence follows C1: single reads return
Lookup, list queries return lists (empty when nothing matches), and list
filters are ``X | None`` parameters where None means "unfiltered".
"""

import json
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from datetime import datetime, timezone

from school.domain import (
    CourseRef,
    DedupKey,
    DriverKind,
    DriverRecord,
    Found,
    ItemStatus,
    Lookup,
    Missing,
    ResourceKind,
    ResourceRecord,
    RunOutcome,
    SourceKind,
    SourceRun,
    StudentRef,
    normalize_text,
)
from school.store.schema import ConnectionFactory


def _dt_to_text(value: datetime) -> str:
    """Serialize a datetime as ISO-8601 UTC text (naive is taken as UTC)."""
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).isoformat()


def _dt_from_text(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _open(factory: ConnectionFactory) -> sqlite3.Connection:
    connection = factory()
    connection.row_factory = sqlite3.Row
    return connection


_DRIVER_COLUMNS = (
    "dedup_key, student_id, student_name, course_name, course_period, "
    "course_teacher, title, kind, due_at, status, score_text, source_kind, "
    "source_ref, scraped_at"
)

_UPSERT_DRIVER = f"""
INSERT INTO drivers ({_DRIVER_COLUMNS})
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (dedup_key) DO UPDATE SET
    student_name = excluded.student_name,
    course_period = excluded.course_period,
    course_teacher = excluded.course_teacher,
    title = excluded.title,
    kind = excluded.kind,
    due_at = excluded.due_at,
    status = excluded.status,
    score_text = excluded.score_text,
    source_kind = excluded.source_kind,
    source_ref = excluded.source_ref,
    scraped_at = excluded.scraped_at
"""


def _driver_params(record: DriverRecord) -> tuple[object, ...]:
    return (
        DedupKey.from_record(record).to_storage(),
        record.student.id,
        record.student.display_name,
        record.course.name,
        record.course.period,
        record.course.teacher,
        record.title,
        record.kind.value,
        _dt_to_text(record.due_at) if record.due_at is not None else None,
        record.status.name,
        record.score_text,
        record.source_kind.value,
        record.source_ref,
        _dt_to_text(record.scraped_at),
    )


def _row_to_driver(row: sqlite3.Row) -> DriverRecord:
    due_text = row["due_at"]
    return DriverRecord(
        student=StudentRef(id=row["student_id"], display_name=row["student_name"]),
        course=CourseRef(
            name=row["course_name"],
            period=row["course_period"],
            teacher=row["course_teacher"],
        ),
        title=row["title"],
        kind=DriverKind(row["kind"]),
        status=ItemStatus[row["status"]],
        source_kind=SourceKind(row["source_kind"]),
        scraped_at=_dt_from_text(row["scraped_at"]),
        due_at=_dt_from_text(due_text) if due_text is not None else None,
        score_text=row["score_text"],
        source_ref=row["source_ref"],
    )


class DriverRepository:
    """Due items, keyed by DedupKey. Pattern: Repository."""

    def __init__(self, connect: ConnectionFactory) -> None:
        self._connect = connect

    def upsert_driver(self, record: DriverRecord) -> None:
        self.upsert_drivers([record])

    def upsert_drivers(self, records: Iterable[DriverRecord]) -> None:
        params = [_driver_params(record) for record in records]
        if not params:
            return
        with closing(_open(self._connect)) as connection, connection:
            connection.executemany(_UPSERT_DRIVER, params)

    def get_driver(self, key: DedupKey) -> Lookup[DriverRecord]:
        with closing(_open(self._connect)) as connection:
            row = connection.execute(
                f"SELECT {_DRIVER_COLUMNS} FROM drivers WHERE dedup_key = ?",
                (key.to_storage(),),
            ).fetchone()
        if row is None:
            return Missing(reason=f"no driver stored under key {key.to_storage()}")
        return Found(value=_row_to_driver(row))

    def list_drivers(
        self,
        *,
        student_id: str | None = None,
        course_name: str | None = None,
    ) -> list[DriverRecord]:
        """Drivers for a student and/or exact course name, dated first."""
        clauses: list[str] = []
        params: list[object] = []
        if student_id is not None:
            clauses.append("student_id = ?")
            params.append(student_id)
        if course_name is not None:
            clauses.append("course_name = ?")
            params.append(course_name)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            f"SELECT {_DRIVER_COLUMNS} FROM drivers{where}"
            " ORDER BY due_at IS NULL, due_at, title"
        )
        with closing(_open(self._connect)) as connection:
            rows = connection.execute(sql, params).fetchall()
        return [_row_to_driver(row) for row in rows]


_RESOURCE_COLUMNS = (
    "natural_key, student_id, student_name, course_name, course_period, "
    "course_teacher, title, kind, url, source_kind, scraped_at"
)

_UPSERT_RESOURCE = f"""
INSERT INTO resources ({_RESOURCE_COLUMNS})
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (natural_key) DO UPDATE SET
    title = excluded.title,
    kind = excluded.kind,
    scraped_at = excluded.scraped_at
"""


def _resource_key(record: ResourceRecord) -> str:
    """Natural identity of a resource: owner + normalized course/title + URL.

    The URL is part of the key verbatim (URLs are already canonical in a
    way titles are not); titles/courses go through the domain normalizer.
    """
    return json.dumps(
        [
            record.student.id if record.student is not None else None,
            normalize_text(record.course.name) if record.course is not None else None,
            normalize_text(record.title),
            record.url,
            record.source_kind.value,
        ],
        separators=(",", ":"),
    )


def _resource_params(record: ResourceRecord) -> tuple[object, ...]:
    student = record.student
    course = record.course
    return (
        _resource_key(record),
        student.id if student is not None else None,
        student.display_name if student is not None else None,
        course.name if course is not None else None,
        course.period if course is not None else None,
        course.teacher if course is not None else None,
        record.title,
        record.kind.value,
        record.url,
        record.source_kind.value,
        _dt_to_text(record.scraped_at),
    )


def _row_to_resource(row: sqlite3.Row) -> ResourceRecord:
    student = None
    if row["student_id"] is not None:
        student = StudentRef(id=row["student_id"], display_name=row["student_name"])
    course = None
    if row["course_name"] is not None:
        course = CourseRef(
            name=row["course_name"],
            period=row["course_period"],
            teacher=row["course_teacher"],
        )
    return ResourceRecord(
        title=row["title"],
        kind=ResourceKind(row["kind"]),
        url=row["url"],
        source_kind=SourceKind(row["source_kind"]),
        scraped_at=_dt_from_text(row["scraped_at"]),
        student=student,
        course=course,
    )


class ResourceRepository:
    """Materials, keyed by natural key. Pattern: Repository."""

    def __init__(self, connect: ConnectionFactory) -> None:
        self._connect = connect

    def upsert_resource(self, record: ResourceRecord) -> None:
        self.upsert_resources([record])

    def upsert_resources(self, records: Iterable[ResourceRecord]) -> None:
        params = [_resource_params(record) for record in records]
        if not params:
            return
        with closing(_open(self._connect)) as connection, connection:
            connection.executemany(_UPSERT_RESOURCE, params)

    def list_resources(
        self,
        *,
        student_id: str | None = None,
        course_name: str | None = None,
    ) -> list[ResourceRecord]:
        """Resources matching the filters exactly.

        Shared resources (no student/course) appear only when the
        corresponding filter is unset — callers wanting "Sam's plus
        shared" union an unfiltered call themselves, explicitly.
        """
        clauses: list[str] = []
        params: list[object] = []
        if student_id is not None:
            clauses.append("student_id = ?")
            params.append(student_id)
        if course_name is not None:
            clauses.append("course_name = ?")
            params.append(course_name)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT {_RESOURCE_COLUMNS} FROM resources{where} ORDER BY title"
        with closing(_open(self._connect)) as connection:
            rows = connection.execute(sql, params).fetchall()
        return [_row_to_resource(row) for row in rows]


class RunLogRepository:
    """One row per scrape (C10). Pattern: Repository."""

    def __init__(self, connect: ConnectionFactory) -> None:
        self._connect = connect

    def append_run(self, run: SourceRun) -> None:
        with closing(_open(self._connect)) as connection, connection:
            connection.execute(
                "INSERT INTO source_runs (source_kind, started_at, finished_at,"
                " outcome, message, records_found, records_new, records_updated)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run.source_kind.value,
                    _dt_to_text(run.started_at),
                    _dt_to_text(run.finished_at),
                    run.outcome.value,
                    run.message,
                    run.found,
                    run.new,
                    run.updated,
                ),
            )

    def latest_run(self, kind: SourceKind) -> Lookup[SourceRun]:
        """The most recently finished run of ``kind``, or Missing."""
        with closing(_open(self._connect)) as connection:
            row = connection.execute(
                "SELECT source_kind, started_at, finished_at, outcome, message,"
                " records_found, records_new, records_updated FROM source_runs"
                " WHERE source_kind = ? ORDER BY finished_at DESC, id DESC"
                " LIMIT 1",
                (kind.value,),
            ).fetchone()
        if row is None:
            return Missing(reason=f"no runs recorded for {kind.value}")
        return Found(
            value=SourceRun(
                source_kind=SourceKind(row["source_kind"]),
                started_at=_dt_from_text(row["started_at"]),
                finished_at=_dt_from_text(row["finished_at"]),
                outcome=RunOutcome(row["outcome"]),
                found=row["records_found"],
                new=row["records_new"],
                updated=row["records_updated"],
                message=row["message"],
            )
        )
