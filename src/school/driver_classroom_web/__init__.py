"""Google Classroom web driver: Playwright persistent profile, To-do page and Classwork scrapers.

Patterns: Adapter; Strategy (page extractors); session health probe with
re-auth escalation (extraction spec: docs/FIXTURES.md).
"""

from school.driver_classroom_web.driver import (
    RELOGIN_MESSAGE,
    ClassroomStudentConfig,
    ClassroomWebDriver,
)
from school.driver_classroom_web.extractors import (
    DueParse,
    RawRow,
    extract_todo,
    parse_done_tail,
    parse_due_text,
    parse_row_name,
    record_for_row,
)
from school.driver_classroom_web.reauth import ReauthHelper, ReauthOutcome
from school.driver_classroom_web.session import (
    ClassroomSession,
    ProfileConfig,
    SessionState,
)
from school.driver_classroom_web.tabs import TodoTab

__all__ = [
    "RELOGIN_MESSAGE",
    "ClassroomSession",
    "ClassroomStudentConfig",
    "ClassroomWebDriver",
    "DueParse",
    "ProfileConfig",
    "RawRow",
    "ReauthHelper",
    "ReauthOutcome",
    "SessionState",
    "TodoTab",
    "extract_todo",
    "parse_done_tail",
    "parse_due_text",
    "parse_row_name",
    "record_for_row",
]
