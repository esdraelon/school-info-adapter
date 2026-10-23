"""HacCacheAdapter: I/O boundary behavior over tmp fixture copies."""

import json
from datetime import timedelta
from pathlib import Path

import pytest

from school.domain import CourseRef, ItemStatus
from school.driver_hac import CacheLoadError, HacCacheAdapter
from driver_hac.conftest import (
    CACHE_MTIME,
    FINISH,
    REFERENCE_DATE,
    SAM,
    make_adapter,
    stage_cache,
)


def test_load_full_cache_with_default_reader(tmp_path: Path) -> None:
    path = stage_cache(tmp_path, "cache_full.json")
    snapshot = make_adapter(path).load(reference_date=REFERENCE_DATE)
    assert snapshot.student_found
    assert snapshot.total_items == 7
    assert snapshot.skipped == ()
    assert len(snapshot.records) == 7
    # Freshness is data: every record carries the file's mtime (C10).
    assert snapshot.scraped_at == CACHE_MTIME
    assert {record.scraped_at for record in snapshot.records} == {CACHE_MTIME}
    # The configured student is the authority (the file's student_id
    # is null and ignored).
    assert {record.student for record in snapshot.records} == {SAM}
    statuses = {record.title: record.status for record in snapshot.records}
    assert statuses == {
        "Literary Essay Unit Assessment": ItemStatus.GRADED,
        "Vocabulary Quiz 4": ItemStatus.MISSING,
        "Reading Log Week 8": ItemStatus.ASSIGNED,
        "Timeline Project": ItemStatus.GRADED,
        "Map Skills Check": ItemStatus.GRADED,
        "Practice Record Week 8": ItemStatus.ASSIGNED,
        "Sectional Attendance": ItemStatus.MISSING,
    }
    scores = {record.title: record.score_text for record in snapshot.records}
    assert scores["Literary Essay Unit Assessment"] == "80"
    assert scores["Timeline Project"] == "87.77"
    assert scores["Map Skills Check"] == "0"  # a 0.0 score is GRADED
    assert scores["Vocabulary Quiz 4"] is None
    # Cache items have no id anywhere.
    assert {record.source_ref for record in snapshot.records} == {None}


def test_load_parses_course_prefixes(tmp_path: Path) -> None:
    path = stage_cache(tmp_path, "cache_full.json")
    snapshot = make_adapter(path).load(reference_date=REFERENCE_DATE)
    courses = {record.title: record.course for record in snapshot.records}
    assert courses["Literary Essay Unit Assessment"] == CourseRef(
        name="Language Arts 8", period="P1"
    )
    assert courses["Timeline Project"] == CourseRef(
        name="Social Studies 8 Advanced", period="P4"
    )
    # A course without the code prefix keeps its whole name.
    assert courses["Practice Record Week 8"] == CourseRef(name="Wind Ensemble")


def test_load_statuses_follow_the_reference_date(tmp_path: Path) -> None:
    """The same cache reads differently on a later reference date:
    the unscored 10/26 items flip from ASSIGNED to MISSING."""
    path = stage_cache(tmp_path, "cache_full.json")
    snapshot = make_adapter(path).load(
        reference_date=REFERENCE_DATE + timedelta(days=7)
    )
    statuses = {record.title: record.status for record in snapshot.records}
    assert statuses["Reading Log Week 8"] is ItemStatus.MISSING
    assert statuses["Practice Record Week 8"] is ItemStatus.MISSING
    assert statuses["Literary Essay Unit Assessment"] is ItemStatus.GRADED


def test_load_partial_cache_counts_skips(tmp_path: Path) -> None:
    path = stage_cache(tmp_path, "cache_partial.json")
    snapshot = make_adapter(path).load(reference_date=REFERENCE_DATE)
    assert snapshot.student_found
    assert snapshot.total_items == 4
    assert len(snapshot.records) == 1
    assert len(snapshot.skipped) == 3
    assert any("no title" in reason for reason in snapshot.skipped)
    assert any("not an object" in reason for reason in snapshot.skipped)
    assert any("no name" in reason for reason in snapshot.skipped)


