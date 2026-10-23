"""QueryServices against the real store repositories (no mocks)."""

from datetime import datetime, timedelta, timezone

import pytest

from school.domain import (
    DriverKind,
    DriverRecord,
    ItemStatus,
    ResourceKind,
    ResourceRecord,
    RunOutcome,
    SourceKind,
    SourceRun,
)
from school.mcp_server.services import QueryServices
from school.store import DriverRepository, ResourceRepository, RunLogRepository
from mcp_server.conftest import (
    MATH,
    NOW,
    OWEN,
    SAM,
    SCIENCE,
    SOCIAL,
    make_driver,
    make_resource,
)


def _due(day: int) -> datetime:
    return datetime(2026, 10, day, tzinfo=timezone.utc)


@pytest.fixture
def seeded_drivers(drivers_repo: DriverRepository) -> None:
    drivers_repo.upsert_drivers(
        [
            make_driver(
                title="Quiz: Pythagorean Theorem",
                kind=DriverKind.QUIZ,
                due_at=_due(26),
            ),
            make_driver(
                title="Timeline Project",
                course=SOCIAL,
                kind=DriverKind.PROJECT,
                status=ItemStatus.GRADED,
                due_at=_due(23),
                score_text="87.77",
                source_kind=SourceKind.HAC,
            ),
            make_driver(
                title="Workbook Check",
                status=ItemStatus.MISSING,
                due_at=_due(16),
                source_kind=SourceKind.HAC,
            ),
            make_driver(title="Reading Log", due_at=None),
            make_driver(
                title="Owen Quiz",
                student=OWEN,
                kind=DriverKind.QUIZ,
                due_at=_due(26),
                source_kind=SourceKind.HAC,
            ),
        ]
    )


def _titles(
    records: list[DriverRecord] | list[ResourceRecord],
) -> set[str]:
    return {r.title for r in records}


# --- list_drivers -------------------------------------------------------


def test_list_drivers_returns_only_the_student(
    services: QueryServices, seeded_drivers: None
) -> None:
    records = services.list_drivers("sam")
    assert _titles(records) == {
        "Quiz: Pythagorean Theorem",
        "Timeline Project",
        "Workbook Check",
        "Reading Log",
    }


def test_list_drivers_course_filter(
    services: QueryServices, seeded_drivers: None
) -> None:
    records = services.list_drivers("sam", course_name="Math 8")
    assert _titles(records) == {
        "Quiz: Pythagorean Theorem",
        "Workbook Check",
        "Reading Log",
    }


def test_list_drivers_status_filter(
    services: QueryServices, seeded_drivers: None
) -> None:
    graded = services.list_drivers("sam", status=ItemStatus.GRADED)
    assert _titles(graded) == {"Timeline Project"}
    assert graded[0].score_text == "87.77"
    missing = services.list_drivers("sam", status=ItemStatus.MISSING)
    assert _titles(missing) == {"Workbook Check"}


def test_list_drivers_due_window_excludes_undated(
    services: QueryServices, seeded_drivers: None
) -> None:
    before = services.list_drivers("sam", due_on_or_before=_due(23))
    assert _titles(before) == {"Timeline Project", "Workbook Check"}
    after = services.list_drivers("sam", due_on_or_after=_due(24))
    assert _titles(after) == {"Quiz: Pythagorean Theorem"}
    window = services.list_drivers(
        "sam", due_on_or_after=_due(20), due_on_or_before=_due(27)
    )
    assert _titles(window) == {"Timeline Project", "Quiz: Pythagorean Theorem"}


def test_list_drivers_limit(
    services: QueryServices, seeded_drivers: None
) -> None:
    records = services.list_drivers("sam", limit=2)
    # Repository order: dated first by due date, undated last.
    assert [r.title for r in records] == ["Workbook Check", "Timeline Project"]
    assert services.list_drivers("sam", limit=0) == []


def test_list_drivers_unknown_student_is_empty(
    services: QueryServices, seeded_drivers: None
) -> None:
    assert services.list_drivers("nobody") == []


# --- list_resources -----------------------------------------------------


