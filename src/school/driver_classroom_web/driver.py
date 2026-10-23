"""ClassroomWebDriver: the Classroom source as an spi.SourceDriver.

Pattern: Strategy (SourceDriver) over a session port. The scrape:

1. Build a session from the injected factory and probe it.
2. EXPIRED -> at most ONE re-auth attempt from vault credentials
   (ReauthHelper, which itself hard-stops at any Google challenge).
   Still not authenticated -> a FAILURE run whose message carries
   the machine-actionable phrase ``session expired; supervised
   re-login needed`` — the digest/health layer surfaces it verbatim
   and a human does the supervised re-login. Never a retry loop:
   automated sign-in attempts are the highest-risk event in this
   system (PLAN §4).
3. UNKNOWN probe -> FAILURE with the probe detail (we say what we
   saw instead of guessing).
4. AUTHENTICATED -> extract all three tabs; a tab that fails to
   parse is named in a PARTIAL run (good tabs still flow); all tabs
   failing is a FAILURE.

Failures are run outcomes, never raises (the SourceDriver contract),
and the session is always closed.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from school.domain import (
    DriverRecord,
    Found,
    Missing,
    RunOutcome,
    SourceKind,
    SourceRun,
)
from school.driver_classroom_web.extractors import extract_todo, record_for_row
from school.driver_classroom_web.pages import SignInPage, TodoPage
from school.driver_classroom_web.reauth import ReauthHelper, ReauthOutcome
from school.driver_classroom_web.session import SessionState
from school.driver_classroom_web.tabs import TodoTab
from school.spi import ScrapeContext, ScrapeResult
from school.vault import VaultCredentials, VaultError

#: The escalation phrase: health/digest surfaces match on it.
RELOGIN_MESSAGE = "session expired; supervised re-login needed"


@runtime_checkable
class ClassroomSessionLike(Protocol):
    """The session shape the driver consumes (ClassroomSession in
    production; scripted fakes in tests)."""

    @property
    def last_probe_detail(self) -> str:
        """What the last probe saw, for failure messages."""
        ...

    def probe(self) -> SessionState:
        """Classify the persisted session's state."""
        ...

    def open_todo(self, tab: TodoTab) -> TodoPage:
        """Navigate to a To-do tab."""
        ...

    def sign_in_page(self) -> SignInPage:
        """The current page as a sign-in form (for re-auth)."""
        ...

    def close(self) -> None:
        """Release the browser."""
        ...


@dataclass(frozen=True, kw_only=True)
class ClassroomStudentConfig:
    """Which account this driver serves.

    ``account_id`` keys the vault entries (``<account_id>.username`` /
    ``.password``); ``expected_email`` is the address a human would
    re-login as, named in escalation messages.
    """

    account_id: str
    expected_email: str


