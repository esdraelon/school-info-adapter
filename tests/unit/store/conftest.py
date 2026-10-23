"""Store test fixtures: a real (in-memory) SQLite database per test.

The repositories open a fresh connection per operation, so the fixture
uses SQLite's shared-cache in-memory mode: every connection to the URI
sees the same database while the anchor connection is held open.
"""

import sqlite3
import uuid
from collections.abc import Iterator

import pytest

from school.store import ConnectionFactory, migrate


@pytest.fixture
def connection_factory() -> Iterator[ConnectionFactory]:
    uri = f"file:schooltest-{uuid.uuid4().hex}?mode=memory&cache=shared"
    anchor = sqlite3.connect(uri, uri=True)
    migrate(anchor)

    def connect() -> sqlite3.Connection:
        return sqlite3.connect(uri, uri=True)

    yield connect
    anchor.close()
