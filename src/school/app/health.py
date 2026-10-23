"""Health evaluation: SourceHealth facts -> an OK/WARNING/UNHEALTHY verdict.

Pattern: pure function over injected facts (C3/C10). The MCP layer
reports *what happened* (latest run outcome, when it finished); this
module owns the *judgement* — which of those facts mean a consumer
must not trust the data:

- never ran                                 -> UNHEALTHY
- latest run outcome FAILURE                -> UNHEALTHY
- age beyond the kind's staleness threshold -> UNHEALTHY
- latest run outcome PARTIAL                -> WARNING (data flows,
  but incomplete — surfaced, not fatal)
- otherwise                                 -> OK

Kinds with no configured threshold simply get no staleness verdict;
the never-ran and FAILURE rules still apply. Ages are recomputed from
``now`` and the run's finish stamp (never trusted from a precomputed
age), so evaluations are reproducible.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from school.domain import RunOutcome, SourceKind
from school.mcp_server.services import SourceHealth

SECONDS_PER_HOUR = 3600.0


class HealthStatus(StrEnum):
    """One source's evaluated health."""

    OK = "ok"
    WARNING = "warning"
    UNHEALTHY = "unhealthy"


@dataclass(frozen=True, kw_only=True)
class HealthEntry:
    """One source's verdict, with the reason in plain language."""

    source_kind: SourceKind
    status: HealthStatus
    reason: str


@dataclass(frozen=True, kw_only=True)
class HealthReport:
    """The verdict for every evaluated source."""

    entries: tuple[HealthEntry, ...]

    @property
    def ok(self) -> bool:
        """True when no entry is UNHEALTHY (warnings do not fail it)."""
        return all(
            entry.status is not HealthStatus.UNHEALTHY
            for entry in self.entries
        )


def _evaluate_one(
    health: SourceHealth,
    *,
    threshold_hours: float | None,
    now: datetime,
) -> HealthEntry:
    kind = health.source_kind

    def entry(status: HealthStatus, reason: str) -> HealthEntry:
        return HealthEntry(source_kind=kind, status=status, reason=reason)

    if health.last_finished_at is None or health.last_outcome is None:
        return entry(HealthStatus.UNHEALTHY, "source has never run")
    if health.last_outcome is RunOutcome.FAILURE:
        detail = f": {health.message}" if health.message else ""
        return entry(
            HealthStatus.UNHEALTHY, f"latest run failed{detail}"
        )
    age_hours = (now - health.last_finished_at).total_seconds() / (
        SECONDS_PER_HOUR
    )
    if threshold_hours is not None and age_hours > threshold_hours:
        return entry(
            HealthStatus.UNHEALTHY,
            f"stale: latest run finished {age_hours:.1f}h ago, over "
            f"the {threshold_hours:g}h threshold",
        )
    if health.last_outcome is RunOutcome.PARTIAL:
        detail = f": {health.message}" if health.message else ""
        return entry(
            HealthStatus.WARNING, f"latest run was partial{detail}"
        )
    return entry(
        HealthStatus.OK,
        f"latest run succeeded {age_hours:.1f}h ago",
    )


def evaluate_health(
    health: list[SourceHealth],
    thresholds: Mapping[SourceKind, float],
    now: datetime,
) -> HealthReport:
    """Evaluate every entry, preserving the input order."""
    return HealthReport(
        entries=tuple(
            _evaluate_one(
                entry,
                threshold_hours=thresholds.get(entry.source_kind),
                now=now,
            )
            for entry in health
        )
    )
