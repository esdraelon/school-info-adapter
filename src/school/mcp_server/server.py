"""The MCP server: three tools over the query services.

Pattern: thin Adapter (DESIGN §3) — handlers do argument translation
(string -> enum/datetime) and serialization only; every read decision
lives in QueryServices. Transport is the SDK's Streamable HTTP app,
stateless (each request self-contained — fits the gateway's
intermittent, per-request model and keeps tests free of session
bookkeeping), wrapped in our bearer-token middleware (see auth.py).

``build_server`` is the factory tests and the composition root use:
services and the expected token are injected, nothing is read from the
environment. ``main`` is the thin production shim that does read the
environment (M5 wires it into the gateway/container).
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.types import ASGIApp

from school.domain import ItemStatus, ResourceKind
from school.mcp_server.auth import BearerTokenMiddleware
from school.mcp_server.serializers import (
    JsonDict,
    driver_to_dict,
    resource_to_dict,
    source_health_to_dict,
)
from school.mcp_server.services import QueryServices
from school.store import (
    DriverRepository,
    ResourceRepository,
    RunLogRepository,
    file_connection_factory,
)

DEFAULT_PORT = 8765


def _parse_status(name: str | None) -> ItemStatus | None:
    if name is None:
        return None
    try:
        return ItemStatus[name]
    except KeyError:
        valid = ", ".join(status.name for status in ItemStatus)
        raise ValueError(f"unknown status {name!r}; expected one of {valid}") from None


def _parse_kind(name: str | None) -> ResourceKind | None:
    if name is None:
        return None
    try:
        return ResourceKind[name]
    except KeyError:
        valid = ", ".join(kind.name for kind in ResourceKind)
        raise ValueError(f"unknown kind {name!r}; expected one of {valid}") from None


def _parse_datetime(text: str | None) -> datetime | None:
    """ISO-8601 text -> datetime; naive values are taken as UTC."""
    if text is None:
        return None
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def build_mcp(services: QueryServices) -> FastMCP:
    """The FastMCP app with exactly the three school tools registered."""
    mcp = FastMCP(
        "school-info",
        instructions=(
            "School Information Adapter: due items (drivers), study "
            "resources, and per-source freshness for the household's "
            "students, merged across HAC, the team site, and agenda decks."
        ),
        stateless_http=True,
        # The endpoint lives at the app root, not the SDK's default
        # /mcp: at the default, POST /mcp/ answers 307 -> /mcp, and a
        # redirect is exactly what breaks behind the gateway (scheme /
        # prefix rewriting). Gateway shape: nginx strips the public
        # /mcp/school/ prefix, so requests arrive at / and are served
        # directly — the same shape as the other gateway MCPs.
        streamable_http_path="/",
    )

    @mcp.tool()
    def get_drivers(
        student_id: str,
        course_name: str | None = None,
        status: str | None = None,
        due_on_or_before: str | None = None,
        due_on_or_after: str | None = None,
        limit: int = 200,
    ) -> list[JsonDict]:
        """Due items for one student, merged across sources.

        ``status`` is an ItemStatus name (ASSIGNED, MISSING, DONE,
        GRADED); due bounds are ISO-8601 datetimes and exclude undated
        items. Records carry source provenance and scraped_at.
        """
        records = services.list_drivers(
            student_id,
            course_name=course_name,
            status=_parse_status(status),
            due_on_or_before=_parse_datetime(due_on_or_before),
            due_on_or_after=_parse_datetime(due_on_or_after),
            limit=limit,
        )
        return [driver_to_dict(record) for record in records]

    @mcp.tool()
    def get_resources(
        student_id: str | None = None,
        course_name: str | None = None,
        kind: str | None = None,
        limit: int = 200,
    ) -> list[JsonDict]:
        """Study resources: the student's own plus shared ones.

        ``kind`` is a ResourceKind name (NOTES, TUTORIAL, LINK,
        DOCUMENT). Without ``student_id``, all resources are returned.
        """
        records = services.list_resources(
            student_id,
            course_name=course_name,
            kind=_parse_kind(kind),
            limit=limit,
        )
        return [resource_to_dict(record) for record in records]

    @mcp.tool()
    def get_source_health() -> list[JsonDict]:
        """Per-source freshness: latest run outcome and its age.

        Sources that have never run report null outcome/finished/age.
        """
        return [
            source_health_to_dict(entry)
            for entry in services.source_health(services.now())
        ]

    return mcp


def build_server(services: QueryServices, *, expected_token: str) -> ASGIApp:
    """The deployable ASGI app: MCP behind bearer-token middleware."""
    if not expected_token:
        raise ValueError("expected_token must not be empty")
    return BearerTokenMiddleware(
        build_mcp(services).streamable_http_app(),
        expected_token=expected_token,
    )


@dataclass(frozen=True, kw_only=True)
class ServerConfig:
    """Runtime configuration for the production entry point."""

    port: int
    token: str
    db_path: str


def read_config(env: Mapping[str, str]) -> ServerConfig:
    """Config from environment: SCHOOL_MCP_PORT / _TOKEN / _DB_PATH."""
    return ServerConfig(
        port=int(env.get("SCHOOL_MCP_PORT", str(DEFAULT_PORT))),
        token=env.get("SCHOOL_MCP_TOKEN", ""),
        db_path=env.get("SCHOOL_MCP_DB_PATH", "school.db"),
    )


def main() -> None:
    """Production entry point: build services from config and serve.

    A thin shim — all wiring decisions live in build_server/QueryServices
    and in the M5 composition root.
    """
    config = read_config(os.environ)
    factory = file_connection_factory(config.db_path)
    services = QueryServices(
        drivers=DriverRepository(factory),
        resources=ResourceRepository(factory),
        runs=RunLogRepository(factory),
        clock=lambda: datetime.now(timezone.utc),
    )
    uvicorn.run(
        build_server(services, expected_token=config.token),
        host="127.0.0.1",
        port=config.port,
    )


if __name__ == "__main__":
    main()
