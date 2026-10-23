"""Server factory + tool handlers, exercised in-process via FastMCP.

Handlers are thin by design; these tests prove the tool surface is
exactly the three PLAN §3 tools and that argument translation
(status/kind names, ISO datetimes) reaches the services correctly.
Full HTTP behaviour is the system suite's job (tests_system/).
"""

import asyncio
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

import school.mcp_server.server as server_module
from school.domain import ItemStatus, RunOutcome, SourceKind, SourceRun
from school.mcp_server.auth import BearerTokenMiddleware
from school.mcp_server.server import build_mcp, build_server, main, read_config
from school.mcp_server.services import QueryServices
from school.store import DriverRepository, ResourceRepository, RunLogRepository
from mcp_server.conftest import MATH, NOW, make_driver, make_resource


def _call(mcp: Any, name: str, args: dict[str, Any]) -> Any:
    """One in-process tool call, returning the structured payload."""

    async def go() -> Any:
        _content, structured = await mcp.call_tool(name, args)
        return structured["result"]

    return asyncio.run(go())


def _seeded(services: QueryServices, drivers_repo: DriverRepository,
            resources_repo: ResourceRepository,
            runs_repo: RunLogRepository) -> QueryServices:
    drivers_repo.upsert_drivers(
        [
            make_driver(
                title="Quiz: Pythagorean Theorem",
                status=ItemStatus.ASSIGNED,
            ),
            make_driver(
                title="Timeline Project",
                status=ItemStatus.GRADED,
                score_text="87.77",
            ),
        ]
    )
    resources_repo.upsert_resources(
        [make_resource(title="Volume notes", url="https://x.test/1")]
    )
    runs_repo.append_run(
        SourceRun(
            source_kind=SourceKind.HAC,
            started_at=NOW - timedelta(minutes=5),
            finished_at=NOW,
            outcome=RunOutcome.SUCCESS,
        )
    )
    return services


def test_tool_surface_is_exactly_the_three_tools(
    services: QueryServices,
) -> None:
    mcp = build_mcp(services)

    async def names() -> set[str]:
        return {tool.name for tool in await mcp.list_tools()}

    assert asyncio.run(names()) == {
        "get_drivers",
        "get_resources",
        "get_source_health",
    }


def test_get_drivers_handler_translates_and_serializes(
    services: QueryServices,
    drivers_repo: DriverRepository,
    resources_repo: ResourceRepository,
    runs_repo: RunLogRepository,
) -> None:
    mcp = build_mcp(_seeded(services, drivers_repo, resources_repo, runs_repo))
    all_drivers = _call(mcp, "get_drivers", {"student_id": "sam"})
    assert {d["title"] for d in all_drivers} == {
        "Quiz: Pythagorean Theorem",
        "Timeline Project",
    }
    graded = _call(
        mcp, "get_drivers", {"student_id": "sam", "status": "GRADED"}
    )
    assert [d["title"] for d in graded] == ["Timeline Project"]
    assert graded[0]["score_text"] == "87.77"
    assert graded[0]["status"] == "GRADED"
    windowed = _call(
        mcp,
        "get_drivers",
        {"student_id": "sam", "due_on_or_after": "2999-01-01"},
    )
    assert windowed == []  # both seeded drivers are undated -> excluded


def test_get_resources_handler(
    services: QueryServices,
    drivers_repo: DriverRepository,
    resources_repo: ResourceRepository,
    runs_repo: RunLogRepository,
) -> None:
    mcp = build_mcp(_seeded(services, drivers_repo, resources_repo, runs_repo))
    resources = _call(mcp, "get_resources", {"student_id": "sam"})
    assert [r["title"] for r in resources] == ["Volume notes"]
    assert resources[0]["course"]["name"] == MATH.name
    none_kind = _call(
        mcp, "get_resources", {"student_id": "sam", "kind": "TUTORIAL"}
    )
    assert none_kind == []


def test_get_source_health_handler(
    services: QueryServices,
    drivers_repo: DriverRepository,
    resources_repo: ResourceRepository,
    runs_repo: RunLogRepository,
) -> None:
    mcp = build_mcp(_seeded(services, drivers_repo, resources_repo, runs_repo))
    health = _call(mcp, "get_source_health", {})
    by_kind = {entry["source_kind"]: entry for entry in health}
    assert by_kind["HAC"]["last_outcome"] == "SUCCESS"
    assert by_kind["HAC"]["age_seconds"] == 0.0  # clock pinned at NOW
    assert by_kind["AGENDA"]["last_outcome"] is None


def test_unknown_status_name_is_a_tool_error(
    services: QueryServices,
) -> None:
    mcp = build_mcp(services)
    with pytest.raises(Exception, match="unknown status"):
        _call(mcp, "get_drivers", {"student_id": "sam", "status": "LATE"})


def test_unknown_kind_name_is_a_tool_error(
    services: QueryServices,
) -> None:
    mcp = build_mcp(services)
    with pytest.raises(Exception, match="unknown kind"):
        _call(mcp, "get_resources", {"kind": "VIDEO"})


def test_call_tool_text_content_is_the_same_json(
    services: QueryServices,
    drivers_repo: DriverRepository,
    resources_repo: ResourceRepository,
    runs_repo: RunLogRepository,
) -> None:
    mcp = build_mcp(_seeded(services, drivers_repo, resources_repo, runs_repo))

    async def go() -> Any:
        content, _structured = await mcp.call_tool(
            "get_resources", {"student_id": "sam"}
        )
        return [json.loads(block.text) for block in content]

    assert asyncio.run(go()) == [
        {
            "student": {"id": "sam", "display_name": "Sam"},
            "course": {"name": "Math 8", "period": "3", "teacher": "VanDyke"},
            "title": "Volume notes",
            "kind": "NOTES",
            "url": "https://x.test/1",
            "source_kind": "AGENDA",
            "scraped_at": "2026-10-22T18:00:00Z",
        }
    ]


# --- factory / config / entry shim --------------------------------------


def test_build_server_wraps_in_auth_and_rejects_empty_token(
    services: QueryServices,
) -> None:
    app = build_server(services, expected_token="tok")
    assert isinstance(app, BearerTokenMiddleware)
    with pytest.raises(ValueError, match="must not be empty"):
        build_server(services, expected_token="")


def test_read_config_defaults_and_overrides() -> None:
    defaults = read_config({})
    assert defaults.port == server_module.DEFAULT_PORT
    assert defaults.token == ""
    assert defaults.db_path == "school.db"
    custom = read_config(
        {
            "SCHOOL_MCP_PORT": "9999",
            "SCHOOL_MCP_TOKEN": "sekrit",
            "SCHOOL_MCP_DB_PATH": "/tmp/x.db",
        }
    )
    assert (custom.port, custom.token, custom.db_path) == (
        9999,
        "sekrit",
        "/tmp/x.db",
    )


def test_main_wires_config_into_uvicorn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(app: Any, **kwargs: Any) -> None:
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setattr(server_module.uvicorn, "run", fake_run)
    monkeypatch.setenv("SCHOOL_MCP_TOKEN", "sekrit")
    monkeypatch.setenv("SCHOOL_MCP_PORT", "8123")
    monkeypatch.setenv("SCHOOL_MCP_DB_PATH", str(tmp_path / "school.db"))
    main()
    assert captured["port"] == 8123
    assert captured["host"] == "127.0.0.1"
    assert isinstance(captured["app"], BearerTokenMiddleware)
