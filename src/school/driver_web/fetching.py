"""HTTP fetching: the web drivers' I/O boundary.

Pattern: injected callable (C2/C3). Every web component takes a
``Fetcher`` — a plain ``Callable[[str], str]`` mapping URL -> page text —
so unit tests inject fixture-backed fakes and never touch the network.
The production default is urllib with a browser-ish User-Agent (the
public Google pages serve their full content to it) and a timeout.
"""

from collections.abc import Callable
from urllib.error import URLError
from urllib.request import Request, urlopen

# The public Sites/Slides pages are served in full to a browser UA.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
DEFAULT_TIMEOUT_SECONDS = 30.0

Fetcher = Callable[[str], str]


class FetchError(Exception):
    """A page could not be fetched.

    Drivers convert this into a FAILURE/PARTIAL SourceRun; it never
    escapes a scrape as an exception (per the SourceDriver contract).
    """


def fetch_url(
    url: str,
    *,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Fetch ``url`` as text via urllib (the production Fetcher)."""
    request = Request(url, headers={"User-Agent": user_agent})
    try:
        with urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            raw: bytes = response.read()
            return raw.decode(charset, errors="replace")
    except (URLError, OSError) as exc:
        raise FetchError(f"cannot fetch {url}: {exc}") from exc


def urllib_fetcher(
    *,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Fetcher:
    """Build the production Fetcher with a fixed UA/timeout."""

    def fetch(url: str) -> str:
        return fetch_url(url, user_agent=user_agent, timeout=timeout)

    return fetch
