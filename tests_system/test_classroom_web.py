"""Classroom system test: REAL ClassroomSession + Chromium against a
local stub origin (C7: the only replaced piece is classroom.google.com
itself, served as small synthetic pages authored to docs/FIXTURES.md).

Three flows:
(a) extraction — open stub (no auth wall): records from all three
    tabs with correct statuses/dues, through collapsed groups and a
    "View all", including a date-only due and a Done-tab grade;
(b) re-auth — the stub walls the tabs behind a form sign-in (email
    page -> password page -> cookie): the session probes EXPIRED,
    the vault's credentials sign in, the scrape succeeds;
(c) challenge — the stub answers the password submit with a
    challenge page: the driver FAILS with the supervised-re-login
    wording and the stub's request log shows NO interaction after
    the challenge was served.

Chromium resolution: SCHOOL_TEST_CHROMIUM, else Playwright's bundled
browser, else the VM's system Chromium. The probe navigates to a
LOCAL stub page (not a data: URL) so environment-level blocks on
local-network navigation are detected up front; if no candidate can
load the stub, the module skips with the collected errors as the
reason, and the fake-session fallback suite
(test_classroom_fallback.py) carries the system-level assertions.
"""

import os
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from school.domain import ItemStatus, RunOutcome, StudentRef
from school.driver_classroom_web import (
    RELOGIN_MESSAGE,
    ClassroomSession,
    ClassroomStudentConfig,
    ClassroomWebDriver,
    ProfileConfig,
)
from school.spi import ScrapeContext
from school.vault import Vault, VaultCredentials

NOW = datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)
SAM = StudentRef(id="sam", display_name="Sam")
ACCOUNT = "sam@example.test"
PASSWORD = "<redacted>"
COURSES = [
    "Math 8",
    "Goodie - 8th Grade ELA",
    "Gholizadeh 8th Science",
    "26-27 Wind Ensemble (TMEA Honor Band)",
]

# (heading, visible rows, rows behind "View all") — row = (text, href)
TAB_GROUPS: dict[str, list[tuple[str, list[tuple[str, str]], list[tuple[str, str]]]]] = {
    "/a/not-turned-in/all": [
        (
            "This week",
            [("Fractions Quiz Math 8 Tomorrow, 9:00 AM", "/c/1")],
            [("Workbook Practice Math 8 Friday, Oct 23", "/c/2")],
        ),
        (
            "No due date",
            [
                (
                    "8th Grade ELA Portfolio Goodie - 8th Grade ELA "
                    "Posted Monday, Aug 24",
                    "/c/3",
                )
            ],
            [],
        ),
        (
            "Later",
            [
                (
                    "Reading Project 26-27 Wind Ensemble "
                    "(TMEA Honor Band) Wednesday, Dec 9",
                    "/c/4",
                )
            ],
            [],
        ),
    ],
    "/a/missing/all": [
        (
            "Earlier",
            [
                (
                    "Newton's 2nd Law Calculations Gholizadeh 8th "
                    "Science Friday, Oct 9",
                    "/c/5",
                )
            ],
            [],
        )
    ],
    "/a/turned-in/all": [
        (
            "This week",
            [
                (
                    "10/21 Scale Run Through with Chromatic "
                    "(Wind Ensemble) 26-27 Wind Ensemble "
                    "(TMEA Honor Band) 87.77 points out of possible "
                    "100 Not turned in",
                    "/c/6",
                ),
                ("Choir Reflection Goodie - 8th Grade ELA Turned in", "/c/7"),
            ],
            [],
        )
    ],
}


def _tab_html(groups: list) -> str:
    sections = []
    for index, (heading, visible, hidden) in enumerate(groups):
        count = len(visible) + len(hidden)
        rows = "".join(
            f'<a href="{href}">{text}</a>' for text, href in visible
        )
        view_all = ""
        if hidden:
            extra = "".join(
                f'<a href="{href}">{text}</a>' for text, href in hidden
            )
            view_all = (
                f'<button data-viewall="g{index}x">View all</button>'
                f'<span id="g{index}x" hidden>{extra}</span>'
            )
        sections.append(
            f"<section><h2>{heading}</h2>"
            f'<button aria-expanded="false" data-toggle="g{index}">'
            f"{heading} {count}</button>"
            f'<div id="g{index}" hidden>{rows}{view_all}</div></section>'
        )
    options = "".join(
        f"<option>{course}</option>" for course in ["All classes", *COURSES]
    )
    return (
        "<!doctype html><html><body>"
        '<nav aria-label="Page navigation">'
        '<a href="/a/not-turned-in/all">Assigned</a>'
        '<a href="/a/missing/all">Missing</a>'
        '<a href="/a/turned-in/all">Done</a></nav>'
        "<h1>Google Classroom Classroom To-do</h1>"
        f'<label>Class filter <select aria-label="Class filter">'
        f"{options}</select></label>"
        f"<main>{''.join(sections)}</main>"
        "<script>"
        "document.querySelectorAll('[data-toggle]').forEach(b => "
        "b.addEventListener('click', () => {"
        "const body = document.getElementById(b.dataset.toggle);"
        "body.hidden = !body.hidden;"
        "b.setAttribute('aria-expanded', String(!body.hidden));}));"
        "document.querySelectorAll('[data-viewall]').forEach(b => "
        "b.addEventListener('click', () => {"
        "document.getElementById(b.dataset.viewall).hidden = false;"
        "b.hidden = true;}));"
        "</script></body></html>"
    )


