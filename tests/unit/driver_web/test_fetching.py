"""The production urllib Fetcher, with urlopen stubbed (no network)."""

from types import SimpleNamespace
from urllib.error import URLError

import pytest

from school.driver_web.fetching import FetchError, fetch_url, urllib_fetcher


class _Response:
    def __init__(self, body: bytes, charset: str | None = "utf-8") -> None:
        self._body = body
        self.headers = SimpleNamespace(
            get_content_charset=lambda: charset
        )

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_fetch_url_decodes_body_and_sends_user_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def fake_urlopen(request: object, timeout: float) -> _Response:
        seen["request"] = request
        seen["timeout"] = timeout
        return _Response("caf\u00e9".encode("utf-8"))

    monkeypatch.setattr("school.driver_web.fetching.urlopen", fake_urlopen)
    assert fetch_url("https://example.com/x") == "caf\u00e9"
    request = seen["request"]
    assert request.full_url == "https://example.com/x"  # type: ignore[attr-defined]
    assert "Mozilla" in request.headers["User-agent"]  # type: ignore[attr-defined]
    assert seen["timeout"] == 30.0


def test_fetch_url_defaults_to_utf8_without_charset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "school.driver_web.fetching.urlopen",
        lambda request, timeout: _Response(b"plain", charset=None),
    )
    assert fetch_url("https://example.com") == "plain"


def test_fetch_url_wraps_errors_as_fetch_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(request: object, timeout: float) -> _Response:
        raise URLError("no route")

    monkeypatch.setattr("school.driver_web.fetching.urlopen", boom)
    with pytest.raises(FetchError, match="cannot fetch"):
        fetch_url("https://example.com/down")


def test_urllib_fetcher_binds_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "school.driver_web.fetching.urlopen",
        lambda request, timeout: _Response(b"ok"),
    )
    fetcher = urllib_fetcher(user_agent="TestAgent/1.0", timeout=5.0)
    assert fetcher("https://example.com") == "ok"
