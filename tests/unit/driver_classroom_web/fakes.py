"""Scripted fakes implementing the driver_classroom_web protocols.

Two families:

- Page fakes (``FakeTodoPage``/``FakeSignInPage``/``FakePageAdapter``)
  implement the pages.py protocols with in-memory state. FakeSignInPage
  records every MUTATING interaction (fills and advances) in
  ``mutations`` so tests can prove the re-auth helper stops dead when
  a challenge appears; queries are not recorded.
- ``FakeSession`` implements the session shape the driver consumes
  (probe/open_todo/sign_in_page/close), with a scripted probe state
  the paired FakeSignInPage flips to AUTHENTICATED on a good sign-in.
"""

from dataclasses import dataclass, field

from school.driver_classroom_web.pages import Row
from school.driver_classroom_web.session import SessionState
from school.driver_classroom_web.tabs import TodoTab


@dataclass
class FakeGroup:
    """One To-do group: rows, rows hidden behind "View all", state."""

    rows: list[Row] = field(default_factory=list)
    hidden_rows: list[Row] = field(default_factory=list)
    expanded: bool = False


class FakeTodoPage:
    """TodoPage fake: collapsed groups expose no rows until expanded."""

    def __init__(
        self,
        *,
        courses: list[str] | None = None,
        groups: dict[str, FakeGroup] | None = None,
    ) -> None:
        self._courses = courses or []
        self._groups = groups or {}
        self.expanded: list[str] = []

    def course_names(self) -> list[str]:
        return list(self._courses)

    def group_headings(self) -> list[str]:
        return list(self._groups)

    def expand_group(self, heading: str) -> None:
        self.expanded.append(heading)
        group = self._groups[heading]
        if not group.expanded:
            group.expanded = True
            group.rows = [*group.rows, *group.hidden_rows]
            group.hidden_rows = []

    def rows_in_group(self, heading: str) -> list[Row]:
        group = self._groups[heading]
        return list(group.rows) if group.expanded else []


class FakeSignInPage:
    """SignInPage fake with a scripted outcome.

    ``after_password`` selects what the post-submit page looks like:
    "todo" (sign-in worked; also flips the paired probe state),
    "challenge" (a 2-Step Verification interstitial), "wrong"
    (wrong-password error text), or "blank" (unrecognizable page).
    ``challenge_after_email`` puts the challenge one step earlier.
    """

    def __init__(
        self,
        *,
        email_field: bool = True,
        password_field: bool = False,
        after_password: str = "todo",
        challenge_after_email: bool = False,
        on_signed_in: object = None,
    ) -> None:
        self._email_field = email_field
        self._password_field = password_field
        self._after_password = after_password
        self._challenge_after_email = challenge_after_email
        self._on_signed_in = on_signed_in
        # A page constructed already showing the password field starts
        # at the password stage: its first advance IS the submit.
        self._stage = (
            "password" if (password_field and not email_field) else "start"
        )
        self._text = "Sign in to Google Classroom"
        self.mutations: list[str] = []
        self.filled_email: str | None = None
        self.filled_password: str | None = None

    def has_email_field(self) -> bool:
        return self._email_field

    def fill_email(self, email: str) -> None:
        self.mutations.append("fill_email")
        self.filled_email = email

    def has_password_field(self) -> bool:
        return self._password_field

    def fill_password(self, password: str) -> None:
        self.mutations.append("fill_password")
        self.filled_password = password

    def advance(self) -> None:
        self.mutations.append("advance")
        if self._stage == "start":
            self._stage = "password"
            if self._challenge_after_email:
                self._text = "2-Step Verification: enter the code"
            else:
                self._password_field = True
                self._text = "Welcome: enter your password"
            return
        self._stage = "done"
        self._email_field = False
        self._password_field = False
        if self._after_password == "todo":
            self._text = "Google Classroom Classroom To-do"
            if callable(self._on_signed_in):
                self._on_signed_in()
        elif self._after_password == "challenge":
            self._text = "Verify it's you: complete 2-Step Verification"
        elif self._after_password == "wrong":
            self._text = "Wrong password. Try again."
        else:
            self._text = "Something unexpected"

    def visible_text(self) -> str:
        return self._text

    def has_todo_structure(self) -> bool:
        return self._stage == "done" and self._after_password == "todo"