_SIGNIN_EMAIL_HTML = (
    "<!doctype html><html><body><h1>Sign in</h1>"
    '<form method="get" action="/signin/password">'
    '<label>Email <input type="email" name="email"></label>'
    '<button type="submit">Next</button></form></body></html>'
)


def _signin_password_html(error: bool) -> str:
    message = "<p>Wrong password. Try again.</p>" if error else ""
    return (
        "<!doctype html><html><body><h1>Welcome</h1>"
        f"{message}"
        '<form method="post" action="/do-login">'
        '<label>Password <input type="password" name="password"></label>'
        '<button type="submit">Next</button></form></body></html>'
    )


_CHALLENGE_HTML = (
    "<!doctype html><html><body><h1>Verify it's you</h1>"
    "<p>2-Step Verification: enter the code sent to your phone.</p>"
    "</body></html>"
)


class _StubServer(ThreadingHTTPServer):
    mode: str = "open"  # open | auth | challenge
    log: list[str] = []


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass  # the structured request log lives on the server

    @property
    def _stub(self) -> _StubServer:
        return self.server  # type: ignore[return-value]

    def _record(self) -> None:
        if self.path != "/favicon.ico":
            self._stub.log.append(f"{self.command} {self.path}")

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str, cookie: bool = False) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        if cookie:
            self.send_header("Set-Cookie", "stub_auth=1; Path=/")
        self.end_headers()

    def _authenticated(self) -> bool:
        return "stub_auth=1" in (self.headers.get("Cookie") or "")

    def do_GET(self) -> None:
        self._record()
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/favicon.ico":
            self.send_response(404)
            self.end_headers()
            return
        if path in TAB_GROUPS:
            if self._stub.mode != "open" and not self._authenticated():
                self._redirect("/signin")
                return
            self._send_html(_tab_html(TAB_GROUPS[path]))
            return
        if path == "/signin":
            self._send_html(_SIGNIN_EMAIL_HTML)
            return
        if path == "/signin/password":
            error = "error=1" in (parsed.query or "")
            self._send_html(_signin_password_html(error))
            return
        if path == "/challenge":
            self._send_html(_CHALLENGE_HTML)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        self._record()
        if urlparse(self.path).path != "/do-login":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length") or 0)
        form = parse_qs(self.rfile.read(length).decode("utf-8"))
        password = (form.get("password") or [""])[0]
        if self._stub.mode == "challenge":
            self._redirect("/challenge")
            return
        if password == PASSWORD:
            self._redirect("/a/not-turned-in/all", cookie=True)
            return
        self._redirect("/signin/password?error=1")


@contextmanager
def stub_server(mode: str) -> Iterator[tuple[str, list[str]]]:
    server = _StubServer(("127.0.0.1", 0), _Handler)
    server.mode = mode
    server.log = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield origin, server.log
    finally:
        server.shutdown()
        server.server_close()


# The stub origin is 127.0.0.1, and current Chromium builds gate
# local-network navigations behind Local Network Access checks; the
# flag below disables them for the TEST browser only (production
# navigates to the public classroom.google.com origin).
_TEST_LAUNCH_ARGS = (
    "--password-store=basic",
    "--no-first-run",
    "--no-sandbox",
    "--disable-features=LocalNetworkAccessChecks",
)


class _ProbeHandler(BaseHTTPRequestHandler):
    """Serves one trivial page: the probe target."""

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        body = b"<title>probe</title><h1>ok</h1>"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _probe_launch(executable: Path | None) -> None:
    """Launch ``executable`` and load a LOCAL page with it.

    A data:-URL probe is not enough: this environment's system
    Chromium launches fine but refuses local-network navigations
    (ERR_BLOCKED_BY_LOCAL_NETWORK_ACCESS_CHECKS), which is exactly
    what the stub tests need. Probing the real shape means the skip
    reason names the real blocker.
    """
    from playwright.sync_api import sync_playwright

    server = ThreadingHTTPServer(("127.0.0.1", 0), _ProbeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/"
        with tempfile.TemporaryDirectory() as profile:
            with sync_playwright() as pw:
                kwargs: dict[str, Any] = {
                    "headless": True,
                    "args": list(_TEST_LAUNCH_ARGS),
                }
                if executable is not None:
                    kwargs["executable_path"] = str(executable)
                context = pw.chromium.launch_persistent_context(
                    profile, **kwargs
                )
                page = (
                    context.pages[0]
                    if context.pages
                    else context.new_page()
                )
                page.goto(url)
                assert page.title() == "probe"
                context.close()
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="module")
def chromium() -> Path | None:
    candidates: list[Path | None] = []
    env = os.environ.get("SCHOOL_TEST_CHROMIUM")
    if env:
        candidates.append(Path(env))
    candidates.append(None)  # Playwright's bundled Chromium
    system = Path("/opt/meta-chromium/chrome")
    if system.exists():
        candidates.append(system)
    errors: list[str] = []
    for candidate in candidates:
        try:
            _probe_launch(candidate)
            return candidate
        except Exception as exc:  # collect every failure for the skip
            first_line = str(exc).splitlines()[0] if str(exc) else repr(exc)
            errors.append(f"{candidate or 'bundled'}: {first_line}")
    pytest.skip(
        "no Chromium in this environment can load a local page "
        "for the classroom system test: " + " | ".join(errors)
    )


