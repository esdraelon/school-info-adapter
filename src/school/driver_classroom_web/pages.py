"""Page protocols: the ONLY surface the session, extractors, and
re-auth helper are allowed to see of a browser page.

Pattern: minimal structural Protocols (ports) with the Playwright
implementation quarantined in ``session.py``. Everything above this
layer — extraction, re-auth, the driver itself — is written against
these protocols, so unit tests substitute scripted fakes and no test
outside tests_system ever needs a browser (C7).

The shapes mirror docs/FIXTURES.md: a To-do tab is a set of h2-headed
groups of row links; a row's accessible name carries title + course +
right-column text; the sign-in surface is the standard Google email ->
password form.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@runtime_checkable
class RowLink(Protocol):
    """One assignment row: a single link (docs/FIXTURES.md)."""

    @property
    def name(self) -> str:
        """The link's accessible name: title + course + right column."""
        ...

    @property
    def href(self) -> str | None:
        """The link target, when the page exposes one."""
        ...


@dataclass(frozen=True, kw_only=True)
class Row:
    """A concrete RowLink, produced by adapters and test fakes."""

    name: str
    href: str | None = None


@runtime_checkable
class TodoPage(Protocol):
    """One To-do tab, as the extractors consume it."""

    def course_names(self) -> list[str]:
        """Course names from the page's own Class filter (may be
        empty when the filter cannot be read)."""
        ...

    def group_headings(self) -> list[str]:
        """The h2 group headings, in document order."""
        ...

    def expand_group(self, heading: str) -> None:
        """Expand the group: its toggle button, then its "View all"
        button when the group has one (docs/FIXTURES.md)."""
        ...

    def rows_in_group(self, heading: str) -> list[RowLink]:
        """Every row link under ``heading`` (call after expand_group)."""
        ...


@runtime_checkable
class SignInPage(Protocol):
    """The Google sign-in form, as the re-auth helper consumes it."""

    def has_email_field(self) -> bool:
        """An email/identifier field is present and visible."""
        ...

    def fill_email(self, email: str) -> None:
        """Type the account email into the identifier field."""
        ...

    def has_password_field(self) -> bool:
        """A password field is present and visible."""
        ...

    def fill_password(self, password: str) -> None:
        """Type the account password into the password field."""
        ...

    def advance(self) -> None:
        """Click the form's Next button."""
        ...

    def visible_text(self) -> str:
        """The page's visible text (challenge/error detection)."""
        ...

    def has_todo_structure(self) -> bool:
        """The Classroom To-do structure (Page navigation landmark)
        is present — the sign-in succeeded."""
        ...


@runtime_checkable
class PageAdapter(Protocol):
    """One browser page plus navigation, abstracted for the session.

    The production implementation wraps a Playwright page; tests use
    scripted fakes. The session never sees anything richer than this.
    """

    @property
    def url(self) -> str:
        """The page's current URL."""
        ...

    def goto(self, url: str) -> None:
        """Navigate the page to ``url``."""
        ...

    def has_signin_form(self) -> bool:
        """A Google sign-in form is showing (session expired)."""
        ...

    def has_todo_landmark(self) -> bool:
        """The To-do 'Page navigation' landmark is showing."""
        ...

    def todo_page(self) -> TodoPage:
        """This page viewed through the TodoPage protocol."""
        ...

    def sign_in_page(self) -> SignInPage:
        """This page viewed through the SignInPage protocol."""
        ...

    def close(self) -> None:
        """Release the page and its browser context."""
        ...
