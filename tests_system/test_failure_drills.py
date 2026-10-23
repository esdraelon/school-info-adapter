"""M7 failure drills D1/D3/D4/D5: the system degrades VISIBLY.

Each drill breaks one thing the way production eventually will — a
browser dying mid-scrape, the grades cache going quiet, a vault key
swapped under a restored vault, two sources out at once — and asserts
the failure surfaces in runs, health, or exit codes, never silently.
Boundaries follow C7: real drivers, runner, store, and composition
root; only the externals (web pages, the cache file, the browser
session) are scripted.
"""

import io
import json
import os
import shutil
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from school.app import build_app, config_from_env
from school.app.cli import cmd_scrape
from school.app.config import AppConfig
from school.app.health import HealthStatus, evaluate_health
from school.app.scrape_runner import ScrapeRunner
from school.domain import Found, RunOutcome, SourceKind, StudentRef
from school.driver_classroom_web import (
    ClassroomStudentConfig,
    ClassroomWebDriver,
    SessionState,
    TodoTab,
)
from school.driver_classroom_web.pages import Row
from school.driver_web.fetching import FetchError, fetch_url
from school.spi import DriverRegistry, ScrapeContext
from school.store import (
    DriverRepository,
    ResourceRepository,
    RunLogRepository,
    file_connection_factory,
)
from school.vault import Vault, VaultError

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_FIXTURES = REPO_ROOT / "tests" / "unit" / "driver_web" / "fixtures"
HAC_FIXTURES = REPO_ROOT / "tests" / "unit" / "driver_hac" / "fixtures"

NOW = datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)
SAM = StudentRef(id="sam", display_name="Sam")
ACCOUNT = "sam@example.test"

HUB_URL = "https://sites.google.com/leanderisd.org/8th-grade-team-site/home"
MATH_DECK_URL = (
    "https://docs.google.com/presentation/d/e/"
    "2PACX-1vTNlVp2y1IQY8QJE4-7I1zFgDIqVPj1y8gmj3Lilr9jqjlrMWQ-"
    "r58TmlkxdrjdPw0ffzrfbICyG2Dx/pub"
)
TUTORIAL_EMBED_URL = (
    "https://docs.google.com/presentation/d/"
    "1U__2wWHfuv-_61KnL3b05FXNgEL_S5atP3q7XrPipp8/embed"
)


# ---------------------------------------------------------------- D1


class _DrillPage:
    """TodoPage protocol over canned rows (groups pre-expanded)."""

    def __init__(self, groups: dict[str, list[Row]]) -> None:
        self._groups = groups

    def course_names(self) -> list[str]:
        return ["Math 8", "26-27 Wind Ensemble (TMEA Honor Band)"]

    def group_headings(self) -> list[str]:
        return list(self._groups)

    def expand_group(self, heading: str) -> None:
        pass

    def rows_in_group(self, heading: str) -> list[Row]:
        return list(self._groups[heading])


class _DyingSession:
    """Session port: authenticates, serves Assigned, dies on Missing."""

    last_probe_detail = "drill session"

    def __init__(self) -> None:
        self.closed = False
        self.opened: list[TodoTab] = []

    def probe(self) -> SessionState:
        return SessionState.AUTHENTICATED

    def open_todo(self, tab: TodoTab) -> _DrillPage:
        self.opened.append(tab)
        if tab is TodoTab.MISSING:
            raise ConnectionError("browser target closed mid-scrape")
        if tab is TodoTab.ASSIGNED:
            return _DrillPage(
                {
                    "This week": [
                        Row(
                            name="Fractions Quiz Math 8 Tomorrow, 9:00 AM",
                            href="/c/1",
                        )
                    ]
                }
            )
        return _DrillPage(
            {
                "This week": [
                    Row(
                        name="10/21 Scale Run Through with Chromatic "
                        "(Wind Ensemble) 26-27 Wind Ensemble (TMEA Honor "
                        "Band) 87.77 points out of possible 100 "
                        "Not turned in",
                        href="/c/6",
                    )
                ]
            }
        )

    def sign_in_page(self) -> Any:
        raise AssertionError("an authenticated session never re-auths")

    def close(self) -> None:
        self.closed = True