def _driver(
    tmp_path: Path,
    origin: str,
    executable: Path | None,
    credentials: VaultCredentials | None,
) -> ClassroomWebDriver:
    config = ProfileConfig(
        profile_dir=tmp_path / "profile",
        base_url=origin,
        executable_path=executable,
        launch_args=_TEST_LAUNCH_ARGS,
    )
    return ClassroomWebDriver(
        session_factory=lambda: ClassroomSession(config),
        credentials=credentials,
        student=ClassroomStudentConfig(
            account_id=ACCOUNT, expected_email=ACCOUNT
        ),
        clock=lambda: NOW,
    )


def _vault_credentials(tmp_path: Path) -> VaultCredentials:
    vault = Vault(
        vault_path=tmp_path / "vault.json",
        key_path=tmp_path / "vault.key",
    )
    vault.unlock()
    vault.put(f"{ACCOUNT}.username", ACCOUNT)
    vault.put(f"{ACCOUNT}.password", PASSWORD)
    return VaultCredentials(vault)


def test_extraction_from_all_three_tabs(
    tmp_path: Path, chromium: Path | None
) -> None:
    with stub_server("open") as (origin, _log):
        driver = _driver(tmp_path, origin, chromium, credentials=None)
        result = driver.scrape(ScrapeContext(student=SAM, now=NOW))
    assert result.run.outcome is RunOutcome.SUCCESS, result.run.message
    assert result.run.found == 6
    by_title = {record.title: record for record in result.drivers}

    quiz = by_title["Fractions Quiz"]
    assert quiz.status is ItemStatus.ASSIGNED
    assert quiz.due_at == datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc)
    workbook = by_title["Workbook Practice"]  # arrived via "View all"
    assert workbook.status is ItemStatus.ASSIGNED
    assert workbook.due_at == datetime(
        2026, 10, 23, 23, 59, tzinfo=timezone.utc
    )
    portfolio = by_title["8th Grade ELA Portfolio"]
    assert portfolio.due_at is None  # "Posted Monday, Aug 24"
    project = by_title["Reading Project"]
    assert project.due_at == datetime(
        2026, 12, 9, 23, 59, tzinfo=timezone.utc
    )
    newton = by_title["Newton's 2nd Law Calculations"]
    assert newton.status is ItemStatus.MISSING
    assert newton.due_at == datetime(
        2026, 10, 9, 23, 59, tzinfo=timezone.utc
    )
    scale = by_title["10/21 Scale Run Through with Chromatic (Wind Ensemble)"]
    assert scale.status is ItemStatus.GRADED
    assert scale.score_text == "87.77 points out of possible 100"
    choir = by_title["Choir Reflection"]
    assert choir.status is ItemStatus.DONE
    assert all(
        record.source_ref and record.source_ref.startswith("/c/")
        for record in result.drivers
    )


def test_reauth_with_vault_credentials_then_scrape(
    tmp_path: Path, chromium: Path | None
) -> None:
    credentials = _vault_credentials(tmp_path)
    with stub_server("auth") as (origin, log):
        driver = _driver(tmp_path, origin, chromium, credentials)
        result = driver.scrape(ScrapeContext(student=SAM, now=NOW))
    assert result.run.outcome is RunOutcome.SUCCESS, result.run.message
    assert result.run.found == 6
    assert "POST /do-login" in log  # the re-auth really happened


def test_challenge_stops_everything(
    tmp_path: Path, chromium: Path | None
) -> None:
    credentials = _vault_credentials(tmp_path)
    with stub_server("challenge") as (origin, log):
        driver = _driver(tmp_path, origin, chromium, credentials)
        result = driver.scrape(ScrapeContext(student=SAM, now=NOW))
    assert result.run.outcome is RunOutcome.FAILURE
    assert result.run.message is not None
    assert RELOGIN_MESSAGE in result.run.message
    challenge_at = log.index("GET /challenge")
    # NO post-challenge interaction reached the stub at all.
    assert log[challenge_at + 1 :] == []
