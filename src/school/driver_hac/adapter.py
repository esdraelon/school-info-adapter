"""HAC cache access: the driver's I/O boundary.

Pattern: Adapter — adapts the grades MCP's cache file (an external,
foreign artifact) to this system's domain types. Every file and clock
access the driver needs lives behind this class's injected callables
(cache-path provider, clock, text reader), so tests run entirely on
injected fakes and tmp fixtures — never the real cache (C2/C3).

Freshness contract (C10): a snapshot's records all carry the cache
file's mtime as their ``scraped_at`` — that is when the grades scraper
actually produced the data, and downstream health reads it as fact. The
injected clock is used only when no file time exists (e.g. a reader
injected over a non-file source); "now" is never invented while a file
time is available.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from school.domain import DriverRecord, Found, Missing, StudentRef
from school.driver_hac import mapping


class CacheLoadError(Exception):
    """The cache could not be read or parsed.

    The driver converts this into a FAILURE SourceRun; it never escapes
    a scrape as an exception (per the SourceDriver contract).
    """


@dataclass(frozen=True, kw_only=True)
class CacheSnapshot:
    """One read of the cache, fully mapped (see HacCacheAdapter.load).

    ``skipped`` holds the per-item reasons (from the mapping layer) for
    items that could not be mapped; ``total_items`` is records + skipped.
    ``student_found`` is False when the cache has no node for the target
    student — a successful read with nothing to map, so ``note`` carries
    the explanation instead of a failure.
    """

    scraped_at: datetime
    records: tuple[DriverRecord, ...]
    skipped: tuple[str, ...]
    total_items: int
    student_found: bool
    note: str | None = None


def read_cache_text(path: Path) -> str:
    """The default cache reader: a plain UTF-8 file read."""
    return path.read_text(encoding="utf-8")


class HacCacheAdapter:
    """Reads and maps the grades cache for one target student.

    Pattern: Adapter. Constructed with injected dependencies only:
    ``path_provider`` locates the cache file, ``clock`` supplies the
    fallback timestamp, ``reader`` fetches cache text (defaults to a
    JSON file read; tests inject fixture text).
    """

    def __init__(
        self,
        *,
        path_provider: Callable[[], Path],
        clock: Callable[[], datetime],
        student: StudentRef,
        reader: Callable[[Path], str] = read_cache_text,
    ) -> None:
        self._path_provider = path_provider
        self._clock = clock
        self._student = student
        self._reader = reader

    @property
    def student(self) -> StudentRef:
        """The student this adapter is configured to extract."""
        return self._student

    def now(self) -> datetime:
        """The injected clock's current time (run stamps)."""
        return self._clock()

    def cache_timestamp(self) -> datetime | None:
        """The cache file's mtime (UTC), or None when it cannot be
        stat'ed — the cheap staleness probe behind driver.cache_age."""
        return self._mtime(self._path_provider())

    def _mtime(self, path: Path) -> datetime | None:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None
        return datetime.fromtimestamp(mtime, tz=timezone.utc)

    def load(self) -> CacheSnapshot:
        """Read, parse, and map the cache for the target student.

        Raises CacheLoadError when the file is missing/unreadable,
        invalid JSON, or a JSON shape that cannot be a cache at all.
        A student absent from a well-formed cache is NOT an error (see
        CacheSnapshot.student_found).
        """
        path = self._path_provider()
        try:
            text = self._reader(path)
        except OSError as exc:
            raise CacheLoadError(f"cannot read HAC cache at {path}: {exc}") from exc
        try:
            payload: object = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CacheLoadError(
                f"HAC cache at {path} is not valid JSON: {exc}"
            ) from exc
        if not isinstance(payload, (Mapping, list)):
            raise CacheLoadError(
                f"HAC cache at {path} has an unrecognized shape "
                f"({type(payload).__name__}); expected an object or list"
            )
        scraped_at = self._mtime(path)
        if scraped_at is None:
            scraped_at = self._clock()
        return self._snapshot(payload, scraped_at)

    def _snapshot(self, payload: object, scraped_at: datetime) -> CacheSnapshot:
        match mapping.extract_student_items(payload, self._student.id):
            case Missing(reason=reason):
                return CacheSnapshot(
                    scraped_at=scraped_at,
                    records=(),
                    skipped=(),
                    total_items=0,
                    student_found=False,
                    note=reason,
                )
            case Found(value=raw_items):
                pass
        records: list[DriverRecord] = []
        skipped: list[str] = []
        for raw in raw_items:
            match mapping.map_item(
                raw, student=self._student, scraped_at=scraped_at
            ):
                case Found(value=record):
                    records.append(record)
                case Missing(reason=reason):
                    skipped.append(reason)
        return CacheSnapshot(
            scraped_at=scraped_at,
            records=tuple(records),
            skipped=tuple(skipped),
            total_items=len(raw_items),
            student_found=True,
        )
