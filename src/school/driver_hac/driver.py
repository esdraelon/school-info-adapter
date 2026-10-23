"""HacDriver: the HAC source as an spi.SourceDriver strategy.

Pattern: Strategy (SourceDriver), with the scrape assembled as a
Pipeline composition (C3): state -> load -> assemble. The driver itself
does no I/O and touches no raw cache dicts — the adapter owns the file,
the mapping layer owns the dicts; the driver only turns a CacheSnapshot
into a ScrapeResult with the right run outcome:

- cache unreadable / invalid -> FAILURE (returned, never raised)
- cache read, some items un-mappable -> PARTIAL (good items still flow)
- cache payload with no courses list -> SUCCESS with found=0
- otherwise -> SUCCESS

Staleness (C10) surfaces two ways: every record carries the cache
file's mtime as its scraped_at, and ``cache_age`` exposes that mtime
against the caller's clock for health checks without a full scrape.
"""

from dataclasses import dataclass, replace
from datetime import timedelta

from school.domain import DriverRecord, RunOutcome, SourceKind, SourceRun
from school.driver_hac.adapter import CacheLoadError, CacheSnapshot, HacCacheAdapter
from school.spi import ScrapeContext, ScrapeResult, pipe


@dataclass(frozen=True, kw_only=True)
class _ScrapeState:
    """Pipeline working value: exactly one of snapshot/error gets set."""

    context: ScrapeContext
    snapshot: CacheSnapshot | None = None
    error: str | None = None


class HacDriver:
    """SourceDriver strategy over the grades-MCP cache (via the adapter)."""

    def __init__(self, adapter: HacCacheAdapter) -> None:
        self._adapter = adapter

    @property
    def kind(self) -> SourceKind:
        return SourceKind.HAC

    def scrape(self, context: ScrapeContext) -> ScrapeResult:
        """Scrape the cache once; failures are run outcomes, not raises."""
        if context.student.id != self._adapter.student.id:
            return self._result(
                context,
                outcome=RunOutcome.FAILURE,
                message=(
                    f"HAC driver is configured for student "
                    f"{self._adapter.student.id!r}; scrape context asked "
                    f"for {context.student.id!r}"
                ),
            )
        state = pipe(_ScrapeState(context=context), self._load)
        return self._assemble(state)

    def cache_age(self, context: ScrapeContext) -> timedelta | None:
        """How old the cache file is relative to the context clock;
        None when the file cannot be stat'ed (age unknown, not zero)."""
        timestamp = self._adapter.cache_timestamp()
        if timestamp is None:
            return None
        return context.now - timestamp

    def _load(self, state: _ScrapeState) -> _ScrapeState:
        """Pipeline stage: read+map the cache, capturing any failure.

        The context's calendar date is the mapping layer's reference
        date for due-relative status derivation (HAC's only missing
        signal) — the caller's clock, never a wall-clock read.
        """
        try:
            snapshot = self._adapter.load(
                reference_date=state.context.now.date()
            )
        except CacheLoadError as exc:
            return replace(state, error=str(exc))
        return replace(state, snapshot=snapshot)

    def _assemble(self, state: _ScrapeState) -> ScrapeResult:
        context = state.context
        if state.error is not None:
            return self._result(
                context, outcome=RunOutcome.FAILURE, message=state.error
            )
        snapshot = state.snapshot
        if snapshot is None:  # unreachable: _load sets exactly one field
            return self._result(
                context,
                outcome=RunOutcome.FAILURE,
                message="HAC scrape produced neither data nor an error",
            )
        if not snapshot.student_found:
            return self._result(
                context,
                outcome=RunOutcome.SUCCESS,
                found=0,
                message=snapshot.note,
            )
        if snapshot.skipped:
            detail = "; ".join(snapshot.skipped)
            return self._result(
                context,
                outcome=RunOutcome.PARTIAL,
                found=snapshot.total_items,
                message=(
                    f"{len(snapshot.skipped)} of {snapshot.total_items} "
                    f"cache item(s) could not be mapped: {detail}"
                ),
                records=snapshot.records,
            )
        return self._result(
            context,
            outcome=RunOutcome.SUCCESS,
            found=len(snapshot.records),
            records=snapshot.records,
        )

    def _result(
        self,
        context: ScrapeContext,
        *,
        outcome: RunOutcome,
        found: int = 0,
        message: str | None = None,
        records: tuple[DriverRecord, ...] = (),
    ) -> ScrapeResult:
        run = SourceRun(
            source_kind=SourceKind.HAC,
            started_at=context.now,
            finished_at=self._adapter.now(),
            outcome=outcome,
            found=found,
            message=message,
        )
        return ScrapeResult(run=run, drivers=list(records))
