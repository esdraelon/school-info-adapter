"""Classroom system test, FALLBACK mode (no runnable Chromium here).

The real-Chromium suite (test_classroom_web.py) skips in this
environment: Playwright's bundled Chromium cannot be downloaded
(CDN failure, recorded in the skip reason) and the VM's system
Chromium refuses local-network navigations. Per the M6 fallback
plan, this suite carries the same three system-level assertions
with everything real EXCEPT the browser boundary: the real
ClassroomWebDriver, real extractors, real ReauthHelper, and the
real AES-GCM Vault — driven through scripted session/page fakes
implementing the production protocols.

When a Chromium that can load a local page exists (e.g. hal9k after
``playwright install chromium``), test_classroom_web.py supersedes
this suite; both assert the same flows.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from school.domain import ItemStatus, RunOutcome, StudentRef
from school.driver_classroom_web import (
    RELOGIN_MESSAGE,
    ClassroomStudentConfig,
    ClassroomWebDriver,
    SessionState,
    TodoTab,
)
from school.driver_classroom_web.pages import Row
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


@dataclass
class _Group:
    rows: list[Row] = field(default_factory=list)
    hidden: list[Row] = field(default_factory=list)
    expanded: bool = False


class _TodoPage:
    """TodoPage protocol over in-memory groups (the stub's shape)."""

    def __init__(self, groups: dict[str, _Group]) -> None:
        self._groups = groups

    def course_names(self) -> list[str]:
        return list(COURSES)

    def group_headings(self) -> list[str]:
        return list(self._groups)

    def expand_group(self, heading: str) -> None:
        group = self._groups[heading]
        if not group.expanded:
            group.expanded = True
            group.rows = [*group.rows, *group.hidden]
            group.hidden = []

    def rows_in_group(self, heading: str) -> list[Row]:
        group = self._groups[heading]
        return list(group.rows) if group.expanded else []


class _SignInPage:
    """SignInPage protocol, scripted; records mutating interactions."""

    def __init__(self, session: "_Session", *, challenge: bool) -> None:
        self._session = session
        self._challenge = challenge
        self._stage = "email"
        self._text = "Sign in"
        self.mutations: list[str] = []

    def has_email_field(self) -> bool:
        return self._stage == "email"

    def fill_email(self, email: str) -> None:
        self.mutations.append("fill_email")

    def has_password_field(self) -> bool:
        return self._stage == "password"

    def fill_password(self, password: str) -> None:
        self.mutations.append("fill_password")

    def advance(self) -> None:
        self.mutations.append("advance")
        if self._stage == "email":
            self._stage = "password"
            self._text = "Welcome: enter your password"
            return
        self._stage = "done"
        if self._challenge:
            self._text = "Verify it's you: 2-Step Verification"
        else:
            self._text = "Google Classroom Classroom To-do"
            self._session.authenticated = True

    def visible_text(self) -> str:
        return self._text

    def has_todo_structure(self) -> bool:
        return self._stage == "done" and not self._challenge


class _Session:
    """The driver's session port, scripted (probe/open/close)."""

    def __init__(self, *, expired: bool, challenge: bool = False) -> None:
        self.authenticated = not expired
        self.last_probe_detail = "fallback fake probe"
        self._sign_in = _SignInPage(self, challenge=challenge)
        self.closed = False

    def probe(self) -> SessionState:
        return (
            SessionState.AUTHENTICATED
            if self.authenticated
            else SessionState.EXPIRED
        )

    def open_todo(self, tab: TodoTab) -> _TodoPage:
        return _TodoPage(_groups_for(tab))

    def sign_in_page(self) -> _SignInPage:
        return self._sign_in

    def close(self) -> None:
        self.closed = True


def _groups_for(tab: TodoTab) -> dict[str, _Group]:
    if tab is TodoTab.ASSIGNED:
        return {
            "This week": _Group(
                rows=[
                    Row(
                        name="Fractions Quiz Math 8 Tomorrow, 9:00 AM",
                        href="/c/1",
                    )
                ],
                hidden=[
                    Row(
                        name="Workbook Practice Math 8 Friday, Oct 23",
                        href="/c/2",
                    )
                ],
            ),
            "Later": _Group(
                rows=[
                    Row(
                        name="Reading Project 26-27 Wind Ensemble "
                        "(TMEA Honor Band) Wednesday, Dec 9",
                        href="/c/4",
                    )
                ]
            ),
        }
    if tab is TodoTab.MISSING:
        return {
            "Earlier": _Group(
                rows=[
                    Row(
                        name="Newton's 2nd Law Calculations "
                        "Gholizadeh 8th Science Friday, Oct 9",
                        href="/c/5",
                    )
                ]
            )
        }
    return {
        "This week": _Group(
            rows=[
                Row(
                    name="10/21 Scale Run Through with Chromatic "
                    "(Wind Ensemble) 26-27 Wind Ensemble "
                    "(TMEA Honor Band) 87.77 points out of possible "
                    "100 Not turned in",
                    href="/c/6",
                )
            ]
        )
    }


def _driver(
    session: _Session, credentials: VaultCredentials | None
) -> ClassroomWebDriver:
    return ClassroomWebDriver(
        session_factory=lambda: session,
        credentials=credentials,
        student=ClassroomStudentConfig(
            account_id=ACCOUNT, expected_email=ACCOUNT
        ),
        clock=lambda: NOW,
    )


def _credentials(tmp_path: Path) -> VaultCredentials:
    vault = Vault(
        vault_path=tmp_path / "vault.json",
        key_path=tmp_path / "vault.key",
    )
    vault.unlock()
    vault.put(f"{ACCOUNT}.username", ACCOUNT)
    vault.put(f"{ACCOUNT}.password", PASSWORD)
    return VaultCredentials(vault)


def test_extraction_from_all_three_tabs() -> None:
    session = _Session(expired=False)
    result = _driver(session, None).scrape(
        ScrapeContext(student=SAM, now=NOW)
    )
    assert result.run.outcome is RunOutcome.SUCCESS, result.run.message
    assert result.run.found == 5
    by_title = {record.title: record for record in result.drivers}
    assert by_title["Fractions Quiz"].due_at == datetime(
        2026, 10, 23, 9, 0, tzinfo=timezone.utc
    )
    assert by_title["Workbook Practice"].due_at == datetime(
        2026, 10, 23, 23, 59, tzinfo=timezone.utc
    )
    assert by_title["Reading Project"].due_at == datetime(
        2026, 12, 9, 23, 59, tzinfo=timezone.utc
    )
    newton = by_title["Newton's 2nd Law Calculations"]
    assert newton.status is ItemStatus.MISSING
    scale = by_title["10/21 Scale Run Through with Chromatic (Wind Ensemble)"]
    assert scale.status is ItemStatus.GRADED
    assert scale.score_text == "87.77 points out of possible 100"
    assert session.closed is True


def test_reauth_with_vault_credentials_then_scrape(
    tmp_path: Path,
) -> None:
    session = _Session(expired=True)
    result = _driver(session, _credentials(tmp_path)).scrape(
        ScrapeContext(student=SAM, now=NOW)
    )
    assert result.run.outcome is RunOutcome.SUCCESS, result.run.message
    assert result.run.found == 5
    assert session.sign_in_page().mutations == [
        "fill_email",
        "advance",
        "fill_password",
        "advance",
    ]


def test_challenge_stops_everything(tmp_path: Path) -> None:
    session = _Session(expired=True, challenge=True)
    result = _driver(session, _credentials(tmp_path)).scrape(
        ScrapeContext(student=SAM, now=NOW)
    )
    assert result.run.outcome is RunOutcome.FAILURE
    assert result.run.message is not None
    assert RELOGIN_MESSAGE in result.run.message
    # The mutation log ENDS at the submit that revealed the challenge:
    # no fill, no advance, no tab extraction after it.
    assert session.sign_in_page().mutations == [
        "fill_email",
        "advance",
        "fill_password",
        "advance",
    ]
    assert result.drivers == []
