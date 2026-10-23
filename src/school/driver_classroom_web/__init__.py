"""Google Classroom web driver: Playwright persistent profile, To-do page and Classwork scrapers.

Patterns: Adapter; Strategy (page extractors); session health probe with
re-auth escalation (extraction spec: docs/FIXTURES.md).
"""

from school.driver_classroom_web.reauth import ReauthHelper, ReauthOutcome
from school.driver_classroom_web.session import (
    ClassroomSession,
    ProfileConfig,
    SessionState,
)
from school.driver_classroom_web.tabs import TodoTab

__all__ = [
    "ClassroomSession",
    "ProfileConfig",
    "ReauthHelper",
    "ReauthOutcome",
    "SessionState",
    "TodoTab",
]
