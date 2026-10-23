"""Shared fixtures for the HAC driver tests.

Everything runs on tmp_path copies of the JSON fixtures in ./fixtures —
the tests never touch the real cache on hal9k. The staged copy's mtime
is pinned to CACHE_MTIME so scraped_at assertions are exact.
"""

import os
import shutil
from datetime import date, datetime, timezone
from pathlib import Path

from school.domain import StudentRef
from school.driver_hac import HacCacheAdapter, HacDriver

FIXTURES = Path(__file__).parent / "fixtures"

SAM = StudentRef(id="sam", display_name="Sam")
NOW = datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)
FINISH = datetime(2026, 10, 22, 18, 5, tzinfo=timezone.utc)
CACHE_MTIME = datetime(2026, 10, 21, 18, 13, tzinfo=timezone.utc)
# The reference "today" for due-relative status derivation; matches
# NOW's calendar date, which is what the driver threads into load().
REFERENCE_DATE = date(2026, 10, 22)


def stage_cache(tmp_path: Path, name: str) -> Path:
    """Copy a fixture into tmp_path with its mtime pinned to CACHE_MTIME."""
    target = tmp_path / name
    shutil.copyfile(FIXTURES / name, target)
    stamp = CACHE_MTIME.timestamp()
    os.utime(target, (stamp, stamp))
    return target


def make_adapter(path: Path, *, student: StudentRef = SAM) -> HacCacheAdapter:
    """An adapter over ``path`` with a fixed clock (FINISH)."""
    return HacCacheAdapter(
        path_provider=lambda: path,
        clock=lambda: FINISH,
        student=student,
    )


def make_driver(path: Path, *, student: StudentRef = SAM) -> HacDriver:
    return HacDriver(make_adapter(path, student=student))
