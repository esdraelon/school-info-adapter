"""ScrapeRunner: real registry + real repositories, stub drivers only.

The drivers are the runner's immediate collaborators, so they are the
one faked seam (C7 applies to system tests); merge, store, and run log
are the production code.
"""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import pytest

from school.app.scrape_runner import ScrapeRunner
from school.domain import (
    CourseRef,
    DriverKind,
    DriverRecord,
    Found,
    ItemStatus,
    Missing,
    ResourceKind,
    ResourceRecord,
    RunOutcome,
    SourceKind,
    SourceRun,
    StudentRef,
)
from school.spi import DriverRegistry, ScrapeContext, ScrapeResult
from school.store import (
    DriverRepository,
    ResourceRepository,
    RunLogRepository,
)
from tests.unit.app.conftest import NOW, SAM

MATH = CourseRef(name="Math 8")
DUE = datetime(2026, 10, 26, 5, 0, tzinfo=timezone.utc)


class StubDriver:
    """A SourceDriver that returns a canned result or raises."""

    def __init__(
        self,
        kind: SourceKind,
        *,
        result: ScrapeResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self._kind = kind
        self._result = result
        self._error = error

    @property
    def kind(self) -> SourceKind:
        return self._kind

    def scrape(self, context: ScrapeContext) -> ScrapeResult:
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def run_for(
    kind: SourceKind, outcome: RunOutcome = RunOutcome.SUCCESS
) -> SourceRun:
    return SourceRun(
        source_kind=kind,
        started_at=NOW,
        finished_at=NOW,
        outcome=outcome,
        found=1,
    )


def driver_record(
    source: SourceKind, status: ItemStatus, scraped_at: datetime = NOW
) -> DriverRecord:
    return DriverRecord(
        student=SAM,
        course=MATH,
        title="Quiz: Pythagorean Theorem",
        kind=DriverKind.QUIZ,
        status=status,
        source_kind=source,
        scraped_at=scraped_at,
        due_at=DUE,
    )


def resource(scraped_at: datetime) -> ResourceRecord:
    return ResourceRecord(
        title="Pythagorean notes",
        kind=ResourceKind.NOTES,
        url="https://example.test/notes",
        source_kind=SourceKind.AGENDA,
        scraped_at=scraped_at,
        course=MATH,
    )


RunnerAndRepos = tuple[
    ScrapeRunner, DriverRepository, ResourceRepository, RunLogRepository
]
MakeRunner = Callable[[dict[SourceKind, StubDriver]], RunnerAndRepos]


@pytest.fixture
def make_runner(
    driver_repository: DriverRepository,
    resource_repository: ResourceRepository,
    run_log_repository: RunLogRepository,
) -> MakeRunner:
    def build(drivers: dict[SourceKind, StubDriver]) -> RunnerAndRepos:
        runner = ScrapeRunner(
            registry=DriverRegistry(drivers),
            driver_repository=driver_repository,
            resource_repository=resource_repository,
            run_log_repository=run_log_repository,
            clock=lambda: NOW,
        )
        return (
            runner,
            driver_repository,
            resource_repository,
            run_log_repository,
        )

    return build


def test_successful_run_merges_persists_and_logs(
    make_runner: MakeRunner,
) -> None:
    runner, drivers_repo, resources_repo, runs_repo = make_runner(
        {
            SourceKind.AGENDA: StubDriver(
                SourceKind.AGENDA,
                result=ScrapeResult(
                    run=run_for(SourceKind.AGENDA),
                    drivers=[driver_record(SourceKind.AGENDA, ItemStatus.ASSIGNED)],
                    resources=[resource(NOW), resource(NOW + timedelta(hours=1))],
                ),
            ),
            SourceKind.HAC: StubDriver(
                SourceKind.HAC,
                result=ScrapeResult(
                    run=run_for(SourceKind.HAC),
                    drivers=[driver_record(SourceKind.HAC, ItemStatus.GRADED)],
                ),
            ),
        }
    )
    runs = runner.run(SAM)
    # Registry order (by kind value): agenda before hac.
    assert [run.source_kind for run in runs] == [
        SourceKind.AGENDA,
        SourceKind.HAC,
    ]
    assert all(run.outcome is RunOutcome.SUCCESS for run in runs)
    # The two sightings of the same item merged into one stored record;
    # HAC outranks AGENDA, so the merged record is HAC's GRADED sighting.
    stored = drivers_repo.list_drivers(student_id="sam")
    assert len(stored) == 1
    assert stored[0].source_kind is SourceKind.HAC
    assert stored[0].status is ItemStatus.GRADED
    # The duplicated resource (same natural key) persisted once, freshest.
    stored_resources = resources_repo.list_resources()
    assert len(stored_resources) == 1
    assert stored_resources[0].scraped_at == NOW + timedelta(hours=1)
    # Every kind left a run-log row.
    for kind in (SourceKind.AGENDA, SourceKind.HAC):
        assert isinstance(runs_repo.latest_run(kind), Found)


def test_raising_driver_becomes_failure_run_and_others_continue(
    make_runner: MakeRunner,
) -> None:
    runner, drivers_repo, _, runs_repo = make_runner(
        {
            SourceKind.AGENDA: StubDriver(
                SourceKind.AGENDA, error=RuntimeError("deck exploded")
            ),
            SourceKind.HAC: StubDriver(
                SourceKind.HAC,
                result=ScrapeResult(
                    run=run_for(SourceKind.HAC),
                    drivers=[driver_record(SourceKind.HAC, ItemStatus.GRADED)],
                ),
            ),
        }
    )
    runs = runner.run(SAM)
    by_kind = {run.source_kind: run for run in runs}
    failure = by_kind[SourceKind.AGENDA]
    assert failure.outcome is RunOutcome.FAILURE
    assert failure.message is not None
    assert "RuntimeError" in failure.message
    assert "deck exploded" in failure.message
    assert by_kind[SourceKind.HAC].outcome is RunOutcome.SUCCESS
    # The healthy driver's records still landed, and the failure is logged.
    assert len(drivers_repo.list_drivers(student_id="sam")) == 1
    match runs_repo.latest_run(SourceKind.AGENDA):
        case Found(value=logged):
            assert logged.outcome is RunOutcome.FAILURE
        case _:
            pytest.fail("agenda failure run was not logged")


def test_unregistered_kind_is_a_recorded_failure_not_a_crash(
    make_runner: MakeRunner,
) -> None:
    runner, _, _, runs_repo = make_runner(
        {
            SourceKind.HAC: StubDriver(
                SourceKind.HAC, result=ScrapeResult(run=run_for(SourceKind.HAC))
            )
        }
    )
    runs = runner.run(SAM, kinds=[SourceKind.CLASSROOM_WEB])
    assert len(runs) == 1
    assert runs[0].source_kind is SourceKind.CLASSROOM_WEB
    assert runs[0].outcome is RunOutcome.FAILURE
    assert runs[0].message is not None
    assert "no source driver registered" in runs[0].message
    match runs_repo.latest_run(SourceKind.CLASSROOM_WEB):
        case Found(value=logged):
            assert logged.outcome is RunOutcome.FAILURE
        case _:
            pytest.fail("unregistered-kind failure was not logged")
    # ...and the registered driver was not touched by the subset run.
    assert isinstance(runs_repo.latest_run(SourceKind.HAC), Missing)


def test_subset_run_only_scrapes_the_named_kinds(
    make_runner: MakeRunner,
) -> None:
    runner, _, _, runs_repo = make_runner(
        {
            SourceKind.AGENDA: StubDriver(
                SourceKind.AGENDA,
                result=ScrapeResult(run=run_for(SourceKind.AGENDA)),
            ),
            SourceKind.HAC: StubDriver(
                SourceKind.HAC, result=ScrapeResult(run=run_for(SourceKind.HAC))
            ),
        }
    )
    runs = runner.run(SAM, kinds=[SourceKind.HAC])
    assert [run.source_kind for run in runs] == [SourceKind.HAC]
    assert isinstance(runs_repo.latest_run(SourceKind.AGENDA), Missing)


def test_student_with_no_drivers_registered_runs_empty(
    driver_repository: DriverRepository,
    resource_repository: ResourceRepository,
    run_log_repository: RunLogRepository,
) -> None:
    runner = ScrapeRunner(
        registry=DriverRegistry({}),
        driver_repository=driver_repository,
        resource_repository=resource_repository,
        run_log_repository=run_log_repository,
        clock=lambda: NOW,
    )
    assert runner.run(StudentRef(id="nobody", display_name="Nobody")) == []
