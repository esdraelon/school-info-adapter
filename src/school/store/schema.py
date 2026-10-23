"""SQLite schema and migrations.

Pattern: ordered migration list + idempotent runner. ``migrate`` applies
each pending (version, SQL) migration once and records it in
``schema_migrations``; calling it on every new connection keeps the
repositories free of schema bookkeeping.

Storage conventions: datetimes are ISO-8601 UTC text; enums are stored by
value (ItemStatus by name — its int values encode richness order, not a
storage format); the driver's dedup key is stored verbatim as the primary
key so upserts are natural (see school.domain.dedup).
"""

import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

#: How repositories get connections: injected, so tests use an in-memory
#: database and production uses a file — the repositories can't tell.
ConnectionFactory = Callable[[], sqlite3.Connection]

_V1 = """
CREATE TABLE drivers (
    dedup_key     TEXT PRIMARY KEY,
    student_id    TEXT NOT NULL,
    student_name  TEXT NOT NULL,
    course_name   TEXT NOT NULL,
    course_period TEXT,
    course_teacher TEXT,
    title         TEXT NOT NULL,
    kind          TEXT NOT NULL,
    due_at        TEXT,
    status        TEXT NOT NULL,
    score_text    TEXT,
    source_kind   TEXT NOT NULL,
    source_ref    TEXT,
    scraped_at    TEXT NOT NULL
);
CREATE INDEX idx_drivers_student ON drivers (student_id);
CREATE INDEX idx_drivers_course ON drivers (course_name);

CREATE TABLE resources (
    natural_key   TEXT PRIMARY KEY,
    student_id    TEXT,
    student_name  TEXT,
    course_name   TEXT,
    course_period TEXT,
    course_teacher TEXT,
    title         TEXT NOT NULL,
    kind          TEXT NOT NULL,
    url           TEXT NOT NULL,
    source_kind   TEXT NOT NULL,
    scraped_at    TEXT NOT NULL
);
CREATE INDEX idx_resources_student ON resources (student_id);

CREATE TABLE source_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_kind   TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT NOT NULL,
    outcome       TEXT NOT NULL,
    message       TEXT,
    records_found INTEGER NOT NULL,
    records_new   INTEGER NOT NULL,
    records_updated INTEGER NOT NULL
);
CREATE INDEX idx_runs_kind_finished ON source_runs (source_kind, finished_at);
"""

#: Ordered (version, SQL) migrations. Append-only: never edit a shipped one.
MIGRATIONS: tuple[tuple[int, str], ...] = ((1, _V1),)


def migrate(connection: sqlite3.Connection) -> None:
    """Apply every pending migration to ``connection`` (idempotent)."""
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = {
        row[0] for row in connection.execute("SELECT version FROM schema_migrations")
    }
    for version, sql in MIGRATIONS:
        if version in applied:
            continue
        connection.executescript(sql)
        connection.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (version, datetime.now(timezone.utc).isoformat()),
        )
    connection.commit()


def file_connection_factory(path: str | Path) -> ConnectionFactory:
    """A ConnectionFactory over a database file, migrating on each connect."""

    def connect() -> sqlite3.Connection:
        connection = sqlite3.connect(path)
        migrate(connection)
        return connection

    return connect
