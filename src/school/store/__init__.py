"""SQLite persistence: schema/migrations, DriverRepository, ResourceRepository, RunLogRepository.

Pattern: Repository, with composable query helpers.
"""

from school.store.repositories import (
    DriverRepository,
    ResourceRepository,
    RunLogRepository,
)
from school.store.schema import ConnectionFactory, file_connection_factory, migrate

__all__ = [
    "ConnectionFactory",
    "DriverRepository",
    "ResourceRepository",
    "RunLogRepository",
    "file_connection_factory",
    "migrate",
]
