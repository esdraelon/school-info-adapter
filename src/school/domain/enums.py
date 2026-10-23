"""Domain enumerations.

Pattern: closed vocabularies as enums (StrEnum for wire/storage-friendly
values; IntEnum where the numeric order itself carries meaning).

``ItemStatus`` values are assigned in increasing order of *information
richness* — GRADED knows the most about an item, ASSIGNED the least — so
plain ``<``/``>`` comparisons on statuses implement the merge tiebreak
"prefer the richer status" (see school.merge).
"""

from enum import IntEnum, StrEnum


class SourceKind(StrEnum):
    """Which source family produced a record."""

    HAC = "hac"
    TEAM_SITE = "team_site"
    AGENDA = "agenda"
    CLASSROOM_WEB = "classroom_web"


class DriverKind(StrEnum):
    """What sort of due item a DriverRecord is (only as rich as sources know)."""

    ASSIGNMENT = "assignment"
    QUIZ = "quiz"
    TEST = "test"
    PROJECT = "project"
    PRACTICE = "practice"
    OTHER = "other"


class ItemStatus(IntEnum):
    """Lifecycle status of a due item, ordered by information richness.

    ASSIGNED (a source says it exists) < MISSING (a source says it is
    overdue) < DONE (turned in / completed) < GRADED (a score exists).
    The merge engine uses this ordering as its status tiebreak.
    """

    ASSIGNED = 1
    MISSING = 2
    DONE = 3
    GRADED = 4


class ResourceKind(StrEnum):
    """What sort of material a ResourceRecord points at."""

    NOTES = "notes"
    TUTORIAL = "tutorial"
    LINK = "link"
    DOCUMENT = "document"


class RunOutcome(StrEnum):
    """How a single scrape run ended."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILURE = "failure"
