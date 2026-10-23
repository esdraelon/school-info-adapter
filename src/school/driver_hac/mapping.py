"""HAC cache field mapping: raw cache JSON -> domain records.

Pattern: pure mapping functions (the field-mapping layer of the Adapter).
This module is the ONLY place in the driver that touches raw cache dicts:
if the real cache's field names differ from the assumptions below, this
file changes and nothing else does. Everything here is a pure function —
no I/O, no clock, no file access — so the whole layer is table-testable.

Cache schema assumptions (encoded here and in the test fixtures; to be
reconciled against the real cache before deploy):

- Top level is one of: an object keyed by student id; an object with a
  ``students`` object keyed by student id; or a bare list of items (each
  item may carry a ``student``/``student_id`` marker).
- A student node is a list of items, or an object whose items live under
  ``classwork`` (fallbacks: ``assignments``, ``items``).
- Item fields, first present wins: course <- course|class|course_name;
  title <- title|assignment|name; due <- due_date|due|due_at;
  score <- score|grade|score_text; kind <- kind|category|type;
  id -> source_ref.
- Due values are ISO dates/datetimes or ``MM/DD/YYYY``; naive values are
  read as UTC.
- Status mapping is deliberately conservative (HAC lags reality): a score
  means GRADED, an explicit missing flag/status means MISSING, everything
  else is ASSIGNED. The cache carries no turned-in signal, so DONE is
  never produced here.
"""

import re
from collections.abc import Mapping
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

_COURSE_KEYS = ("course", "class", "course_name")
_TITLE_KEYS = ("title", "assignment", "name")
_DUE_KEYS = ("due_date", "due", "due_at")
_SCORE_KEYS = ("score", "grade", "score_text")
_KIND_KEYS = ("kind", "category", "type")
_ITEM_LIST_KEYS = ("classwork", "assignments", "items")
_STUDENT_MARKER_KEYS = ("student", "student_id")

# A trailing period marker: "Math 8 P4", "Social Studies Period 4",
# "Wind Ensemble (P4)", "Math 8 - P4". The marker must start at a token
# boundary (whitespace, comma, dash, or open paren) so a course like
# "AP 4" is not misread.
_PERIOD_SUFFIX = re.compile(
    r"^(?P<name>.+?)(?:[\s,]+|[-–—]\s*|\(\s*)(?:period|p)\s*(?P<num>\d+)\s*\)?\s*$",
    re.IGNORECASE,
)


def _first_value(raw: Mapping[str, Any], keys: tuple[str, ...]) -> object | None:
    """The first non-None value among ``keys`` (schema-detection helper)."""
    for key in keys:
        value: object = raw.get(key)
        if value is not None:
            return value
    return None


