"""The three Classroom To-do tabs and their URLs (docs/FIXTURES.md).

Pattern: closed vocabulary (enum) + a lookup table — tab selection is
data, never branching (C2).
"""

from enum import StrEnum

#: Where the To-do pages live in production; the composition root can
#: point a session at a stub origin for system tests (C7).
DEFAULT_BASE_URL = "https://classroom.google.com"


class TodoTab(StrEnum):
    """One tab of the student To-do page."""

    ASSIGNED = "assigned"
    MISSING = "missing"
    DONE = "done"


#: URL path per tab, exactly as captured in docs/FIXTURES.md.
TAB_PATHS: dict[TodoTab, str] = {
    TodoTab.ASSIGNED: "/a/not-turned-in/all",
    TodoTab.MISSING: "/a/missing/all",
    TodoTab.DONE: "/a/turned-in/all",
}


def tab_url(base_url: str, tab: TodoTab) -> str:
    """The full URL of ``tab`` under ``base_url``."""
    return base_url.rstrip("/") + TAB_PATHS[tab]