def test_d1_session_death_mid_scrape_is_a_recorded_partial(
    tmp_path: Path,
) -> None:
    """The browser dies opening the Missing tab: no exception escapes
    scrape(), the run is a recorded PARTIAL naming the dead tab, and
    the surviving tabs' records are persisted."""
    session = _DyingSession()
    driver = ClassroomWebDriver(
        session_factory=lambda: session,
        credentials=None,
        student=ClassroomStudentConfig(
            account_id=ACCOUNT, expected_email=ACCOUNT
        ),
        clock=lambda: NOW,
    )
    factory = file_connection_factory(tmp_path / "school.db")
    runs_repo = RunLogRepository(factory)
    runner = ScrapeRunner(
        registry=DriverRegistry({SourceKind.CLASSROOM_WEB: driver}),
        driver_repository=DriverRepository(factory),
        resource_repository=ResourceRepository(factory),
        run_log_repository=runs_repo,
        clock=lambda: NOW,
    )
    runs = runner.run(SAM)  # reaching here proves nothing escaped
    (run,) = runs
    assert run.outcome is RunOutcome.PARTIAL
    assert run.message is not None
    assert "missing tab" in run.message
    assert session.closed  # even a dead session is closed out
    recorded = runs_repo.latest_run(SourceKind.CLASSROOM_WEB)
    assert isinstance(recorded, Found)
    assert recorded.value.outcome is RunOutcome.PARTIAL
    titles = {
        record.title
        for record in DriverRepository(factory).list_drivers(
            student_id="sam"
        )
    }
    assert "Fractions Quiz" in titles


# ---------------------------------------------------------------- D3


def test_d3_quiet_hac_cache_evaluates_unhealthy_stale(
    tmp_path: Path,
) -> None:
    """The grades scraper goes quiet: the cache is ~10 days old and no
    scrape pass has run since its era. Through the real read path —
    QueryServices.source_health + the app's health evaluation — HAC
    must verdict UNHEALTHY (stale) with the age in the reason; this is
    the digest banner's trigger."""
    cache_path = tmp_path / "cache.json"
    shutil.copyfile(HAC_FIXTURES / "cache_full.json", cache_path)
    stale_mtime = datetime(2026, 10, 12, 18, 13, tzinfo=timezone.utc)
    stamp = stale_mtime.timestamp()
    os.utime(cache_path, (stamp, stamp))
    registry_path = tmp_path / "deck_registry.json"
    registry_path.write_text(json.dumps({"decks": []}), encoding="utf-8")
    config = AppConfig(
        db_path=str(tmp_path / "school.db"),
        hac_cache_path=str(cache_path),
        token="drill-token",
        deck_registry_path=str(registry_path),
    )
    last_pass = datetime(2026, 10, 12, 18, 15, tzinfo=timezone.utc)
    app = build_app(config, clock=lambda: last_pass)
    (hac_run,) = app.scrape_runner.run(
        app.student, kinds=[SourceKind.HAC]
    )
    assert hac_run.outcome is RunOutcome.SUCCESS
    # Freshness is data (C10): records still carry the cache's mtime,
    # and the driver's age probe agrees the cache is ~10 days old.
    records = app.driver_repository.list_drivers(student_id="sam")
    assert records and {r.scraped_at for r in records} == {stale_mtime}
    hac_driver = app.registry.driver_for(SourceKind.HAC).unwrap()
    context = ScrapeContext(student=app.student, now=NOW)
    assert hac_driver.cache_age(context) == NOW - stale_mtime
    report = evaluate_health(
        [
            entry
            for entry in app.services.source_health(NOW)
            if entry.source_kind in config.stale_thresholds
        ],
        config.stale_thresholds,
        NOW,
    )
    entry = next(
        e for e in report.entries if e.source_kind is SourceKind.HAC
    )
    assert entry.status is HealthStatus.UNHEALTHY
    assert "stale" in entry.reason
    assert "239" in entry.reason  # ~9d 23h45m past, over the 30h budget
    assert not report.ok


# ---------------------------------------------------------------- D4


def _classroom_config(tmp_path: Path, vault_dir: Path) -> AppConfig:
    registry_path = tmp_path / "deck_registry.json"
    registry_path.write_text(json.dumps({"decks": []}), encoding="utf-8")
    return AppConfig(
        db_path=str(tmp_path / "school.db"),
        hac_cache_path=str(tmp_path / "cache.json"),
        token="drill-token",
        deck_registry_path=str(registry_path),
        classroom_profile_dir=str(tmp_path / "profile"),
        classroom_account=ACCOUNT,
        vault_dir=str(vault_dir),
    )