def test_load_without_courses_list_is_not_an_error(tmp_path: Path) -> None:
    path = stage_cache(tmp_path, "cache_empty.json")
    snapshot = make_adapter(path).load(reference_date=REFERENCE_DATE)
    assert not snapshot.student_found
    assert snapshot.records == ()
    assert snapshot.total_items == 0
    assert snapshot.note is not None
    assert "no 'courses' list" in snapshot.note
    # The read still happened: the snapshot is stamped from the file.
    assert snapshot.scraped_at == CACHE_MTIME


def test_load_missing_file_raises_cache_load_error(tmp_path: Path) -> None:
    adapter = make_adapter(tmp_path / "does-not-exist.json")
    with pytest.raises(CacheLoadError, match="cannot read HAC cache"):
        adapter.load(reference_date=REFERENCE_DATE)


def test_load_corrupt_json_raises_cache_load_error(tmp_path: Path) -> None:
    path = stage_cache(tmp_path, "cache_corrupt.json")
    with pytest.raises(CacheLoadError, match="not valid JSON"):
        make_adapter(path).load(reference_date=REFERENCE_DATE)


def test_load_scalar_json_raises_cache_load_error(tmp_path: Path) -> None:
    path = tmp_path / "scalar.json"
    path.write_text("42", encoding="utf-8")
    with pytest.raises(CacheLoadError, match="unrecognized shape"):
        make_adapter(path).load(reference_date=REFERENCE_DATE)


def test_load_list_json_raises_cache_load_error(tmp_path: Path) -> None:
    path = tmp_path / "list.json"
    path.write_text('[{"name": "x"}]', encoding="utf-8")
    with pytest.raises(CacheLoadError, match="unrecognized shape"):
        make_adapter(path).load(reference_date=REFERENCE_DATE)


def test_load_reader_oserror_becomes_cache_load_error(tmp_path: Path) -> None:
    def broken_reader(path: Path) -> str:
        raise PermissionError("denied")

    adapter = HacCacheAdapter(
        path_provider=lambda: tmp_path / "cache.json",
        clock=lambda: FINISH,
        student=SAM,
        reader=broken_reader,
    )
    with pytest.raises(CacheLoadError, match="cannot read HAC cache"):
        adapter.load(reference_date=REFERENCE_DATE)


def test_scraped_at_falls_back_to_clock_when_no_file_time(
    tmp_path: Path,
) -> None:
    """An injected reader over a nonexistent path: no mtime exists, so the
    injected clock stamps the snapshot (never a hidden wall-clock read)."""
    payload = {
        "student_id": None,
        "courses": [
            {
                "name": "1801 - 1    Language Arts 8",
                "average": None,
                "assignments": [{"name": "Workbook", "score": None}],
            }
        ],
    }
    adapter = HacCacheAdapter(
        path_provider=lambda: tmp_path / "virtual.json",
        clock=lambda: FINISH,
        student=SAM,
        reader=lambda path: json.dumps(payload),
    )
    snapshot = adapter.load(reference_date=REFERENCE_DATE)
    assert snapshot.scraped_at == FINISH
    assert snapshot.records[0].scraped_at == FINISH
    assert snapshot.records[0].course == CourseRef(
        name="Language Arts 8", period="P1"
    )


def test_cache_timestamp_reads_mtime(tmp_path: Path) -> None:
    path = stage_cache(tmp_path, "cache_full.json")
    adapter = make_adapter(path)
    assert adapter.cache_timestamp() == CACHE_MTIME
    assert adapter.now() == FINISH
    assert adapter.student is SAM


def test_cache_timestamp_missing_file_is_none(tmp_path: Path) -> None:
    adapter = make_adapter(tmp_path / "does-not-exist.json")
    assert adapter.cache_timestamp() is None
