"""HAC cache field mapping: raw cache JSON -> domain records.

Pattern: pure mapping functions (the field-mapping layer of the Adapter).
This module is the ONLY place in the driver that touches raw cache dicts:
if the real cache's shape drifts, this file changes and nothing else
does. Everything here is a pure function — no I/O, no wall clock — so
the whole layer is table-testable; the reference date for status
derivation is injected by the caller (the driver derives it from
ScrapeContext.now read in the configured school timezone — HAC due
dates are school-local, M7).

Real cache schema (verified 2026-10-22 against
``/var/www/haliffer/grades-cache/cache/sam_classwork_cache.json`` on
hal9k — one file per student, written by the grades MCP scraper)::

    {
      "student_id": null,
      "courses": [
        {"name": "1801 - 1    Language Arts 8", "average": 91.2,
         "assignments": [
           {"name": "...", "category": "Summative (Product)",
            "date_assigned": "09/29/2026", "date_due": "10/02/2026",
            "max_points": 100.0, "score": 80.0, "notes": ""}
         ]}
      ]
    }

- ``student_id`` in the file is null and stays ignored: the adapter's
  configured student (and the per-student cache file) is the authority.
- Course names carry a ``"<code> - <period>    <display name>"`` prefix;
  the period digit canonicalizes to "P<digit>". A name without the
  prefix pattern is kept whole (whitespace-collapsed), period None.
- Items carry no course field and no id: the course comes from the
  enclosing course node, and ``source_ref`` stays None (the merge
  layer's dedup key carries identity).
- ``max_points``, ``average``, ``date_assigned``, and ``notes`` are not
  modeled; missing optional item keys degrade to None, never a crash.

Status derivation (derived-from-HAC-convention): the real cache has NO
missing flag and no turned-in signal anywhere. The only "missing"
signal it carries is an unscored item whose due date has passed, so:
a present ``score`` means GRADED (a 0.0 score included — an earned
zero and a missing-zero are indistinguishable in the cache, and we do
not invent a flag); ``score`` null with due date before the reference
date means MISSING; anything else is ASSIGNED. DONE is never produced
here. This rule deliberately understates: HAC lags reality, and the
merge layer outranks HAC with Classroom data when both know an item.

Kind mapping: ``category`` is teacher free-text, so kinds resolve by a
substring heuristic table (first match wins): quiz -> QUIZ;
test/assessment/summative -> TEST; project -> PROJECT; practice ->
PRACTICE; anything else (or absent) -> ASSIGNMENT.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any

from school.domain import (
    CourseRef,
    DriverKind,
    DriverRecord,
    Found,
    ItemStatus,
    Lookup,
    Missing,
    SourceKind,
    StudentRef,
)

# The real course-name prefix: "1801 - 1    Language Arts 8",
# "4801ADV - 4    Social Studies 8 Advanced". The code is a single
# token, the period a single digit; whitespace runs separate the parts.
_COURSE_PREFIX = re.compile(
    r"^(?P<code>\S+) - (?P<period>\d)\s+(?P<name>.+)$",
)

# Kind heuristic table over the free-text category (see module
# docstring): first substring match wins, so order is significant —
# "Tests-Projects" is a TEST, "Daily Work-Quizzes" a QUIZ.
_KIND_RULES: tuple[tuple[str, DriverKind], ...] = (
    ("quiz", DriverKind.QUIZ),
    ("test", DriverKind.TEST),
    ("assessment", DriverKind.TEST),
    ("summative", DriverKind.TEST),
    ("project", DriverKind.PROJECT),
    ("practice", DriverKind.PRACTICE),
)


def _as_text(value: object | None) -> str | None:
    """A scalar as stripped text; None for blanks, bools, and containers."""
    if isinstance(value, str):
        text = value.strip()
        return text if text else None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _collapse(text: str) -> str:
    """Internal whitespace runs collapsed to single spaces."""
    return " ".join(text.split())


def parse_course_name(raw_name: str) -> CourseRef:
    """A cache course string -> CourseRef (see the schema notes above).

    "1801 - 1    Language Arts 8" -> CourseRef(name="Language Arts 8",
    period="P1"). A name with no code prefix keeps the whole
    (whitespace-collapsed) name with period=None — never a crash.
    """
    match = _COURSE_PREFIX.match(raw_name.strip())
    if match is not None:
        return CourseRef(
            name=_collapse(match.group("name")),
            period=f"P{match.group('period')}",
        )
    return CourseRef(name=_collapse(raw_name))


def parse_due_at(value: object | None) -> datetime | None:
    """A raw due value -> aware datetime; None when absent/unparseable.

    The real cache writes "MM/DD/YYYY"; ISO dates/datetimes are also
    tolerated. Date-only values land at start of day; naive values are
    read as UTC.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    elif isinstance(value, str) and value.strip():
        from_text = _parse_due_text(value.strip())
        if from_text is None:
            return None
        parsed = from_text
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_due_text(text: str) -> datetime | None:
    try:
        return datetime.strptime(text, "%m/%d/%Y")
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    try:
        return datetime.combine(date.fromisoformat(text), time.min)
    except ValueError:
        return None


