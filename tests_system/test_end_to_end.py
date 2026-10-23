"""End-to-end system test: drivers -> merge -> store -> MCP over HTTP.

C7 in action: the ONLY replaced pieces are the external boundaries —
Google Sites/Slides pages are served by a local stub HTTP server from
the real captured fixtures (tests/unit/driver_web/fixtures/), and the
HAC cache is a tmp copy of the real-shaped fixture. Everything else is
production code: the real urllib fetcher, the real drivers, the real
registry/merge, a real SQLite file, the real repositories, and the
real MCP ASGI app served by uvicorn and called over real HTTP with
the SDK client.
"""

import asyncio
import json
import shutil
import socket
import threading
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from school.domain import SourceKind, StudentRef
from school.driver_hac import HacCacheAdapter, HacDriver
from school.driver_web import (
    WEEKLY_GRID_TEMPLATE,
    AgendaDriver,
    DeckConfig,
    TeamSiteDriver,
    default_parser_registry,
)
from school.driver_web.fetching import FetchError, fetch_url
from school.mcp_server import QueryServices, build_server
from school.merge import merge_driver_records
from school.spi import DriverRegistry, ScrapeContext
from school.store import (
    DriverRepository,
    ResourceRepository,
    RunLogRepository,
    file_connection_factory,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_FIXTURES = REPO_ROOT / "tests" / "unit" / "driver_web" / "fixtures"
HAC_FIXTURES = REPO_ROOT / "tests" / "unit" / "driver_hac" / "fixtures"

SAM = StudentRef(id="sam", display_name="Sam")
NOW = datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)
HEALTH_NOW = NOW + timedelta(minutes=30)
TOKEN = "system-test-token"

# Production-shaped URLs (identical to the driver unit tests); the
# mapped fetcher below re-points them at the stub server, path by path.
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


