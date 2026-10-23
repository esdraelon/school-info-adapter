"""MCP endpoint (Streamable HTTP, gateway bearer token): tools
get_drivers, get_resources, get_source_health.

Pattern: thin Adapter over query services — no logic in handlers.
Handlers translate arguments and serialize; QueryServices owns every
read decision; BearerTokenMiddleware owns the auth boundary.
"""

from school.mcp_server.auth import BearerTokenMiddleware
from school.mcp_server.serializers import (
    driver_to_dict,
    resource_to_dict,
    source_health_to_dict,
)
from school.mcp_server.server import (
    ServerConfig,
    build_mcp,
    build_server,
    read_config,
)
from school.mcp_server.services import QueryServices, SourceHealth

__all__ = [
    "BearerTokenMiddleware",
    "QueryServices",
    "ServerConfig",
    "SourceHealth",
    "build_mcp",
    "build_server",
    "driver_to_dict",
    "read_config",
    "resource_to_dict",
    "source_health_to_dict",
]
