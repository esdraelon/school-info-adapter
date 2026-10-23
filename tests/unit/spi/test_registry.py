"""DriverRegistry + the SourceDriver protocol contract."""

from datetime import datetime, timezone

from school.domain import (
    Found,
    Missing,
    RunOutcome,
    SourceKind,
    SourceRun,
    StudentRef,
)
from school.spi import DriverRegistry, ScrapeContext, ScrapeResult, SourceDriver

NOW = datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)
SAM = StudentRef(id="sam", display_name="Sam")


class FakeDriver:
    """A minimal SourceDriver strategy, defined by shape alone."""

    def __init__(self, kind: SourceKind) -> None:
        self._kind = kind

    @property
    def kind(self) -> SourceKind:
        return self._kind

    def scrape(self, context: ScrapeContext) -> ScrapeResult:
        return ScrapeResult(
            run=SourceRun(
                source_kind=self._kind,
                started_at=context.now,
                finished_at=context.now,
                outcome=RunOutcome.SUCCESS,
            )
        )


def test_fake_driver_satisfies_the_protocol() -> None:
    assert isinstance(FakeDriver(SourceKind.HAC), SourceDriver)


def test_registry_returns_the_registered_driver() -> None:
    hac = FakeDriver(SourceKind.HAC)
    agenda = FakeDriver(SourceKind.AGENDA)
    registry = DriverRegistry({SourceKind.HAC: hac, SourceKind.AGENDA: agenda})
    result = registry.driver_for(SourceKind.HAC)
    assert isinstance(result, Found)
    assert result.unwrap() is hac
    assert registry.driver_for(SourceKind.AGENDA).unwrap() is agenda


def test_registry_miss_is_a_missing_with_reason() -> None:
    registry = DriverRegistry({SourceKind.HAC: FakeDriver(SourceKind.HAC)})
    result = registry.driver_for(SourceKind.CLASSROOM_WEB)
    assert isinstance(result, Missing)
    assert "classroom_web" in result.reason


def test_registry_kinds_are_sorted_and_complete() -> None:
    registry = DriverRegistry(
        {
            SourceKind.TEAM_SITE: FakeDriver(SourceKind.TEAM_SITE),
            SourceKind.HAC: FakeDriver(SourceKind.HAC),
            SourceKind.AGENDA: FakeDriver(SourceKind.AGENDA),
        }
    )
    assert registry.kinds() == (
        SourceKind.AGENDA,
        SourceKind.HAC,
        SourceKind.TEAM_SITE,
    )
    assert DriverRegistry({}).kinds() == ()


def test_driver_scrape_round_trip_through_registry() -> None:
    registry = DriverRegistry({SourceKind.HAC: FakeDriver(SourceKind.HAC)})
    driver = registry.driver_for(SourceKind.HAC).unwrap()
    result = driver.scrape(ScrapeContext(student=SAM, now=NOW))
    assert result.run.source_kind is SourceKind.HAC
    assert result.drivers == []
    assert result.resources == []


def test_scrape_context_config_defaults_to_empty() -> None:
    context = ScrapeContext(student=SAM, now=NOW)
    assert dict(context.config) == {}
    configured = ScrapeContext(student=SAM, now=NOW, config={"url": "https://x"})
    assert configured.config["url"] == "https://x"
