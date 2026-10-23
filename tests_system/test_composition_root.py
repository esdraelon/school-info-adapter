"""Composition-root system test: production wiring, end to end (M5).

Where the M4 system test played composition root by hand, this one
uses only production entry points: ``config_from_env`` -> the CLI's
``cmd_scrape`` shim (which builds the app via ``build_app``) ->
``build_app`` again for serving -> real MCP tool calls over HTTP.
C7 as ever: the only replaced pieces are the external boundaries —
Google pages via a local stub server over the captured fixtures, the
HAC cache as a tmp fixture copy, and the wall clock.
"""

import asyncio
import io
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

from school.app import build_app, config_from_env
from school.app.cli import cmd_health, cmd_scrape
from school.app.config import AppConfig
from school.driver_web.fetching import FetchError, fetch_url

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_FIXTURES = REPO_ROOT / "tests" / "unit" / "driver_web" / "fixtures"
HAC_FIXTURES = REPO_ROOT / "tests" / "unit" / "driver_hac" / "fixtures"

NOW = datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)
HEALTH_NOW = NOW + timedelta(minutes=30)
TOKEN = "composition-test-token"

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


class Stack:
    """The booted composition: endpoint URL + captured CLI results."""

    def __init__(
        self, url: str, scrape_code: int, health: dict[str, Any]
    ) -> None:
        self.url = url
        self.scrape_code = scrape_code
        self.health = health


@pytest.fixture(scope="module")
def stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Stack]:
    _FixtureHandler.routes = {
        "/hub": WEB_FIXTURES / "team_site_home.html",
        "/math-deck": WEB_FIXTURES / "math_agenda_pub.html",
        "/tutorial-embed": WEB_FIXTURES / "tutorial_embed.html",
    }
    stub = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    stub_base = f"http://127.0.0.1:{stub.server_address[1]}"

    tmp = tmp_path_factory.mktemp("composition")
    cache_path = tmp / "cache.json"
    shutil.copyfile(HAC_FIXTURES / "cache_full.json", cache_path)
    registry_path = tmp / "deck_registry.json"
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

    # Config exactly as production builds it: from the environment.
    config: AppConfig = config_from_env(
        {
            "SCHOOL_DB_PATH": str(tmp / "school.db"),
            "SCHOOL_HAC_CACHE_PATH": str(cache_path),
            "SCHOOL_MCP_TOKEN": TOKEN,
            "SCHOOL_DECK_REGISTRY": str(registry_path),
        }
    )

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

    # The scrape pass goes through the CLI shim's own function (not a
    # subprocess), which builds the app through the real composition
    # root — production wiring, not test wiring.
    scrape_code = cmd_scrape(
        config, fetcher=fetcher, clock=lambda: NOW, out=io.StringIO()
    )
    health_out = io.StringIO()
    health_code = cmd_health(
        config, fetcher=fetcher, clock=lambda: HEALTH_NOW, out=health_out
    )
    assert health_code == 0, health_out.getvalue()
    health_payload = json.loads(health_out.getvalue())

    app = build_app(config, fetcher=fetcher, clock=lambda: HEALTH_NOW)
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            app.asgi_app, host="127.0.0.1", port=port, log_level="error"
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(500):
        if server.started:
            break
        time.sleep(0.01)
    assert server.started, "composed MCP server did not start"
    try:
        yield Stack(
            url=f"http://127.0.0.1:{port}/mcp",
            scrape_code=scrape_code,
            health=health_payload,
        )
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        stub.shutdown()


def call_tool(url: str, name: str, args: dict[str, Any]) -> Any:
    """One MCP tool call over real HTTP via the SDK client."""

    async def go() -> Any:
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


def test_cli_scrape_and_health_pass_through_the_composition_root(
    stack: Stack,
) -> None:
    assert stack.scrape_code == 0
    evaluation = stack.health["evaluation"]
    assert evaluation["ok"] is True
    assert all(
        entry["status"] == "ok" for entry in evaluation["entries"]
    )
    assert {entry["source_kind"] for entry in evaluation["entries"]} == {
        "HAC",
        "TEAM_SITE",
        "AGENDA",
    }


def test_get_drivers_finds_the_pythagorean_quiz(stack: Stack) -> None:
    drivers = call_tool(stack.url, "get_drivers", {"student_id": "sam"})
    quiz = next(
        d for d in drivers if d["title"] == "Quiz: Pythagorean Theorem"
    )
    assert quiz["kind"] == "QUIZ"
    assert quiz["source_kind"] == "AGENDA"
    assert quiz["due_at"] is not None
    assert quiz["due_at"].startswith("2026-10-26")


def test_get_source_health_reports_all_sources_successful(
    stack: Stack,
) -> None:
    health = call_tool(stack.url, "get_source_health", {})
    by_kind = {entry["source_kind"]: entry for entry in health}
    for kind in ("HAC", "TEAM_SITE", "AGENDA"):
        entry = by_kind[kind]
        assert entry["last_outcome"] == "SUCCESS", entry
        assert entry["last_finished_at"] == "2026-10-22T18:00:00Z"
        assert entry["age_seconds"] == 1800.0  # health clock is NOW+30m
    never = by_kind["CLASSROOM_WEB"]
    assert never["last_outcome"] is None