class FakePageAdapter:
    """PageAdapter fake with scripted probe facts and canned pages."""

    def __init__(
        self,
        *,
        url: str = "https://classroom.google.com/a/not-turned-in/all",
        signin_form: bool = False,
        landmark: bool = True,
        todo_page: FakeTodoPage | None = None,
        sign_in_page: FakeSignInPage | None = None,
    ) -> None:
        self._url = url
        self._signin_form = signin_form
        self._landmark = landmark
        self._todo_page = todo_page or FakeTodoPage()
        self._sign_in_page = sign_in_page or FakeSignInPage()
        self.visited: list[str] = []
        self.closed = False

    @property
    def url(self) -> str:
        return self._url

    def goto(self, url: str) -> None:
        self.visited.append(url)
        self._url = url

    def has_signin_form(self) -> bool:
        return self._signin_form

    def has_todo_landmark(self) -> bool:
        return self._landmark

    def todo_page(self) -> FakeTodoPage:
        return self._todo_page

    def sign_in_page(self) -> FakeSignInPage:
        return self._sign_in_page

    def close(self) -> None:
        self.closed = True


class FakeSession:
    """The session shape ClassroomWebDriver consumes, fully scripted.

    ``probe_states`` is a queue: each probe() pops the next state
    (the last one repeats). A paired FakeSignInPage can be given
    ``on_signed_in`` pointing at :meth:`mark_authenticated`.
    """

    def __init__(
        self,
        *,
        probe_states: list[SessionState] | None = None,
        pages: dict[TodoTab, FakeTodoPage] | None = None,
        sign_in_page: FakeSignInPage | None = None,
        probe_error: Exception | None = None,
        open_errors: dict[TodoTab, Exception] | None = None,
        sign_in_error: Exception | None = None,
        close_error: Exception | None = None,
        fail_second_probe: bool = False,
    ) -> None:
        self._probe_states = probe_states or [SessionState.AUTHENTICATED]
        self._pages = pages or {}
        self._sign_in_page = sign_in_page or FakeSignInPage()
        self._probe_error = probe_error
        self._open_errors = open_errors or {}
        self._sign_in_error = sign_in_error
        self._close_error = close_error
        self._fail_second_probe = fail_second_probe
        self.last_probe_detail = "fake probe detail"
        self.probe_calls = 0
        self.opened_tabs: list[TodoTab] = []
        self.closed = False

    def mark_authenticated(self) -> None:
        self._probe_states = [SessionState.AUTHENTICATED]

    def script_sign_in(self, *, after_password: str) -> FakeSignInPage:
        """Install a sign-in page whose success flips this session's
        probe to AUTHENTICATED (the pairing the constructor can't
        express, since the page's callback needs the session)."""
        page = FakeSignInPage(
            after_password=after_password,
            on_signed_in=self.mark_authenticated,
        )
        self._sign_in_page = page
        return page

    def probe(self) -> SessionState:
        self.probe_calls += 1
        if self._probe_error is not None:
            raise self._probe_error
        if self._fail_second_probe and self.probe_calls == 2:
            raise RuntimeError("browser died on re-probe")
        if len(self._probe_states) > 1:
            return self._probe_states.pop(0)
        return self._probe_states[0]

    def open_todo(self, tab: TodoTab) -> FakeTodoPage:
        self.opened_tabs.append(tab)
        if tab in self._open_errors:
            raise self._open_errors[tab]
        if tab not in self._pages:
            raise AssertionError(f"no fake page scripted for {tab}")
        return self._pages[tab]

    def sign_in_page(self) -> FakeSignInPage:
        if self._sign_in_error is not None:
            raise self._sign_in_error
        return self._sign_in_page

    def close(self) -> None:
        self.closed = True
        if self._close_error is not None:
            raise self._close_error
