"""HacDriver: the SourceDriver strategy's outcomes and staleness surface."""

from datetime import timedelta
from pathlib import Path

from school.domain import RunOutcome, SourceKind, StudentRef
from school.driver_hac import HacDriver
from school.spi import DriverRegistry, ScrapeContext, SourceDriver
from driver_hac.conftest import (
    CACHE_MTIME,
    FINISH,
    NOW,
    SAM,
    make_driver,
    stage_cache,
)

AIDEN = StudentRef(id="aiden", display_name="Aiden")


def _context(student: StudentRef = SAM) -> ScrapeContext:
    return ScrapeContext(student=student, now=NOW)


def test_driver_satisfies_the_protocol(tmp_path: Path) -> None:
    driver = make_driver(stage_cache(tmp_path, "cache_full.json"))
    assert isinstance(driver, SourceDriver)
    assert driver.kind is SourceKind.HAC


def test_scrape_success(tmp_path: Path) -> None:
    driver = make_driver(stage_cache(tmp_path, "cache_full.json"))
    result = driver.scrape(_context())
    run = result.run
    assert run.outcome is RunOutcome.SUCCESS
    assert run.source_kind is SourceKind.HAC
    assert run.found == 7
    assert run.message is None
    # Started on the caller's clock, finished on the injected clock.
    assert run.started_at == NOW
    assert run.finished_at == FINISH
    assert len(result.drivers) == 7
    assert result.resources == []
    assert {record.scraped_at for record in result.drivers} == {CACHE_MTIME}
    assert {record.source_kind for record in result.drivers} == {SourceKind.HAC}


def test_scrape_partial_keeps_good_records(tmp_path: Path) -> None:
    driver = make_driver(stage_cache(tmp_path, "cache_partial.json"))
    result = driver.scrape(_context())
    assert result.run.outcome is RunOutcome.PARTIAL
    assert result.run.found == 4
    assert len(result.drivers) == 1
    assert result.drivers[0].title == "Volume of Cylinders Practice"
    message = result.run.message
    assert message is not None
    assert "3 of 4" in message
    assert "no title" in message


def test_scrape_missing_file_is_failure_not_exception(tmp_path: Path) -> None:
    driver = make_driver(tmp_path / "does-not-exist.json")
    result = driver.scrape(_context())
    assert result.run.outcome is RunOutcome.FAILURE
    assert result.run.found == 0
    assert result.drivers == []
    assert result.run.message is not None
    assert "cannot read HAC cache" in result.run.message


def test_scrape_corrupt_json_is_failure(tmp_path: Path) -> None:
    driver = make_driver(stage_cache(tmp_path, "cache_corrupt.json"))
    result = driver.scrape(_context())
    assert result.run.outcome is RunOutcome.FAILURE
    assert result.run.message is not None
    assert "not valid JSON" in result.run.message


def test_scrape_cache_without_courses_is_success_with_zero_found(
    tmp_path: Path,
) -> None:
    driver = make_driver(stage_cache(tmp_path, "cache_empty.json"))
    result = driver.scrape(_context())
    assert result.run.outcome is RunOutcome.SUCCESS
    assert result.run.found == 0
    assert result.drivers == []
    assert result.run.message is not None
    assert "no 'courses' list" in result.run.message


def test_scrape_for_the_wrong_student_fails_loudly(tmp_path: Path) -> None:
    driver = make_driver(stage_cache(tmp_path, "cache_full.json"))
    result = driver.scrape(_context(student=AIDEN))
    assert result.run.outcome is RunOutcome.FAILURE
    assert result.drivers == []
    message = result.run.message
    assert message is not None
    assert "'sam'" in message
    assert "'aiden'" in message


def test_cache_age_against_context_clock(tmp_path: Path) -> None:
    driver = make_driver(stage_cache(tmp_path, "cache_full.json"))
    assert driver.cache_age(_context()) == NOW - CACHE_MTIME
    assert driver.cache_age(_context()) == timedelta(hours=23, minutes=47)


def test_cache_age_unknown_when_file_missing(tmp_path: Path) -> None:
    driver = make_driver(tmp_path / "does-not-exist.json")
    assert driver.cache_age(_context()) is None


def test_driver_runs_through_the_registry(tmp_path: Path) -> None:
    driver = make_driver(stage_cache(tmp_path, "cache_full.json"))
    registry = DriverRegistry({SourceKind.HAC: driver})
    resolved = registry.driver_for(SourceKind.HAC).unwrap()
    assert isinstance(resolved, HacDriver)
    result = resolved.scrape(_context())
    assert result.run.outcome is RunOutcome.SUCCESS
    assert result.run.found == 7
