"""HacDriver: the SourceDriver strategy's outcomes and staleness surface."""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError

import pytest

from school.domain import ItemStatus, RunOutcome, SourceKind, StudentRef
from school.driver_hac import HacDriver
from school.spi import DriverRegistry, ScrapeContext, SourceDriver
from driver_hac.conftest import (
    CACHE_MTIME,
    FINISH,
    NOW,
    SAM,
    make_adapter,
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
    # M7.1: every run message carries the cache-age fact (the
    # fixture's age at NOW is 23h47m -> 23 whole hours).
    assert run.message == "cache_age=23h"
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


def _restamp(path: Path, when: datetime) -> None:
    """Re-pin a staged cache's mtime (see conftest.stage_cache)."""
    stamp = when.timestamp()
    os.utime(path, (stamp, stamp))


def test_stale_cache_downgrades_an_otherwise_successful_run(
    tmp_path: Path,
) -> None:
    """M7.1 FIX A: the upstream grades scraper is dead but the cache
    file still reads fine. The run must not report SUCCESS against
    31h-old data: it downgrades to PARTIAL, names the staleness, and
    the records still flow."""
    path = stage_cache(tmp_path, "cache_full.json")
    _restamp(path, NOW - timedelta(hours=31))
    result = make_driver(path).scrape(_context())
    assert result.run.outcome is RunOutcome.PARTIAL
    assert result.run.found == 7
    assert len(result.drivers) == 7
    message = result.run.message
    assert message is not None
    assert "cache stale" in message
    assert "cache_age=31h" in message


def test_stale_cache_leaves_a_partial_runs_outcome_alone(
    tmp_path: Path,
) -> None:
    """An already-PARTIAL run keeps its outcome and its own
    explanation; only the age fact is appended."""
    path = stage_cache(tmp_path, "cache_partial.json")
    _restamp(path, NOW - timedelta(hours=31))
    result = make_driver(path).scrape(_context())
    assert result.run.outcome is RunOutcome.PARTIAL
    message = result.run.message
    assert message is not None
    assert "3 of 4" in message
    assert "cache stale" not in message
    assert "cache_age=31h" in message


def test_missing_cache_reports_unknown_age(tmp_path: Path) -> None:
    result = make_driver(tmp_path / "does-not-exist.json").scrape(
        _context()
    )
    assert result.run.outcome is RunOutcome.FAILURE
    assert result.run.message is not None
    assert "cache_age=unknown" in result.run.message


def test_stale_after_budget_is_injected(tmp_path: Path) -> None:
    """The standard fixture's age at NOW is 23h47m: inside the
    default 30h budget the scrape succeeds; under a tighter injected
    budget the same cache downgrades."""
    path = stage_cache(tmp_path, "cache_full.json")
    default = make_driver(path).scrape(_context())
    assert default.run.outcome is RunOutcome.SUCCESS
    tight = HacDriver(
        make_adapter(path), stale_after=timedelta(hours=1)
    )
    result = tight.scrape(_context())
    assert result.run.outcome is RunOutcome.PARTIAL
    assert result.run.message is not None
    assert "cache stale" in result.run.message
    assert "cache_age=23h" in result.run.message


def _evening_cache(tmp_path: Path) -> Path:
    """A one-item cache: an unscored worksheet due 10/22/2026."""
    payload = {
        "courses": [
            {
                "name": "1801 - 1    Language Arts 8",
                "assignments": [
                    {
                        "name": "Evening Worksheet",
                        "category": "Daily Work",
                        "date_assigned": "10/21/2026",
                        "date_due": "10/22/2026",
                        "max_points": 100.0,
                        "score": None,
                        "notes": "",
                    }
                ],
            }
        ]
    }
    path = tmp_path / "cache_evening.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _status_at(driver: HacDriver, instant: datetime) -> ItemStatus:
    result = driver.scrape(ScrapeContext(student=SAM, now=instant))
    (record,) = result.drivers
    return record.status


def test_due_today_stays_assigned_through_the_school_evening(
    tmp_path: Path,
) -> None:
    """M7 FIX 2: 2026-10-23T01:30Z is 20:30 CDT on 10-22. A UTC
    reference date (10-23) would flip the item due 10/22 to MISSING
    five hours before the school day ends; the school timezone's
    reference date (10-22) keeps it ASSIGNED."""
    driver = HacDriver(make_adapter(_evening_cache(tmp_path)))
    instant = datetime(2026, 10, 23, 1, 30, tzinfo=timezone.utc)
    assert _status_at(driver, instant) is ItemStatus.ASSIGNED


def test_due_item_flips_only_after_school_midnight(
    tmp_path: Path,
) -> None:
    """2026-10-23T05:30Z is 00:30 CDT on 10-23: now the reference
    date (10-23) is past the 10/22 due date."""
    driver = HacDriver(make_adapter(_evening_cache(tmp_path)))
    instant = datetime(2026, 10, 23, 5, 30, tzinfo=timezone.utc)
    assert _status_at(driver, instant) is ItemStatus.MISSING


def test_explicit_utc_timezone_keeps_the_utc_reference_date(
    tmp_path: Path,
) -> None:
    """The reference instant is explicit: with timezone_name="UTC"
    the same 01:30Z scrape derives against 10-23."""
    driver = HacDriver(
        make_adapter(_evening_cache(tmp_path)), timezone_name="UTC"
    )
    instant = datetime(2026, 10, 23, 1, 30, tzinfo=timezone.utc)
    assert _status_at(driver, instant) is ItemStatus.MISSING


def test_unknown_timezone_fails_at_construction(tmp_path: Path) -> None:
    with pytest.raises(ZoneInfoNotFoundError):
        HacDriver(
            make_adapter(_evening_cache(tmp_path)),
            timezone_name="Mars/Olympus_Mons",
        )


def test_driver_runs_through_the_registry(tmp_path: Path) -> None:
    driver = make_driver(stage_cache(tmp_path, "cache_full.json"))
    registry = DriverRegistry({SourceKind.HAC: driver})
    resolved = registry.driver_for(SourceKind.HAC).unwrap()
    assert isinstance(resolved, HacDriver)
    result = resolved.scrape(_context())
    assert result.run.outcome is RunOutcome.SUCCESS
    assert result.run.found == 7
