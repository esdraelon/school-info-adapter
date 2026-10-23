"""RunLogRepository: append + latest per kind, against real SQLite."""

from datetime import datetime, timedelta, timezone

from school.domain import (
    Found,
    Missing,
    RunOutcome,
    SourceKind,
    SourceRun,
)
from school.store import ConnectionFactory, RunLogRepository

UTC = timezone.utc
T0 = datetime(2026, 10, 22, 8, 0, tzinfo=UTC)


def _run(
    kind: SourceKind,
    *,
    started: datetime = T0,
    outcome: RunOutcome = RunOutcome.SUCCESS,
    message: str | None = None,
    found: int = 0,
    new: int = 0,
    updated: int = 0,
) -> SourceRun:
    return SourceRun(
        source_kind=kind,
        started_at=started,
        finished_at=started + timedelta(minutes=1),
        outcome=outcome,
        found=found,
        new=new,
        updated=updated,
        message=message,
    )


def test_append_and_latest_round_trip(
    connection_factory: ConnectionFactory,
) -> None:
    repo = RunLogRepository(connection_factory)
    run = _run(
        SourceKind.HAC,
        outcome=RunOutcome.PARTIAL,
        message="2 classes failed to parse",
        found=17,
        new=3,
        updated=5,
    )
    repo.append_run(run)
    result = repo.latest_run(SourceKind.HAC)
    assert isinstance(result, Found)
    assert result.unwrap() == run


def test_latest_picks_most_recently_finished(
    connection_factory: ConnectionFactory,
) -> None:
    repo = RunLogRepository(connection_factory)
    repo.append_run(_run(SourceKind.HAC, started=T0 + timedelta(hours=2)))
    repo.append_run(_run(SourceKind.HAC, started=T0))  # appended later, older
    repo.append_run(_run(SourceKind.HAC, started=T0 + timedelta(hours=5)))
    latest = repo.latest_run(SourceKind.HAC).unwrap()
    assert latest.started_at == T0 + timedelta(hours=5)


def test_latest_is_per_kind(connection_factory: ConnectionFactory) -> None:
    repo = RunLogRepository(connection_factory)
    repo.append_run(_run(SourceKind.HAC, started=T0))
    repo.append_run(_run(SourceKind.AGENDA, started=T0 + timedelta(hours=9)))
    assert repo.latest_run(SourceKind.HAC).unwrap().started_at == T0
    assert repo.latest_run(SourceKind.AGENDA).unwrap().started_at == (
        T0 + timedelta(hours=9)
    )


def test_latest_without_any_run_is_missing(
    connection_factory: ConnectionFactory,
) -> None:
    repo = RunLogRepository(connection_factory)
    result = repo.latest_run(SourceKind.CLASSROOM_WEB)
    assert isinstance(result, Missing)
    assert "classroom_web" in result.reason
