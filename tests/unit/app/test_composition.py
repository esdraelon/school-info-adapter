"""The composition root: deck-registry loader + build_app wiring.

The loader is pure (text in, DeckConfigs out); build_app is exercised
against tmp files with a fetcher that must never be called during
construction — wiring is I/O-light by design (the migration is the
only build-time write).
"""

import json
import os
import shutil
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from driver_web.conftest import make_page

from school.app.composition import build_app, load_deck_configs
from school.app.config import AppConfig, ConfigError
from school.domain import (
    Found,
    ItemStatus,
    RunOutcome,
    SourceKind,
    SourceRun,
    StudentRef,
)
from school.spi import ScrapeContext

REPO_ROOT = Path(__file__).resolve().parents[3]
WEB_FIXTURES = REPO_ROOT / "tests" / "unit" / "driver_web" / "fixtures"
HAC_FIXTURES = REPO_ROOT / "tests" / "unit" / "driver_hac" / "fixtures"

FIXED = datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)


def never_fetch(url: str) -> str:
    raise AssertionError(f"fetcher called during build: {url}")


def test_loader_reads_the_real_m3_registry_fixture() -> None:
    text = (WEB_FIXTURES / "deck_registry.json").read_text(encoding="utf-8")
    decks = load_deck_configs(text)
    assert len(decks) == 4
    math = decks[0]
    assert math.subject == "Math 8"
    assert math.template_id == "weekly-grid-v1"
    assert math.url.endswith("/pub")
    team_agenda = decks[2]
    assert team_agenda.template_id == "team-table-v1"


def test_loader_ignores_discovery_metadata() -> None:
    text = json.dumps(
        {
            "captured": "2026-10-22",
            "hub_url": "https://example.test/hub",
            "decks": [
                {
                    "subject": "Math 8",
                    "url": "https://example.test/deck",
                    "template": "weekly-grid-v1",
                    "discovered_via": "a note the loader must ignore",
                }
            ],
        }
    )
    (deck,) = load_deck_configs(text)
    assert deck.subject == "Math 8"
    assert deck.template_id == "weekly-grid-v1"


@pytest.mark.parametrize(
    "text",
    [
        "not json at all",
        "[]",
        "{}",
        '{"decks": {}}',
        '{"decks": [42]}',
        '{"decks": [{"subject": "Math 8"}]}',
        '{"decks": [{"subject": "Math 8", "url": "", "template": "t"}]}',
        '{"decks": [{"subject": "Math 8", "url": "u", "template": 7}]}',
    ],
)
def test_loader_rejects_malformed_registries(text: str) -> None:
    with pytest.raises(ConfigError):
        load_deck_configs(text)


@pytest.fixture
def app_config(tmp_path: Path) -> AppConfig:
    cache_path = tmp_path / "cache.json"
    shutil.copyfile(HAC_FIXTURES / "cache_full.json", cache_path)
    registry_path = tmp_path / "deck_registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "decks": [
                    {
                        "subject": "Math 8",
                        "url": "https://example.test/math-deck",
                        "template": "weekly-grid-v1",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return AppConfig(
        db_path=str(tmp_path / "school.db"),
        hac_cache_path=str(cache_path),
        token="composition-test-token",
        deck_registry_path=str(registry_path),
    )


def test_build_app_wires_the_whole_graph(app_config: AppConfig) -> None:
    app = build_app(app_config, fetcher=never_fetch, clock=lambda: FIXED)
    assert app.config is app_config
    assert app.student == StudentRef(id="sam", display_name="Sam")
    assert app.clock() == FIXED
    assert app.services.now() == FIXED
    # All three production drivers registered, in registry order.
    assert app.registry.kinds() == (
        SourceKind.AGENDA,
        SourceKind.HAC,
        SourceKind.TEAM_SITE,
    )
    for kind in app.registry.kinds():
        assert isinstance(app.registry.driver_for(kind), Found)
    assert app.scrape_runner is not None
    assert app.asgi_app is not None


def test_build_app_migrates_the_database_at_build_time(
    app_config: AppConfig,
) -> None:
    build_app(app_config, fetcher=never_fetch)
    with closing(sqlite3.connect(app_config.db_path)) as connection:
        versions = [
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations"
            )
        ]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert versions == [1]
    assert {"drivers", "resources", "source_runs"} <= tables


def test_build_app_threads_the_configured_timezone_into_the_hac_driver(
    app_config: AppConfig,
) -> None:
    """M7 FIX 2, wired end to end: 2026-10-21T01:30Z is 20:30 CDT on
    10-20, so the school-zone reference date is 10-20 and the
    unscored Vocabulary Quiz 4 (due 10/20) is not yet MISSING; with
    SCHOOL_TIMEZONE=UTC the reference date is 10-21 and it is."""
    instant = datetime(2026, 10, 21, 1, 30, tzinfo=timezone.utc)

    def quiz_status(config: AppConfig) -> ItemStatus:
        app = build_app(config, fetcher=never_fetch, clock=lambda: FIXED)
        driver = app.registry.driver_for(SourceKind.HAC).unwrap()
        result = driver.scrape(
            ScrapeContext(student=app.student, now=instant)
        )
        quiz = next(
            record
            for record in result.drivers
            if record.title == "Vocabulary Quiz 4"
        )
        return quiz.status

    assert quiz_status(app_config) is ItemStatus.ASSIGNED
    assert (
        quiz_status(replace(app_config, timezone_name="UTC"))
        is ItemStatus.MISSING
    )