class _FixtureHandler(BaseHTTPRequestHandler):
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


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture(scope="module")
def stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Boot the whole stack once; yields the MCP endpoint URL."""
    _FixtureHandler.routes = {
        "/hub": WEB_FIXTURES / "team_site_home.html",
        "/math-deck": WEB_FIXTURES / "math_agenda_pub.html",
        "/tutorial-embed": WEB_FIXTURES / "tutorial_embed.html",
    }
    stub = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    stub_base = f"http://127.0.0.1:{stub.server_address[1]}"

    tmp = tmp_path_factory.mktemp("e2e")
    cache_path = tmp / "cache.json"
    shutil.copyfile(HAC_FIXTURES / "cache_full.json", cache_path)

    factory = file_connection_factory(tmp / "school.db")
    drivers_repo = DriverRepository(factory)
    resources_repo = ResourceRepository(factory)
    runs_repo = RunLogRepository(factory)

    stub_urls = {
        HUB_URL: f"{stub_base}/hub",
        MATH_DECK_URL: f"{stub_base}/math-deck",
        TUTORIAL_EMBED_URL: f"{stub_base}/tutorial-embed",
    }

    def fetcher(url: str) -> str:
        target = stub_urls.get(url)
        if target is None:
            raise FetchError(f"no stub route for {url}")
        return fetch_url(target)  # the production urllib fetcher

    registry = DriverRegistry(
        {
            SourceKind.HAC: HacDriver(
                HacCacheAdapter(
                    path_provider=lambda: cache_path,
                    clock=lambda: NOW,
                    student=SAM,
                )
            ),
            SourceKind.TEAM_SITE: TeamSiteDriver(
                hub_url=HUB_URL, fetcher=fetcher
            ),
            SourceKind.AGENDA: AgendaDriver(
                fetcher=fetcher,
                decks=(
                    DeckConfig(
                        subject="Math 8",
                        url=MATH_DECK_URL,
                        template_id=WEEKLY_GRID_TEMPLATE,
                    ),
                ),
                parsers=default_parser_registry(),
            ),
        }
    )

    # The scrape flow, played here by the test (M5's composition root
    # owns this wiring in production): scrape -> merge -> upsert, with
    # resources and run-log rows persisted alongside.
    context = ScrapeContext(student=SAM, now=NOW)
    sightings = []
    resources = []
    for kind in registry.kinds():
        driver = registry.driver_for(kind).unwrap()
        result = driver.scrape(context)
        runs_repo.append_run(result.run)
        sightings.extend(result.drivers)
        resources.extend(result.resources)
    drivers_repo.upsert_drivers(merge_driver_records(sightings))
    resources_repo.upsert_resources(resources)

    services = QueryServices(
        drivers=drivers_repo,
        resources=resources_repo,
        runs=runs_repo,
        clock=lambda: HEALTH_NOW,
    )
    app = build_server(services, expected_token=TOKEN)

    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(500):
        if server.started:
            break
        time.sleep(0.01)
    assert server.started, "MCP server did not start"
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        stub.shutdown()


def call_tool(url: str, name: str, args: dict[str, Any]) -> Any:
    """One MCP tool call over real HTTP via the SDK client."""

    async def go() -> Any:
        # trust_env=False: the sandbox's proxy env must not swallow
        # localhost traffic (its bracketed-IPv6 no_proxy entries also
        # break httpx's proxy-pattern parsing outright).
        http_client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {TOKEN}"},
            trust_env=False,
            follow_redirects=True,
        )
        async with streamable_http_client(
            url, http_client=http_client
        ) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, args)
                assert not result.isError, f"{name} failed: {result.content}"
                if result.structuredContent is not None:
                    return result.structuredContent["result"]
                blocks = [
                    json.loads(block.text)
                    for block in result.content
                    if block.type == "text"
                ]
                if len(blocks) == 1 and isinstance(blocks[0], list):
                    return blocks[0]
                return blocks

    return asyncio.run(go())


def test_get_drivers_merges_agenda_and_hac(stack: str) -> None:
    drivers = call_tool(stack, "get_drivers", {"student_id": "sam"})
    quiz = next(
        d for d in drivers if d["title"] == "Quiz: Pythagorean Theorem"
    )
    # From the math deck, with the FD (final deadline) override: the
    # quiz sat on Fri 10/16 but its retake deadline 10/26 wins.
    assert quiz["kind"] == "QUIZ"
    assert quiz["source_kind"] == "AGENDA"
    assert quiz["due_at"] is not None
    assert quiz["due_at"].startswith("2026-10-26")
    graded = [
        d
        for d in drivers
        if d["status"] == "GRADED" and d["source_kind"] == "HAC"
    ]
    assert graded, "expected at least one GRADED item from the HAC cache"
    timeline = next(d for d in graded if d["title"] == "Timeline Project")
    assert timeline["score_text"] == "87.77"


def test_get_resources_returns_deck_notes_and_shared(stack: str) -> None:
    notes = call_tool(
        stack, "get_resources", {"student_id": "sam", "kind": "NOTES"}
    )
    titles = {r["title"] for r in notes}
    assert {"Volume of cylinders", "Area/Perimeter"} <= titles
    assert all(r["kind"] == "NOTES" for r in notes)
    everything = call_tool(stack, "get_resources", {"student_id": "sam"})
    shared_tutorials = [
        r
        for r in everything
        if r["kind"] == "TUTORIAL" and r["student"] is None
    ]
    assert shared_tutorials, "team-site tutorial resources are shared"


def test_get_source_health_reports_all_sources(stack: str) -> None:
    health = call_tool(stack, "get_source_health", {})
    by_kind = {entry["source_kind"]: entry for entry in health}
    assert set(by_kind) == {"HAC", "TEAM_SITE", "AGENDA", "CLASSROOM_WEB"}
    for kind in ("HAC", "TEAM_SITE", "AGENDA"):
        entry = by_kind[kind]
        assert entry["last_outcome"] == "SUCCESS", entry
        assert entry["last_finished_at"] == "2026-10-22T18:00:00Z"
        assert entry["age_seconds"] == 1800.0  # health clock is NOW+30m
    never = by_kind["CLASSROOM_WEB"]
    assert never["last_outcome"] is None
    assert never["last_finished_at"] is None
    assert never["age_seconds"] is None


def test_unauthenticated_calls_are_rejected(stack: str) -> None:
    with httpx.Client(trust_env=False) as client:
        no_token = client.post(
            stack, json={"jsonrpc": "2.0", "id": 1, "method": "ping"}
        )
        wrong_token = client.post(
            stack,
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"Authorization": "Bearer wrong"},
        )
    for response in (no_token, wrong_token):
        assert response.status_code == 401
        assert response.json() == {"error": "unauthorized"}
