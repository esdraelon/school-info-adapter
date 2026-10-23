"""HacCacheAdapter: I/O boundary behavior over tmp fixture copies."""

import json
from pathlib import Path

import pytest

from school.domain import ItemStatus, StudentRef
from school.driver_hac import CacheLoadError, HacCacheAdapter
from driver_hac.conftest import (
    CACHE_MTIME,
    FINISH,
    SAM,
    make_adapter,
    stage_cache,
)

AIDEN = StudentRef(id="aiden", display_name="Aiden")


def test_load_full_cache_with_default_reader(tmp_path: Path) -> None:
    path = stage_cache(tmp_path, "cache_full.json")
    snapshot = make_adapter(path).load()
    assert snapshot.student_found
    assert snapshot.total_items == 6
    assert snapshot.skipped == ()
    assert len(snapshot.records) == 6
    # Freshness is data: every record carries the file's mtime (C10).
    assert snapshot.scraped_at == CACHE_MTIME
    assert {record.scraped_at for record in snapshot.records} == {CACHE_MTIME}
    statuses = {record.title: record.status for record in snapshot.records}
    assert statuses == {
        "Pythagorean Theorem Workbook": ItemStatus.ASSIGNED,
        "Scale Run Through with Chromatic": ItemStatus.GRADED,
        "Newton's 2nd Law Calculations": ItemStatus.MISSING,
        "Spoon River 155-170": ItemStatus.MISSING,
        "RTR Timeline Project": ItemStatus.GRADED,
        "Practice Record Week 2-5": ItemStatus.ASSIGNED,
    }


def test_load_only_extracts_the_target_student(tmp_path: Path) -> None:
    path = stage_cache(tmp_path, "cache_full.json")
    snapshot = make_adapter(path).load()
    assert {record.student for record in snapshot.records} == {SAM}
    assert all(
        record.course.name != "Biology" for record in snapshot.records
    )


def test_load_partial_cache_counts_skips(tmp_path: Path) -> None:
    path = stage_cache(tmp_path, "cache_partial.json")
    snapshot = make_adapter(path).load()
    assert snapshot.student_found
    assert snapshot.total_items == 4
    assert len(snapshot.records) == 1
    assert len(snapshot.skipped) == 3
    assert any("no title" in reason for reason in snapshot.skipped)
    assert any("not an object" in reason for reason in snapshot.skipped)
    assert any("no course name" in reason for reason in snapshot.skipped)


def test_load_student_absent_is_not_an_error(tmp_path: Path) -> None:
    path = stage_cache(tmp_path, "cache_full.json")
    snapshot = make_adapter(path, student=AIDEN).load()
    assert not snapshot.student_found
    assert snapshot.records == ()
    assert snapshot.total_items == 0
    assert snapshot.note is not None
    assert "'aiden' not in cache" in snapshot.note
    # The read still happened: the snapshot is stamped from the file.
    assert snapshot.scraped_at == CACHE_MTIME


def test_load_missing_file_raises_cache_load_error(tmp_path: Path) -> None:
    adapter = make_adapter(tmp_path / "does-not-exist.json")
    with pytest.raises(CacheLoadError, match="cannot read HAC cache"):
        adapter.load()


def test_load_corrupt_json_raises_cache_load_error(tmp_path: Path) -> None:
    path = stage_cache(tmp_path, "cache_corrupt.json")
    with pytest.raises(CacheLoadError, match="not valid JSON"):
        make_adapter(path).load()


def test_load_scalar_json_raises_cache_load_error(tmp_path: Path) -> None:
    path = tmp_path / "scalar.json"
    path.write_text("42", encoding="utf-8")
    with pytest.raises(CacheLoadError, match="unrecognized shape"):
        make_adapter(path).load()


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
        adapter.load()


def test_scraped_at_falls_back_to_clock_when_no_file_time(
    tmp_path: Path,
) -> None:
    """An injected reader over a nonexistent path: no mtime exists, so the
    injected clock stamps the snapshot (never a hidden wall-clock read)."""
    payload = {"sam": [{"course": "Math 8", "title": "Workbook"}]}
    adapter = HacCacheAdapter(
        path_provider=lambda: tmp_path / "virtual.json",
        clock=lambda: FINISH,
        student=SAM,
        reader=lambda path: json.dumps(payload),
    )
    snapshot = adapter.load()
    assert snapshot.scraped_at == FINISH
    assert snapshot.records[0].scraped_at == FINISH


def test_cache_timestamp_reads_mtime(tmp_path: Path) -> None:
    path = stage_cache(tmp_path, "cache_full.json")
    adapter = make_adapter(path)
    assert adapter.cache_timestamp() == CACHE_MTIME
    assert adapter.now() == FINISH
    assert adapter.student is SAM


def test_cache_timestamp_missing_file_is_none(tmp_path: Path) -> None:
    adapter = make_adapter(tmp_path / "does-not-exist.json")
    assert adapter.cache_timestamp() is None
