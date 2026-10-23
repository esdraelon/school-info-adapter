"""App test fixtures: one student, one fixed clock, real in-memory repos.

The repositories open a fresh connection per operation, so — exactly as
in tests/unit/store — the fixture uses SQLite's shared-cache in-memory
mode with an anchor connection held open for the test's duration.
"""

import sqlite3
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone

import pytest

from school.domain import StudentRef
from school.store import (
    ConnectionFactory,
    DriverRepository,
    ResourceRepository,
    RunLogRepository,
    migrate,
)

SAM = StudentRef(id="sam", display_name="Sam")
NOW = datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)


@pytest.fixture
def connection_factory() -> Iterator[ConnectionFactory]:
    uri = f"file:schoolapp-{uuid.uuid4().hex}?mode=memory&cache=shared"
    anchor = sqlite3.connect(uri, uri=True)
    migrate(anchor)

    def connect() -> sqlite3.Connection:
        return sqlite3.connect(uri, uri=True)

    yield connect
    anchor.close()


@pytest.fixture
def driver_repository(
    connection_factory: ConnectionFactory,
) -> DriverRepository:
    return DriverRepository(connection_factory)


@pytest.fixture
def resource_repository(
    connection_factory: ConnectionFactory,
) -> ResourceRepository:
    return ResourceRepository(connection_factory)


@pytest.fixture
def run_log_repository(
    connection_factory: ConnectionFactory,
) -> RunLogRepository:
    return RunLogRepository(connection_factory)