@pytest.fixture
def seeded_resources(resources_repo: ResourceRepository) -> None:
    resources_repo.upsert_resources(
        [
            make_resource(title="Volume notes", url="https://x.test/1"),
            make_resource(
                title="Tutorial schedule",
                url="https://x.test/2",
                student=None,
                course=None,
                kind=ResourceKind.TUTORIAL,
                source_kind=SourceKind.TEAM_SITE,
            ),
            make_resource(
                title="Shared math notes",
                url="https://x.test/3",
                student=None,
                source_kind=SourceKind.TEAM_SITE,
            ),
            make_resource(
                title="Owen notes", url="https://x.test/4", student=OWEN
            ),
            make_resource(
                title="Science doc",
                url="https://x.test/5",
                course=SCIENCE,
                kind=ResourceKind.DOCUMENT,
                source_kind=SourceKind.HAC,
            ),
        ]
    )


def test_list_resources_unions_own_and_shared(
    services: QueryServices, seeded_resources: None
) -> None:
    records = services.list_resources("sam")
    assert _titles(records) == {
        "Volume notes",
        "Tutorial schedule",
        "Shared math notes",
        "Science doc",
    }


def test_list_resources_course_filter_keeps_courseless_shared(
    services: QueryServices, seeded_resources: None
) -> None:
    records = services.list_resources("sam", course_name="Math 8")
    assert _titles(records) == {
        "Volume notes",
        "Tutorial schedule",
        "Shared math notes",
    }
    science = services.list_resources("sam", course_name="Science 8")
    assert _titles(science) == {"Tutorial schedule", "Science doc"}


def test_list_resources_no_student_sees_everything(
    services: QueryServices, seeded_resources: None
) -> None:
    assert len(services.list_resources()) == 5
    assert len(services.list_resources(None, course_name="Math 8")) == 4


def test_list_resources_kind_filter_and_limit(
    services: QueryServices, seeded_resources: None
) -> None:
    notes = services.list_resources("sam", kind=ResourceKind.NOTES)
    assert _titles(notes) == {"Volume notes", "Shared math notes"}
    tutorials = services.list_resources("sam", kind=ResourceKind.TUTORIAL)
    assert _titles(tutorials) == {"Tutorial schedule"}
    assert len(services.list_resources("sam", limit=2)) == 2


# --- source_health ------------------------------------------------------


def _run(
    kind: SourceKind,
    finished: datetime,
    outcome: RunOutcome,
    message: str | None = None,
) -> SourceRun:
    return SourceRun(
        source_kind=kind,
        started_at=finished - timedelta(minutes=1),
        finished_at=finished,
        outcome=outcome,
        message=message,
    )


def test_source_health_no_runs(services: QueryServices) -> None:
    health = services.source_health(NOW)
    assert [h.source_kind for h in health] == list(SourceKind)
    for entry in health:
        assert entry.last_outcome is None
        assert entry.last_finished_at is None
        assert entry.age_seconds is None
        assert entry.message is None


def test_source_health_latest_run_and_age_math(
    services: QueryServices, runs_repo: RunLogRepository
) -> None:
    t_old = NOW - timedelta(hours=2)
    t_new = NOW - timedelta(minutes=30)
    runs_repo.append_run(_run(SourceKind.HAC, t_old, RunOutcome.FAILURE, "boom"))
    runs_repo.append_run(_run(SourceKind.HAC, t_new, RunOutcome.SUCCESS))
    runs_repo.append_run(
        _run(SourceKind.AGENDA, t_new, RunOutcome.PARTIAL, "1 deck failed")
    )
    by_kind = {h.source_kind: h for h in services.source_health(NOW)}
    hac = by_kind[SourceKind.HAC]
    assert hac.last_outcome is RunOutcome.SUCCESS  # latest wins, not the failure
    assert hac.last_finished_at == t_new
    assert hac.age_seconds == 1800.0
    assert hac.message is None
    agenda = by_kind[SourceKind.AGENDA]
    assert agenda.last_outcome is RunOutcome.PARTIAL
    assert agenda.message == "1 deck failed"
    assert agenda.age_seconds == 1800.0
    assert by_kind[SourceKind.TEAM_SITE].last_outcome is None


def test_services_now_uses_the_injected_clock(
    services: QueryServices,
) -> None:
    assert services.now() == NOW