def test_build_app_threads_the_stale_budget_into_the_hac_driver(
    app_config: AppConfig,
) -> None:
    """M7.1 FIX A, wired end to end: SCHOOL_STALE_HAC_HOURS reaches
    HacDriver as stale_after. The cache is stamped 2h before the
    scrape instant — inside the default 30h budget the run succeeds;
    with a 1h budget the same scrape downgrades to PARTIAL with the
    staleness named and the age fact attached."""
    cache = Path(app_config.hac_cache_path)
    stamp = (FIXED - timedelta(hours=2)).timestamp()
    os.utime(cache, (stamp, stamp))

    def scrape_run(config: AppConfig) -> SourceRun:
        app = build_app(config, fetcher=never_fetch, clock=lambda: FIXED)
        driver = app.registry.driver_for(SourceKind.HAC).unwrap()
        return driver.scrape(
            ScrapeContext(student=app.student, now=FIXED)
        ).run

    fresh = scrape_run(app_config)
    assert fresh.outcome is RunOutcome.SUCCESS
    downgraded = scrape_run(replace(app_config, stale_hac_hours=1.0))
    assert downgraded.outcome is RunOutcome.PARTIAL
    assert downgraded.message is not None
    assert "cache stale" in downgraded.message
    assert "cache_age=2h" in downgraded.message


# The Fix B boundary deck (mirrors tests/unit/driver_web's): one
# week whose Monday cell carries an "FD 1/1" item — the inferred
# year turns on the reference date at the July midpoint.
_BOUNDARY_SLIDE = (
    "<svg>"
    '<g role="img" aria-label="Monday 6/29">'
    '<path d="m 40 40l0 20l 120 0l0 -20z"/></g>'
    '<g role="img" aria-label="Tuesday 6/30">'
    '<path d="m 240 40l0 20l 120 0l0 -20z"/></g>'
    '<g role="img" aria-label="Wednesday 7/1">'
    '<path d="m 440 40l0 20l 120 0l0 -20z"/></g>'
    '<g role="img" aria-label="Thursday 7/2">'
    '<path d="m 640 40l0 20l 120 0l0 -20z"/></g>'
    '<g role="img" aria-label="Friday 7/3">'
    '<path d="m 840 40l0 20l 120 0l0 -20z"/></g>'
    '<g role="img" aria-label="Year Midpoint Quiz&#xa;FD 1/1">'
    '<path d="m 40 100l0 30l 120 0l0 -30z"/></g>'
    "</svg>"
)


def test_build_app_threads_the_configured_timezone_into_the_agenda_driver(
    app_config: AppConfig,
) -> None:
    """M7.1 FIX B, wired end to end: at the evening instant
    2026-07-03T01:30Z (20:30 CDT on 7/2), the boundary deck's FD 1/1
    cell resolves against the school date under the default config
    (due 2026-01-01) and against the UTC date with
    SCHOOL_TIMEZONE=UTC (due 2027-01-01)."""
    page = make_page(_BOUNDARY_SLIDE)
    instant = datetime(2026, 7, 3, 1, 30, tzinfo=timezone.utc)

    def quiz_due(config: AppConfig) -> datetime | None:
        app = build_app(
            config, fetcher=lambda url: page, clock=lambda: FIXED
        )
        driver = app.registry.driver_for(SourceKind.AGENDA).unwrap()
        result = driver.scrape(
            ScrapeContext(student=app.student, now=instant)
        )
        (record,) = result.drivers
        return record.due_at

    assert quiz_due(app_config) == datetime(2026, 1, 1)
    assert quiz_due(replace(app_config, timezone_name="UTC")) == datetime(
        2027, 1, 1
    )


def test_build_app_names_a_missing_deck_registry(
    tmp_path: Path, app_config: AppConfig
) -> None:
    config = replace(
        app_config, deck_registry_path=str(tmp_path / "absent.json")
    )
    with pytest.raises(ConfigError, match="SCHOOL_DECK_REGISTRY"):
        build_app(config, fetcher=never_fetch)


def test_build_app_names_a_malformed_deck_registry(
    tmp_path: Path, app_config: AppConfig
) -> None:
    bad = tmp_path / "bad_registry.json"
    bad.write_text("{no decks here", encoding="utf-8")
    config = replace(app_config, deck_registry_path=str(bad))
    with pytest.raises(ConfigError, match="SCHOOL_DECK_REGISTRY"):
        build_app(config, fetcher=never_fetch)
