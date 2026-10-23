"""Scrape orchestration: registry -> drivers -> merge -> store -> run log.

Pattern: orchestration service over injected collaborators (C2/C3).
This is the production version of the flow the M4 system test played
by hand. The runner owns *sequencing and failure containment* only:
each driver keeps its own scrape logic, the merge engine keeps its
precedence policy, the repositories keep persistence.

Failure rules (a dead source must say so, never sink the others):
- a kind with no registered driver is a recorded FAILURE run whose
  message is the registry's Missing reason;
- a driver raising an unexpected exception is converted into a
  synthesized FAILURE run naming the exception, and the remaining
  drivers still run;
- either way, every attempted kind leaves exactly one run-log row
  (C10) and the successfully scraped records still merge + persist.
"""

from collections.abc import Callable, Iterable
from datetime import datetime

from school.domain import (
    DriverRecord,
    Found,
    ResourceRecord,
    RunOutcome,
    SourceKind,
    SourceRun,
    StudentRef,
    normalize_text,
)
from school.merge import merge_driver_records
from school.spi import DriverRegistry, ScrapeContext
from school.store import (
    DriverRepository,
    ResourceRepository,
    RunLogRepository,
)


def _resource_natural_key(
    record: ResourceRecord,
) -> tuple[str | None, str | None, str, str, str]:
    """The resource identity the store upserts by (mirrors store's key):
    owner + normalized course + normalized title + URL + source kind."""
    return (
        record.student.id if record.student is not None else None,
        normalize_text(record.course.name)
        if record.course is not None
        else None,
        normalize_text(record.title),
        record.url,
        record.source_kind.value,
    )


def _dedupe_resources(
    records: Iterable[ResourceRecord],
) -> list[ResourceRecord]:
    """One record per natural key; the freshest sighting wins (C10)."""
    freshest: dict[tuple[str | None, str | None, str, str, str], ResourceRecord] = {}
    for record in records:
        key = _resource_natural_key(record)
        current = freshest.get(key)
        if current is None or record.scraped_at >= current.scraped_at:
            freshest[key] = record
    return list(freshest.values())


class ScrapeRunner:
    """Runs registered drivers for one student and persists the results."""

    def __init__(
        self,
        *,
        registry: DriverRegistry,
        driver_repository: DriverRepository,
        resource_repository: ResourceRepository,
        run_log_repository: RunLogRepository,
        clock: Callable[[], datetime],
    ) -> None:
        self._registry = registry
        self._drivers = driver_repository
        self._resources = resource_repository
        self._runs = run_log_repository
        self._clock = clock

    def run(
        self,
        student: StudentRef,
        kinds: Iterable[SourceKind] | None = None,
    ) -> list[SourceRun]:
        """Scrape the selected kinds (default: every registered kind).

        Returns the runs in registry order (kinds ordered by value).
        """
        selected = (
            list(self._registry.kinds())
            if kinds is None
            else sorted(set(kinds), key=lambda kind: kind.value)
        )
        context = ScrapeContext(student=student, now=self._clock())
        sightings: list[DriverRecord] = []
        resources: list[ResourceRecord] = []
        runs: list[SourceRun] = []
        for kind in selected:
            run = self._scrape_one(kind, context, sightings, resources)
            self._runs.append_run(run)
            runs.append(run)
        self._drivers.upsert_drivers(merge_driver_records(sightings))
        self._resources.upsert_resources(_dedupe_resources(resources))
        return runs

    def _scrape_one(
        self,
        kind: SourceKind,
        context: ScrapeContext,
        sightings: list[DriverRecord],
        resources: list[ResourceRecord],
    ) -> SourceRun:
        """One kind's scrape; any failure becomes its run, never a raise."""
        match self._registry.driver_for(kind):
            case Found(value=driver):
                pass
            case _ as missing:
                return self._failure_run(
                    kind,
                    started_at=context.now,
                    message=missing.reason,
                )
        try:
            result = driver.scrape(context)
        except Exception as exc:  # noqa: BLE001 - containment is the point
            return self._failure_run(
                kind,
                started_at=context.now,
                message=f"driver raised {type(exc).__name__}: {exc}",
            )
        sightings.extend(result.drivers)
        resources.extend(result.resources)
        return result.run

    def _failure_run(
        self, kind: SourceKind, *, started_at: datetime, message: str
    ) -> SourceRun:
        return SourceRun(
            source_kind=kind,
            started_at=started_at,
            finished_at=self._clock(),
            outcome=RunOutcome.FAILURE,
            message=message,
        )
