"""evaluate_health: the never-ran / FAILURE / stale / PARTIAL / OK matrix."""

from datetime import datetime, timedelta, timezone

from school.app.health import HealthStatus, evaluate_health
from school.domain import RunOutcome, SourceKind
from school.mcp_server.services import SourceHealth

NOW = datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)
THRESHOLDS = {SourceKind.HAC: 30.0, SourceKind.AGENDA: 192.0}


def health(
    kind: SourceKind,
    *,
    outcome: RunOutcome | None = None,
    finished_hours_ago: float | None = None,
    message: str | None = None,
) -> SourceHealth:
    finished = (
        NOW - timedelta(hours=finished_hours_ago)
        if finished_hours_ago is not None
        else None
    )
    return SourceHealth(
        source_kind=kind,
        last_outcome=outcome,
        last_finished_at=finished,
        age_seconds=(
            finished_hours_ago * 3600.0
            if finished_hours_ago is not None
            else None
        ),
        message=message,
    )


def only_status(
    entries: list[SourceHealth],
    thresholds: dict[SourceKind, float] = THRESHOLDS,
) -> HealthStatus:
    report = evaluate_health(entries, thresholds, NOW)
    assert len(report.entries) == 1
    return report.entries[0].status


def test_never_ran_is_unhealthy() -> None:
    report = evaluate_health([health(SourceKind.HAC)], THRESHOLDS, NOW)
    entry = report.entries[0]
    assert entry.status is HealthStatus.UNHEALTHY
    assert "never run" in entry.reason
    assert report.ok is False


def test_failed_latest_run_is_unhealthy_with_its_message() -> None:
    entry = evaluate_health(
        [
            health(
                SourceKind.HAC,
                outcome=RunOutcome.FAILURE,
                finished_hours_ago=1.0,
                message="cache unreadable",
            )
        ],
        THRESHOLDS,
        NOW,
    ).entries[0]
    assert entry.status is HealthStatus.UNHEALTHY
    assert "failed" in entry.reason
    assert "cache unreadable" in entry.reason


def test_stale_success_is_unhealthy() -> None:
    entry = evaluate_health(
        [
            health(
                SourceKind.HAC,
                outcome=RunOutcome.SUCCESS,
                finished_hours_ago=31.0,
            )
        ],
        THRESHOLDS,
        NOW,
    ).entries[0]
    assert entry.status is HealthStatus.UNHEALTHY
    assert "stale" in entry.reason
    assert "30h" in entry.reason


def test_exactly_at_threshold_is_still_ok() -> None:
    assert (
        only_status(
            [
                health(
                    SourceKind.HAC,
                    outcome=RunOutcome.SUCCESS,
                    finished_hours_ago=30.0,
                )
            ]
        )
        is HealthStatus.OK
    )


def test_fresh_success_is_ok() -> None:
    report = evaluate_health(
        [
            health(
                SourceKind.AGENDA,
                outcome=RunOutcome.SUCCESS,
                finished_hours_ago=2.0,
            )
        ],
        THRESHOLDS,
        NOW,
    )
    assert report.entries[0].status is HealthStatus.OK
    assert report.ok is True


def test_fresh_partial_is_a_warning_not_a_failure() -> None:
    report = evaluate_health(
        [
            health(
                SourceKind.AGENDA,
                outcome=RunOutcome.PARTIAL,
                finished_hours_ago=1.0,
                message="one deck failed",
            )
        ],
        THRESHOLDS,
        NOW,
    )
    entry = report.entries[0]
    assert entry.status is HealthStatus.WARNING
    assert "partial" in entry.reason
    assert "one deck failed" in entry.reason
    assert report.ok is True  # warnings do not fail the report


def test_stale_partial_is_unhealthy_not_a_warning() -> None:
    assert (
        only_status(
            [
                health(
                    SourceKind.HAC,
                    outcome=RunOutcome.PARTIAL,
                    finished_hours_ago=100.0,
                )
            ]
        )
        is HealthStatus.UNHEALTHY
    )


def test_kind_without_a_threshold_gets_no_staleness_verdict() -> None:
    assert (
        only_status(
            [
                health(
                    SourceKind.CLASSROOM_WEB,
                    outcome=RunOutcome.SUCCESS,
                    finished_hours_ago=100_000.0,
                )
            ]
        )
        is HealthStatus.OK
    )


def test_report_ok_requires_no_unhealthy_entries() -> None:
    fresh = health(
        SourceKind.AGENDA,
        outcome=RunOutcome.SUCCESS,
        finished_hours_ago=1.0,
    )
    partial = health(
        SourceKind.AGENDA,
        outcome=RunOutcome.PARTIAL,
        finished_hours_ago=1.0,
    )
    dead = health(SourceKind.HAC)
    assert evaluate_health([fresh, partial], THRESHOLDS, NOW).ok is True
    assert evaluate_health([fresh, dead], THRESHOLDS, NOW).ok is False
    assert evaluate_health([], THRESHOLDS, NOW).ok is True