def test_d4_replaced_vault_key_fails_the_build_loudly(
    tmp_path: Path,
) -> None:
    """Composition level (the vault-level wrong-key read is already
    unit-covered in tests/unit/vault): the vault is built with key A
    and holds Sam's credentials; the key file is then replaced by a
    different validly-shaped key — as when vault files are restored
    onto a fresh host. unlock() alone cannot tell (it checks
    structure only), so build_app must fail LOUD via the boot-time
    verify, never boot a driver whose credentials it cannot read."""
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    vault = Vault(
        vault_path=vault_dir / "vault.json",
        key_path=vault_dir / "vault.key",
    )
    vault.unlock()
    vault.put(f"{ACCOUNT}.username", ACCOUNT)
    vault.put(f"{ACCOUNT}.password", "drill-password")
    config = _classroom_config(tmp_path, vault_dir)
    # Control: with the key that fits, the same config builds and the
    # classroom driver registers — the failure below is the key.
    healthy = build_app(config)
    assert isinstance(
        healthy.registry.driver_for(SourceKind.CLASSROOM_WEB), Found
    )
    (vault_dir / "vault.key").write_bytes(os.urandom(32))
    with pytest.raises(VaultError, match="authentication"):
        build_app(config)


# ---------------------------------------------------------------- D5


class _StubHandler(BaseHTTPRequestHandler):
    """Serves captured fixture files by exact path; 404 otherwise."""

    routes: dict[str, Path] = {}

    def do_GET(self) -> None:
        target = self.routes.get(self.path)
        if target is None:
            self.send_error(404)
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        pass


def test_d5_whole_source_outage_degrades_visibly(
    tmp_path: Path,
) -> None:
    """HAC cache file missing AND the agenda fetch 404s, in one pass:
    both are recorded FAILURE, the team site is still scraped, the
    runner returns every run, and the CLI scrape exit code stays 0
    (exit 1 is reserved for EVERY kind failing)."""
    _StubHandler.routes = {
        "/hub": WEB_FIXTURES / "team_site_home.html",
        "/tutorial-embed": WEB_FIXTURES / "tutorial_embed.html",
    }
    stub = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    try:
        stub_base = f"http://127.0.0.1:{stub.server_address[1]}"
        stub_urls = {
            HUB_URL: f"{stub_base}/hub",
            TUTORIAL_EMBED_URL: f"{stub_base}/tutorial-embed",
            # No /math-deck route exists: the stub 404s the deck.
            MATH_DECK_URL: f"{stub_base}/math-deck",
        }

        def fetcher(url: str) -> str:
            target = stub_urls.get(url)
            if target is None:
                raise FetchError(f"no stub route for {url}")
            return fetch_url(target)  # the production urllib fetcher

        registry_path = tmp_path / "deck_registry.json"
        registry_path.write_text(
            json.dumps(
                {
                    "decks": [
                        {
                            "subject": "Math 8",
                            "url": MATH_DECK_URL,
                            "template": "weekly-grid-v1",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        config = config_from_env(
            {
                "SCHOOL_DB_PATH": str(tmp_path / "school.db"),
                # The cache file is never created: HAC is out.
                "SCHOOL_HAC_CACHE_PATH": str(tmp_path / "absent.json"),
                "SCHOOL_MCP_TOKEN": "drill-token",
                "SCHOOL_DECK_REGISTRY": str(registry_path),
            }
        )
        out = io.StringIO()
        code = cmd_scrape(
            config, fetcher=fetcher, clock=lambda: NOW, out=out
        )
        assert code == 0
        lines = out.getvalue()
        assert "hac: failure" in lines
        assert "agenda: failure" in lines
        assert "team_site: success" in lines
        # Runner level: every kind attempted, every run returned, and
        # the outage is a fact in the run log — not a gap in it.
        app = build_app(config, fetcher=fetcher, clock=lambda: NOW)
        runs = app.scrape_runner.run(app.student)
        by_kind = {run.source_kind: run for run in runs}
        assert by_kind[SourceKind.HAC].outcome is RunOutcome.FAILURE
        assert by_kind[SourceKind.AGENDA].outcome is RunOutcome.FAILURE
        assert by_kind[SourceKind.TEAM_SITE].outcome is RunOutcome.SUCCESS
        health = {
            entry.source_kind: entry
            for entry in app.services.source_health(NOW)
        }
        assert health[SourceKind.HAC].last_outcome is RunOutcome.FAILURE
        assert health[SourceKind.AGENDA].last_outcome is RunOutcome.FAILURE
        assert (
            health[SourceKind.TEAM_SITE].last_outcome is RunOutcome.SUCCESS
        )
    finally:
        stub.shutdown()