class ClassroomWebDriver:
    """SourceDriver strategy over a probed Classroom browser session."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], ClassroomSessionLike],
        credentials: VaultCredentials | None,
        student: ClassroomStudentConfig,
        clock: Callable[[], datetime],
        reauth: ReauthHelper | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._credentials = credentials
        self._student = student
        self._clock = clock
        self._reauth = reauth if reauth is not None else ReauthHelper()

    @property
    def kind(self) -> SourceKind:
        return SourceKind.CLASSROOM_WEB

    def scrape(self, context: ScrapeContext) -> ScrapeResult:
        """Scrape Classroom once; failures are run outcomes."""
        try:
            session = self._session_factory()
        except Exception as exc:
            return self._result(
                context,
                outcome=RunOutcome.FAILURE,
                message=f"classroom session failed to start: {exc}",
            )
        try:
            return self._scrape_with_session(session, context)
        finally:
            try:
                session.close()
            except Exception:  # a dead browser must not sink the run
                pass

    # -- internals ----------------------------------------------------

    def _scrape_with_session(
        self, session: ClassroomSessionLike, context: ScrapeContext
    ) -> ScrapeResult:
        try:
            state = session.probe()
        except Exception as exc:
            return self._failure(context, f"classroom probe failed: {exc}")
        if state is SessionState.EXPIRED:
            failure = self._reauthenticate(session)
            if failure is not None:
                return self._failure(context, failure)
            try:
                state = session.probe()
            except Exception as exc:
                return self._failure(
                    context, f"classroom probe failed after re-auth: {exc}"
                )
        if state is SessionState.UNKNOWN:
            return self._failure(
                context,
                "classroom session probe inconclusive "
                f"({session.last_probe_detail})",
            )
        if state is not SessionState.AUTHENTICATED:
            return self._failure(
                context,
                f"{RELOGIN_MESSAGE} for "
                f"{self._student.expected_email}: session still "
                f"{state.value} after re-auth",
            )
        return self._extract_all(session, context)

    def _reauthenticate(
        self, session: ClassroomSessionLike
    ) -> str | None:
        """One re-auth attempt; None on success, else the FAILURE
        message (already carrying the escalation wording)."""
        email = self._student.expected_email
        prefix = f"{RELOGIN_MESSAGE} for {email}"
        if self._credentials is None:
            return f"{prefix}: no vault credentials configured"
        try:
            found = self._credentials.credentials_for(
                self._student.account_id
            )
        except VaultError as exc:
            return f"credential vault error: {exc}"
        match found:
            case Missing(reason=reason):
                return f"{prefix}: {reason}"
            case Found(value=(username, password)):
                pass
        try:
            outcome = self._reauth.attempt(
                session.sign_in_page(),
                username=username,
                password=password,
            )
        except Exception as exc:
            return f"{prefix}: re-auth attempt errored: {exc}"
        return {
            ReauthOutcome.SIGNED_IN: None,
            ReauthOutcome.CHALLENGE: (
                f"{prefix}: re-auth stopped at a Google challenge "
                "(challenges are never fought unattended)"
            ),
            ReauthOutcome.BAD_CREDENTIALS: (
                f"{prefix}: the vault credentials were rejected"
            ),
            ReauthOutcome.FORM_NOT_FOUND: (
                f"{prefix}: the sign-in form was not recognized"
            ),
        }[outcome]

    def _extract_all(
        self, session: ClassroomSessionLike, context: ScrapeContext
    ) -> ScrapeResult:
        records: list[DriverRecord] = []
        failed: list[str] = []
        for tab in TodoTab:
            try:
                page = session.open_todo(tab)
                rows = extract_todo(page, tab, reference=context.now)
            except Exception as exc:
                failed.append(f"{tab.value} tab: {exc}")
                continue
            records.extend(
                record_for_row(
                    row,
                    tab=tab,
                    student=context.student,
                    scraped_at=context.now,
                )
                for row in rows
            )
        if failed and len(failed) == len(TodoTab):
            return self._result(
                context,
                outcome=RunOutcome.FAILURE,
                message=(
                    "all classroom tabs failed: " + "; ".join(failed)
                ),
            )
        if failed:
            return self._result(
                context,
                outcome=RunOutcome.PARTIAL,
                found=len(records),
                message=(
                    "classroom tabs failed: "
                    + "; ".join(failed)
                    + f"; {len(records)} record(s) from the rest"
                ),
                records=tuple(records),
            )
        return self._result(
            context,
            outcome=RunOutcome.SUCCESS,
            found=len(records),
            records=tuple(records),
        )

    def _failure(self, context: ScrapeContext, message: str) -> ScrapeResult:
        return self._result(
            context, outcome=RunOutcome.FAILURE, message=message
        )

    def _result(
        self,
        context: ScrapeContext,
        *,
        outcome: RunOutcome,
        found: int = 0,
        message: str | None = None,
        records: tuple[DriverRecord, ...] = (),
    ) -> ScrapeResult:
        run = SourceRun(
            source_kind=SourceKind.CLASSROOM_WEB,
            started_at=context.now,
            finished_at=self._clock(),
            outcome=outcome,
            found=found,
            message=message,
        )
        return ScrapeResult(run=run, drivers=list(records))
