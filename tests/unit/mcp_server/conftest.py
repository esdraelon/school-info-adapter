"""Shared fixtures for the MCP-server tests.

Repositories are the REAL store repositories over a shared-cache
in-memory SQLite database (the store suite's pattern) — services are
tested against the real store, never repository mocks (C7 spirit).
Seeding goes through the repositories' own upserts.
"""

import sqlite3
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone

import pytest

from school.domain import (
    CourseRef,
    DriverKind,
    DriverRecord,
    ItemStatus,
    ResourceKind,
    ResourceRecord,
    SourceKind,
    StudentRef,
)
from school.mcp_server.services import QueryServices
from school.store import (
    ConnectionFactory,
    DriverRepository,
    ResourceRepository,
    RunLogRepository,
    migrate,
)

NOW = datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)

SAM = StudentRef(id="sam", display_name="Sam")
OWEN = StudentRef(id="owen", display_name="Owen")
MATH = CourseRef(name="Math 8", period="3", teacher="VanDyke")
SOCIAL = CourseRef(name="Social Studies 8", period="4", teacher="Passwater")
SCIENCE = CourseRef(name="Science 8", period="2", teacher="Gholizadeh")


@pytest.fixture
def connection_factory() -> Iterator[ConnectionFactory]:
    uri = f"file:mcptest-{uuid.uuid4().hex}?mode=memory&cache=shared"
    anchor = sqlite3.connect(uri, uri=True)
    migrate(anchor)

    def connect() -> sqlite3.Connection:
        return sqlite3.connect(uri, uri=True)

    yield connect
    anchor.close()


@pytest.fixture
def drivers_repo(connection_factory: ConnectionFactory) -> DriverRepository:
    return DriverRepository(connection_factory)


@pytest.fixture
def resources_repo(
    connection_factory: ConnectionFactory,
) -> ResourceRepository:
    return ResourceRepository(connection_factory)


@pytest.fixture
def runs_repo(connection_factory: ConnectionFactory) -> RunLogRepository:
    return RunLogRepository(connection_factory)


@pytest.fixture
def services(
    drivers_repo: DriverRepository,
    resources_repo: ResourceRepository,
    runs_repo: RunLogRepository,
) -> QueryServices:
    return QueryServices(
        drivers=drivers_repo,
        resources=resources_repo,
        runs=runs_repo,
        clock=lambda: NOW,
    )


def make_driver(
    *,
    title: str,
    student: StudentRef = SAM,
    course: CourseRef = MATH,
    kind: DriverKind = DriverKind.ASSIGNMENT,
    status: ItemStatus = ItemStatus.ASSIGNED,
    due_at: datetime | None = None,
    score_text: str | None = None,
    source_kind: SourceKind = SourceKind.AGENDA,
    source_ref: str | None = None,
    scraped_at: datetime = NOW,
) -> DriverRecord:
    return DriverRecord(
        student=student,
        course=course,
        title=title,
        kind=kind,
        status=status,
        source_kind=source_kind,
        scraped_at=scraped_at,
        due_at=due_at,
        score_text=score_text,
        source_ref=source_ref,
    )


def make_resource(
    *,
    title: str,
    url: str,
    student: StudentRef | None = SAM,
    course: CourseRef | None = MATH,
    kind: ResourceKind = ResourceKind.NOTES,
    source_kind: SourceKind = SourceKind.AGENDA,
    scraped_at: datetime = NOW,
) -> ResourceRecord:
    return ResourceRecord(
        title=title,
        kind=kind,
        url=url,
        source_kind=source_kind,
        scraped_at=scraped_at,
        student=student,
        course=course,
    )