def _as_text(value: object | None) -> str | None:
    """A scalar as stripped text; None for blanks, bools, and containers."""
    if isinstance(value, str):
        text = value.strip()
        return text if text else None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _first_text(raw: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    return _as_text(_first_value(raw, keys))


def parse_course_name(raw_name: str) -> CourseRef:
    """A cache course string -> CourseRef, splitting a trailing period.

    "Math 8 P4" -> CourseRef(name="Math 8", period="P4"); a name with no
    period marker keeps the full name and period=None. The period is
    canonicalized to "P<n>" so differently-styled sources agree.
    """
    name = raw_name.strip()
    match = _PERIOD_SUFFIX.match(name)
    if match is not None:
        base = match.group("name").strip()
        if base:
            return CourseRef(name=base, period=f"P{match.group('num')}")
    return CourseRef(name=name)


def parse_due_at(value: object | None) -> datetime | None:
    """A raw due value -> aware datetime; None when absent/unparseable.

    Tolerates ISO dates ("2026-10-23"), ISO datetimes, and "MM/DD/YYYY".
    Date-only values land at start of day; naive values are read as UTC.
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
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    try:
        return datetime.combine(date.fromisoformat(text), time.min)
    except ValueError:
        pass
    try:
        return datetime.strptime(text, "%m/%d/%Y")
    except ValueError:
        return None


def score_text(raw: Mapping[str, Any]) -> str | None:
    """The item's score in the source's own rendering; None when unscored.

    Numbers render without a trailing ".0" (98.0 -> "98", 87.77 stays
    "87.77"); strings pass through stripped; booleans are not scores.
    """
    value = _first_value(raw, _SCORE_KEYS)
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        return text if text else None
    if isinstance(value, (int, float)):
        return f"{value:g}" if isinstance(value, float) else str(value)
    return None


def _is_missing(raw: Mapping[str, Any]) -> bool:
    """True only on an explicit missing signal (flag or status string)."""
    flag = raw.get("missing")
    if flag is True:
        return True
    if isinstance(flag, str) and flag.strip().casefold() in {"true", "yes"}:
        return True
    status = raw.get("status")
    return isinstance(status, str) and status.strip().casefold() == "missing"


def map_status(raw: Mapping[str, Any]) -> ItemStatus:
    """Conservative status mapping (HAC lags; see module docstring).

    A score outranks a missing flag: a scored item is known-graded, the
    richest signal (and the merge engine's ordering agrees). DONE is
    never produced — the cache has no turned-in signal.
    """
    if score_text(raw) is not None:
        return ItemStatus.GRADED
    if _is_missing(raw):
        return ItemStatus.MISSING
    return ItemStatus.ASSIGNED


def map_kind(raw: Mapping[str, Any]) -> DriverKind:
    """The item kind: a known kind word wins; absent means ASSIGNMENT
    (HAC classwork is assignment-shaped); an unknown word means OTHER."""
    text = _first_text(raw, _KIND_KEYS)
    if text is None:
        return DriverKind.ASSIGNMENT
    try:
        return DriverKind(text.strip().casefold().replace(" ", "_"))
    except ValueError:
        return DriverKind.OTHER


def map_item(
    raw: object,
    *,
    student: StudentRef,
    scraped_at: datetime,
) -> Lookup[DriverRecord]:
    """One raw cache item -> DriverRecord, or Missing with the reason.

    Un-mappable means: not an object, no course name, or no title. Every
    other field is optional (absent -> None, never a crash).
    """
    if not isinstance(raw, Mapping):
        return Missing(reason=f"item is not an object ({type(raw).__name__})")
    course_text = _first_text(raw, _COURSE_KEYS)
    if course_text is None:
        return Missing(reason="item has no course name")
    title = _first_text(raw, _TITLE_KEYS)
    if title is None:
        return Missing(reason=f"item in {course_text!r} has no title")
    return Found(
        value=DriverRecord(
            student=student,
            course=parse_course_name(course_text),
            title=title,
            kind=map_kind(raw),
            status=map_status(raw),
            source_kind=SourceKind.HAC,
            scraped_at=scraped_at,
            due_at=parse_due_at(_first_value(raw, _DUE_KEYS)),
            score_text=score_text(raw),
            source_ref=_first_text(raw, ("id",)),
        )
    )


def _student_marker(item: Mapping[str, Any]) -> str | None:
    return _first_text(item, _STUDENT_MARKER_KEYS)


def _student_node(payload: Mapping[str, Any], student_id: str) -> object | None:
    """The student's node in a keyed cache payload; None when absent."""
    if student_id in payload:
        node: object = payload[student_id]
        return node
    students = payload.get("students")
    if isinstance(students, Mapping) and student_id in students:
        found: object = students[student_id]
        return found
    return None


def _items_from_node(node: object, student_id: str) -> Lookup[list[object]]:
    """A student node -> its raw item list (see the schema assumptions)."""
    if isinstance(node, list):
        return Found(value=list(node))
    if isinstance(node, Mapping):
        for key in _ITEM_LIST_KEYS:
            items = node.get(key)
            if isinstance(items, list):
                return Found(value=list(items))
        return Missing(
            reason=(
                f"cache node for student {student_id!r} has no item list "
                f"under any of {_ITEM_LIST_KEYS}"
            )
        )
    return Missing(
        reason=f"cache node for student {student_id!r} is a {type(node).__name__}"
    )


def extract_student_items(
    payload: object,
    student_id: str,
) -> Lookup[list[object]]:
    """The raw items for one student in a parsed cache payload.

    Missing means the student is not in the cache (or, for a node with
    no recognizable item list, why not). Raw entries pass through
    untouched — including non-object entries — so map_item can count
    them as skipped rather than losing them silently here.
    """
    if isinstance(payload, Mapping):
        node = _student_node(payload, student_id)
        if node is None:
            return Missing(reason=f"student {student_id!r} not in cache")
        return _items_from_node(node, student_id)
    if isinstance(payload, list):
        entries: list[object] = list(payload)
        marked = [
            entry
            for entry in entries
            if isinstance(entry, Mapping) and _student_marker(entry) is not None
        ]
        if not marked:
            return Found(value=entries)
        matched: list[object] = [
            entry
            for entry in marked
            if _student_marker(entry) == student_id
        ]
        if not matched:
            return Missing(reason=f"student {student_id!r} not in cache")
        return Found(value=matched)
    return Missing(
        reason=f"cache payload is a {type(payload).__name__}, not an object or list"
    )
