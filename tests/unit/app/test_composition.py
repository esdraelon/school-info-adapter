"""The composition root: deck-registry loader + build_app wiring.

The loader is pure (text in, DeckConfigs out); build_app is exercised
against tmp files with a fetcher that must never be called during
construction — wiring is I/O-light by design (the migration is the
only build-time write).
"""

import json
import shutil
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from school.app.composition import build_app, load_deck_configs
from school.app.config import AppConfig, ConfigError
from school.domain import Found, ItemStatus, SourceKind, StudentRef
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