def score_text(raw: Mapping[str, Any]) -> str | None:
    """The item's score in the source's own rendering; None when unscored.

    The real cache's ``score`` is a number or null. Numbers render
    without a trailing ".0" (80.0 -> "80", 87.77 stays "87.77");
    strings pass through stripped; booleans are not scores.
    """
    value: object = raw.get("score")
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        return text if text else None
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, int):
        return str(value)
    return None


def map_status(
    raw: Mapping[str, Any],
    *,
    reference_date: date,
) -> ItemStatus:
    """Status from score + due date only (see the module docstring).

    GRADED when a score is present; otherwise MISSING when the due
    date is strictly before ``reference_date``; otherwise ASSIGNED.
    An absent or unparseable due date can never make an item MISSING.
    """
    if score_text(raw) is not None:
        return ItemStatus.GRADED
    due = parse_due_at(raw.get("date_due"))
    if due is not None and due.date() < reference_date:
        return ItemStatus.MISSING
    return ItemStatus.ASSIGNED


def map_kind(category: str | None) -> DriverKind:
    """Kind from the free-text category via the heuristic table;
    absent/unmatched categories are ASSIGNMENT (HAC classwork is
    assignment-shaped by default)."""
    if category:
        text = category.casefold()
        for needle, kind in _KIND_RULES:
            if needle in text:
                return kind
    return DriverKind.ASSIGNMENT


def map_item(
    raw: object,
    *,
    course_name: str,
    student: StudentRef,
    scraped_at: datetime,
    reference_date: date,
) -> Lookup[DriverRecord]:
    """One raw cache item -> DriverRecord, or Missing with the reason.

    The course comes from the enclosing course node (items carry no
    course field). Un-mappable means: not an object, or no title.
    Every other field is optional (absent -> None, never a crash), and
    ``source_ref`` is always None — cache items have no id.
    """
    if not isinstance(raw, Mapping):
        return Missing(reason=f"item is not an object ({type(raw).__name__})")
    title = _as_text(raw.get("name"))
    if title is None:
        return Missing(reason=f"item in {course_name!r} has no title")
    return Found(
        value=DriverRecord(
            student=student,
            course=parse_course_name(course_name),
            title=title,
            kind=map_kind(_as_text(raw.get("category"))),
            status=map_status(raw, reference_date=reference_date),
            source_kind=SourceKind.HAC,
            scraped_at=scraped_at,
            due_at=parse_due_at(raw.get("date_due")),
            score_text=score_text(raw),
            source_ref=None,
        )
    )


@dataclass(frozen=True, kw_only=True)
class CourseMapping:
    """One course node's mapping outcome: its records and skip reasons."""

    records: tuple[DriverRecord, ...]
    skipped: tuple[str, ...]


def map_course(
    raw: object,
    *,
    student: StudentRef,
    scraped_at: datetime,
    reference_date: date,
) -> CourseMapping:
    """One raw course node -> its records plus per-item skip reasons.

    Structural problems (not an object, no usable name, no
    ``assignments`` list) degrade to skip reasons, never exceptions —
    the adapter counts records + skipped as the node's item total.
    """
    if not isinstance(raw, Mapping):
        return CourseMapping(
            records=(),
            skipped=(f"course entry is not an object ({type(raw).__name__})",),
        )
    name = _as_text(raw.get("name"))
    assignments = raw.get("assignments")
    if not isinstance(assignments, list):
        where = f"course {name!r}" if name is not None else "an unnamed course"
        return CourseMapping(
            records=(),
            skipped=(f"{where} has no assignments list",),
        )
    if name is None:
        return CourseMapping(
            records=(),
            skipped=tuple(
                "item in a course with no name" for _ in assignments
            ),
        )
    records: list[DriverRecord] = []
    skipped: list[str] = []
    for item in assignments:
        match map_item(
            item,
            course_name=name,
            student=student,
            scraped_at=scraped_at,
            reference_date=reference_date,
        ):
            case Found(value=record):
                records.append(record)
            case Missing(reason=reason):
                skipped.append(reason)
    return CourseMapping(records=tuple(records), skipped=tuple(skipped))


def extract_courses(payload: object) -> Lookup[list[object]]:
    """The raw course nodes in a parsed cache payload.

    The real schema's only top-level shape is an object with a
    ``courses`` list; anything else is Missing with the reason (the
    payload's ``student_id`` is null in the real cache and stays
    ignored — the configured student is the authority). Raw course
    entries pass through untouched so map_course can count structural
    problems as skipped rather than losing them silently here.
    """
    if not isinstance(payload, Mapping):
        return Missing(
            reason=f"cache payload is a {type(payload).__name__}, not an object"
        )
    courses = payload.get("courses")
    if not isinstance(courses, list):
        return Missing(reason="cache payload has no 'courses' list")
    return Found(value=list(courses))
