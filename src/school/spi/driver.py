"""The source-driver contract. Pattern: Strategy (one driver per source).

Every source (HAC, team site, agenda decks, Classroom web) is a strategy
implementing this protocol; the composition root registers the set and
nothing outside a driver knows how its source works. Drivers are selected
exclusively through DriverRegistry (convention C2).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from school.domain import (
    DriverRecord,
    ResourceRecord,
    SourceKind,
    SourceRun,
    StudentRef,
)


@dataclass(frozen=True, kw_only=True)
class ScrapeContext:
    """Everything a driver needs for one scrape, injected by the caller.

    ``now`` is the caller's clock (drivers never read the wall clock
    directly — run stamps stay testable and consistent within a run).
    ``config`` is driver-specific configuration from the composition root
    (URLs, cache paths, ...); drivers narrow the values they consume.
    """

    student: StudentRef
    now: datetime
    config: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class ScrapeResult:
    """One scrape's output: its run log plus every record it produced."""

    run: SourceRun
    drivers: list[DriverRecord] = field(default_factory=list)
    resources: list[ResourceRecord] = field(default_factory=list)


@runtime_checkable
class SourceDriver(Protocol):
    """A pluggable source adapter. Implementations live in driver_* modules."""

    @property
    def kind(self) -> SourceKind:
        """Which source this driver scrapes."""
        ...

    def scrape(self, context: ScrapeContext) -> ScrapeResult:
        """Scrape the source once; failures are reported via the run's
        outcome/message, not raised, so one dead source can't sink a run
        of the others."""
        ...
