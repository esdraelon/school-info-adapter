"""ClassroomSession: one Playwright persistent profile, probed before use.

Pattern: session health probe (DESIGN §3) over a PageAdapter port.
The profile directory is a bearer credential for the student's
account (PLAN §4): it is BORN on hal9k under Xvfb in a supervised
step and this code only ever consumes an existing directory — it
never creates an account session from nothing. ``--password-store=
basic`` is a launch arg because headless Linux has no keyring
(PLAN §4: create and run in the identical environment).

Every Playwright call is quarantined in the ``_Playwright*`` adapter
classes at the bottom (marked pragma: no cover — they are exercised
by tests_system with real Chromium per C7, and hold no logic worth
unit-testing: the probe's decision lives in the pure ``classify_page``
and the session's flow is unit-tested against fake adapters).
Playwright imports are local to the adapter constructor so importing
this package never requires the browser binaries.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from school.driver_classroom_web.pages import (
    PageAdapter,
    Row,
    RowLink,
    SignInPage,
    TodoPage,
)
from school.driver_classroom_web.tabs import (
    DEFAULT_BASE_URL,
    TodoTab,
    tab_url,
)


class SessionState(StrEnum):
    """What the probe concluded about the persisted session."""

    AUTHENTICATED = "authenticated"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


@dataclass(frozen=True, kw_only=True)
class ProfileConfig:
    """How to open the persistent profile (frozen config dataclass).

    ``base_url`` exists so system tests can point the real session at
    a local stub origin (C7); production takes the default.
    """

    profile_dir: Path
    headless: bool = True
    base_url: str = DEFAULT_BASE_URL
    channel: str | None = None
    executable_path: Path | None = None
    launch_args: tuple[str, ...] = (
        "--password-store=basic",
        "--no-first-run",
    )


def classify_page(
    *,
    url: str,
    signin_form_present: bool,
    todo_landmark_present: bool,
) -> SessionState:
    """Probe verdict from three observed facts (pure, table-tested).

    A sign-in surface wins over everything: a page that landed on
    accounts.google.com or shows the form is EXPIRED even if stale
    Classroom chrome is still in the DOM.
    """
    if "accounts.google.com" in url or signin_form_present:
        return SessionState.EXPIRED
    if todo_landmark_present:
        return SessionState.AUTHENTICATED
    return SessionState.UNKNOWN


class ClassroomSession:
    """Owns one persistent-context browser page over a profile dir.

    Constructed lazily: the browser starts on first use, so building
    the driver (composition) never launches Chromium.
    """

    def __init__(
        self,
        config: ProfileConfig,
        *,
        adapter_factory: Callable[[], PageAdapter] | None = None,
    ) -> None:
        self._config = config
        self._adapter_factory = adapter_factory
        self._adapter: PageAdapter | None = None
        self._last_probe_detail = "session not probed yet"

    @property
    def last_probe_detail(self) -> str:
        """What the last probe saw, for failure messages."""
        return self._last_probe_detail

    def probe(self) -> SessionState:
        """Navigate to the Assigned tab and classify what loads."""
        adapter = self._ensure_adapter()
        adapter.goto(tab_url(self._config.base_url, TodoTab.ASSIGNED))
        signin_form = adapter.has_signin_form()
        landmark = adapter.has_todo_landmark()
        state = classify_page(
            url=adapter.url,
            signin_form_present=signin_form,
            todo_landmark_present=landmark,
        )
        self._last_probe_detail = (
            f"url={adapter.url} signin_form={signin_form} "
            f"todo_landmark={landmark} -> {state.value}"
        )
        return state

    def open_todo(self, tab: TodoTab) -> TodoPage:
        """Navigate to ``tab`` and return it via the TodoPage protocol."""
        adapter = self._ensure_adapter()
        adapter.goto(tab_url(self._config.base_url, tab))
        return adapter.todo_page()

    def sign_in_page(self) -> SignInPage:
        """The current page via the SignInPage protocol (re-auth)."""
        return self._ensure_adapter().sign_in_page()

    def close(self) -> None:
        """Close the browser if it was started; safe to call twice."""
        if self._adapter is not None:
            self._adapter.close()
            self._adapter = None

    def _ensure_adapter(self) -> PageAdapter:
        if self._adapter is None:
            factory = self._adapter_factory or self._playwright_adapter
            self._adapter = factory()
        return self._adapter

    def _playwright_adapter(self) -> PageAdapter:
        return _PlaywrightPageAdapter(self._config)


# ---------------------------------------------------------------------
# Playwright adapters: browser-bound, no business logic. Exercised by
# tests_system/test_classroom_web.py with real Chromium (C7).
# ---------------------------------------------------------------------


class _PlaywrightTodoPage:  # pragma: no cover - browser-bound adapter
    """TodoPage over a real Playwright page.

    Groups are resolved in DOCUMENT ORDER (h2 -> its links until the
    next h2) rather than by DOM ancestry: Classroom's wrapper markup
    is obfuscated and unstable, while heading order is structural
    (docs/FIXTURES.md). Row names are whitespace-collapsed inner text
    — the same shape as the accessibility-tree names in the fixture
    map.
    """

    def __init__(self, page: Any) -> None:
        self._page = page

    def course_names(self) -> list[str]:
        combo = self._page.get_by_role("combobox", name="Class filter")
        if combo.count() == 0:
            return []
        options = combo.locator("option").all_inner_texts()
        return [
            text.strip()
            for text in options
            if text.strip() and text.strip() != "All classes"
        ]

    def group_headings(self) -> list[str]:
        return [
            text
            for tag, _el, text in self._walk()
            if tag == "h2" and text
        ]

    def expand_group(self, heading: str) -> None:
        in_group = False
        for tag, element, text in self._walk():
            if tag == "h2":
                in_group = text == heading
                continue
            if not in_group or tag != "button":
                continue
            if text.startswith(heading):
                if element.get_attribute("aria-expanded") == "false":
                    element.click()
            elif text == "View all" and element.is_visible():
                element.click()

    def rows_in_group(self, heading: str) -> list[RowLink]:
        rows: list[RowLink] = []
        in_group = False
        for tag, element, text in self._walk():
            if tag == "h2":
                in_group = text == heading
                continue
            if in_group and tag == "a":
                rows.append(
                    Row(name=text, href=element.get_attribute("href"))
                )
        return rows

    def _walk(self) -> list[tuple[str, Any, str]]:
        """(tag, element, collapsed text) for h2/button/a under main,
        in document order."""
        main = self._page.locator("main")
        root = main if main.count() > 0 else self._page.locator("body")
        handles = root.locator("h2, button, a")
        walked: list[tuple[str, Any, str]] = []
        for index in range(handles.count()):
            element = handles.nth(index)
            tag = str(element.evaluate("el => el.tagName.toLowerCase()"))
            text = " ".join(element.inner_text().split())
            walked.append((tag, element, text))
        return walked


class _PlaywrightSignInPage:  # pragma: no cover - browser-bound adapter
    """SignInPage over a real Playwright page (standard Google form)."""

    def __init__(self, page: Any) -> None:
        self._page = page

    def has_email_field(self) -> bool:
        field = self._page.locator('input[type="email"]')
        return bool(field.count() > 0 and field.first.is_visible())

    def fill_email(self, email: str) -> None:
        self._page.locator('input[type="email"]').first.fill(email)

    def has_password_field(self) -> bool:
        field = self._page.locator('input[type="password"]')
        return bool(field.count() > 0 and field.first.is_visible())

    def fill_password(self, password: str) -> None:
        self._page.locator('input[type="password"]').first.fill(password)

    def advance(self) -> None:
        button = self._page.get_by_role("button", name="Next")
        if button.count() == 0:
            button = self._page.locator("button").filter(has_text="Next")
        button.first.click()

    def visible_text(self) -> str:
        try:
            return str(self._page.locator("body").inner_text())
        except Exception:  # page mid-navigation: no text to read
            return ""

    def has_todo_structure(self) -> bool:
        landmark = self._page.get_by_role(
            "navigation", name="Page navigation"
        )
        return bool(landmark.count() > 0)


class _PlaywrightPageAdapter:  # pragma: no cover - browser-bound adapter
    """PageAdapter over a Playwright persistent context."""

    def __init__(self, config: ProfileConfig) -> None:
        from playwright.sync_api import sync_playwright

        self._handle = sync_playwright().start()
        kwargs: dict[str, Any] = {
            "headless": config.headless,
            "args": list(config.launch_args),
        }
        if config.channel is not None:
            kwargs["channel"] = config.channel
        if config.executable_path is not None:
            kwargs["executable_path"] = str(config.executable_path)
        self._context = self._handle.chromium.launch_persistent_context(
            str(config.profile_dir), **kwargs
        )
        pages = self._context.pages
        self._page = pages[0] if pages else self._context.new_page()

    @property
    def url(self) -> str:
        return str(self._page.url)

    def goto(self, url: str) -> None:
        self._page.goto(url)

    def has_signin_form(self) -> bool:
        return _PlaywrightSignInPage(self._page).has_email_field()

    def has_todo_landmark(self) -> bool:
        return _PlaywrightSignInPage(self._page).has_todo_structure()

    def todo_page(self) -> TodoPage:
        return _PlaywrightTodoPage(self._page)

    def sign_in_page(self) -> SignInPage:
        return _PlaywrightSignInPage(self._page)

    def close(self) -> None:
        self._context.close()
        self._handle.stop()
