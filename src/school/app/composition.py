"""The composition root: every wiring decision lives here (DESIGN §3).

Pattern: Composition root with manual constructor DI. ``build_app``
takes an ``AppConfig`` plus two injectable seams — the HTTP ``Fetcher``
(defaults to the production urllib fetcher) and the ``clock`` — and
returns a frozen ``App`` holding the fully wired object graph:
connection factory, repositories, the driver registry (the three
always-on production drivers, plus the Classroom driver when its
optional config is present), the query services, the scrape runner,
and the deployable ASGI app. Tests inject a stub-mapped fetcher and a fixed
clock through this same path, so what production runs is what the
system tests exercised (C7).

The database is migrated at build time: an app that cannot open its
store fails at startup, not on first request.
"""

import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from starlette.types import ASGIApp

from school.app.config import AppConfig, ConfigError
from school.app.scrape_runner import ScrapeRunner
from school.domain import SourceKind, StudentRef
from school.driver_classroom_web import (
    ClassroomSession,
    ClassroomStudentConfig,
    ClassroomWebDriver,
    ProfileConfig,
)
from school.driver_hac import HacCacheAdapter, HacDriver
from school.driver_web import (
    AgendaDriver,
    DeckConfig,
    Fetcher,
    TeamSiteDriver,
    default_parser_registry,
    urllib_fetcher,
)
from school.mcp_server import QueryServices, build_server
from school.spi import DriverRegistry, SourceDriver
from school.vault import Vault, VaultCredentials
from school.store import (
    ConnectionFactory,
    DriverRepository,
    ResourceRepository,
    RunLogRepository,
    file_connection_factory,
    migrate,
)


def load_deck_configs(text: str) -> tuple[DeckConfig, ...]:
    """Parse a deck-registry document into DeckConfigs.

    Pure loader for the M3 discovery format:
    ``{"decks": [{"subject", "url", "template"}, ...], ...}`` — extra
    keys (discovery notes, provenance) are ignored. Anything malformed
    raises ConfigError: a deck registry the root cannot read is a
    deployment error, not a scrape failure.
    """
    try:
        payload: object = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"deck registry is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigError("deck registry must be a JSON object")
    raw_decks = payload.get("decks")
    if not isinstance(raw_decks, list):
        raise ConfigError("deck registry must carry a 'decks' list")
    decks: list[DeckConfig] = []
    for index, raw in enumerate(raw_decks):
        if not isinstance(raw, dict):
            raise ConfigError(f"deck registry entry {index} is not an object")
        fields: dict[str, str] = {}
        for key in ("subject", "url", "template"):
            value = raw.get(key)
            if not isinstance(value, str) or not value:
                raise ConfigError(
                    f"deck registry entry {index} needs a non-empty "
                    f"{key!r} string"
                )
            fields[key] = value
        decks.append(
            DeckConfig(
                subject=fields["subject"],
                url=fields["url"],
                template_id=fields["template"],
            )
        )
    return tuple(decks)


def _read_deck_registry(path: str) -> tuple[DeckConfig, ...]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            f"SCHOOL_DECK_REGISTRY: cannot read deck registry at "
            f"{path}: {exc}"
        ) from exc
    try:
        return load_deck_configs(text)
    except ConfigError as exc:
        raise ConfigError(f"SCHOOL_DECK_REGISTRY ({path}): {exc}") from exc


def _classroom_driver(
    config: AppConfig, clock_fn: Callable[[], datetime]
) -> ClassroomWebDriver:
    """Wire the Classroom driver (M6) from its optional config.

    Called only when ``config.classroom_configured``. The vault is
    unlocked here — first unlock creates the key/vault in the vault
    dir, matching how the composition root owns deployment-time I/O
    (the DB migration above does the same). The browser itself stays
    lazy: the session factory builds a ClassroomSession per scrape
    and Chromium starts on first probe.
    """
    profile_dir = config.classroom_profile_dir
    account = config.classroom_account
    if not profile_dir or not account:
        raise ConfigError(
            "classroom driver needs both SCHOOL_CLASSROOM_PROFILE_DIR "
            "and SCHOOL_CLASSROOM_ACCOUNT"
        )
    credentials: VaultCredentials | None = None
    if config.vault_dir:
        vault = Vault(
            vault_path=Path(config.vault_dir) / "vault.json",
            key_path=Path(config.vault_dir) / "vault.key",
        )
        vault.unlock()
        credentials = VaultCredentials(vault)
    profile = ProfileConfig(profile_dir=Path(profile_dir))
    return ClassroomWebDriver(
        session_factory=lambda: ClassroomSession(profile),
        credentials=credentials,
        student=ClassroomStudentConfig(
            account_id=account, expected_email=account
        ),
        clock=clock_fn,
    )


@dataclass(frozen=True, kw_only=True)
class App:
    """The wired object graph, as one immutable value."""

    config: AppConfig
    student: StudentRef
    clock: Callable[[], datetime]
    connect: ConnectionFactory
    driver_repository: DriverRepository
    resource_repository: ResourceRepository
    run_log_repository: RunLogRepository
    registry: DriverRegistry
    services: QueryServices
    scrape_runner: ScrapeRunner
    asgi_app: ASGIApp


def build_app(
    config: AppConfig,
    *,
    fetcher: Fetcher | None = None,
    clock: Callable[[], datetime] | None = None,
) -> App:
    """Wire the whole application from ``config`` (see module docstring)."""
    clock_fn: Callable[[], datetime] = (
        clock if clock is not None else (lambda: datetime.now(timezone.utc))
    )
    fetch: Fetcher = fetcher if fetcher is not None else urllib_fetcher()

    # Migration runs at build time, on a fresh connection (idempotent).
    with closing(sqlite3.connect(config.db_path)) as connection:
        migrate(connection)
    connect = file_connection_factory(config.db_path)
    driver_repository = DriverRepository(connect)
    resource_repository = ResourceRepository(connect)
    run_log_repository = RunLogRepository(connect)

    student = StudentRef(
        id=config.student_id, display_name=config.student_name
    )
    decks = _read_deck_registry(config.deck_registry_path)
    drivers: dict[SourceKind, SourceDriver] = {
        SourceKind.HAC: HacDriver(
            HacCacheAdapter(
                path_provider=lambda: Path(config.hac_cache_path),
                clock=clock_fn,
                student=student,
            ),
            timezone_name=config.timezone_name,
        ),
        SourceKind.TEAM_SITE: TeamSiteDriver(
            hub_url=config.team_site_url, fetcher=fetch
        ),
        SourceKind.AGENDA: AgendaDriver(
            fetcher=fetch,
            decks=decks,
            parsers=default_parser_registry(),
        ),
    }
    if config.classroom_configured:
        drivers[SourceKind.CLASSROOM_WEB] = _classroom_driver(
            config, clock_fn
        )
    registry = DriverRegistry(drivers)
    services = QueryServices(
        drivers=driver_repository,
        resources=resource_repository,
        runs=run_log_repository,
        clock=clock_fn,
    )
    return App(
        config=config,
        student=student,
        clock=clock_fn,
        connect=connect,
        driver_repository=driver_repository,
        resource_repository=resource_repository,
        run_log_repository=run_log_repository,
        registry=registry,
        services=services,
        scrape_runner=ScrapeRunner(
            registry=registry,
            driver_repository=driver_repository,
            resource_repository=resource_repository,
            run_log_repository=run_log_repository,
            clock=clock_fn,
        ),
        asgi_app=build_server(services, expected_token=config.token),
    )
