"""BearerTokenMiddleware: the auth boundary, over a dummy ASGI app."""

import asyncio

import httpx
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from school.mcp_server.auth import BearerTokenMiddleware

TOKEN = "test-token"


def _dummy_app(reached: list[Scope]) -> ASGIApp:
    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        reached.append(scope)
        body = b'{"ok": true}'
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})

    return app


def _get(app: ASGIApp, headers: dict[str, str]) -> httpx.Response:
    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return await client.get("/mcp", headers=headers)

    return asyncio.run(go())


def test_missing_token_is_401_and_app_is_never_reached() -> None:
    reached: list[Scope] = []
    app = BearerTokenMiddleware(_dummy_app(reached), expected_token=TOKEN)
    response = _get(app, {})
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}
    assert reached == []


def test_wrong_token_is_401() -> None:
    reached: list[Scope] = []
    app = BearerTokenMiddleware(_dummy_app(reached), expected_token=TOKEN)
    response = _get(app, {"Authorization": "Bearer nope"})
    assert response.status_code == 401
    assert reached == []


def test_non_bearer_scheme_is_401() -> None:
    reached: list[Scope] = []
    app = BearerTokenMiddleware(_dummy_app(reached), expected_token=TOKEN)
    response = _get(app, {"Authorization": f"Basic {TOKEN}"})
    assert response.status_code == 401
    assert reached == []


def test_correct_token_passes_through() -> None:
    reached: list[Scope] = []
    app = BearerTokenMiddleware(_dummy_app(reached), expected_token=TOKEN)
    response = _get(app, {"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert len(reached) == 1


def test_non_http_scopes_pass_through_unauthenticated() -> None:
    reached: list[Scope] = []
    app = BearerTokenMiddleware(_dummy_app(reached), expected_token=TOKEN)

    async def go() -> None:
        async def receive() -> Message:
            return {"type": "lifespan.startup"}

        async def send(message: Message) -> None:
            pass

        await app({"type": "lifespan"}, receive, send)

    asyncio.run(go())
    assert len(reached) == 1
    assert reached[0]["type"] == "lifespan"
