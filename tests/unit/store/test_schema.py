"""Schema migrations: applied once, idempotent, file factory works."""

import sqlite3
from pathlib import Path

from school.store import file_connection_factory, migrate
from school.store.schema import MIGRATIONS


def test_migrate_creates_tables_and_records_versions() -> None:
    connection = sqlite3.connect(":memory:")
    migrate(connection)
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {"drivers", "resources", "source_runs", "schema_migrations"} <= tables
    versions = [
        row[0]
        for row in connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        )
    ]
    assert versions == [version for version, _ in MIGRATIONS]
    connection.close()


def test_migrate_is_idempotent() -> None:
    connection = sqlite3.connect(":memory:")
    migrate(connection)
    migrate(connection)
    migrate(connection)
    count = connection.execute(
        "SELECT COUNT(*) FROM schema_migrations"
    ).fetchone()[0]
    assert count == len(MIGRATIONS)
    connection.close()


def test_file_connection_factory_persists_across_connections(
    tmp_path: Path,
) -> None:
    factory = file_connection_factory(tmp_path / "school.db")
    with factory() as connection:
        connection.execute(
            "INSERT INTO source_runs (source_kind, started_at, finished_at,"
            " outcome, records_found, records_new, records_updated)"
            " VALUES ('hac', '2026-10-22T00:00:00+00:00',"
            " '2026-10-22T00:01:00+00:00', 'success', 0, 0, 0)"
        )
        connection.commit()
    with factory() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM source_runs"
        ).fetchone()[0]
    assert count == 1
