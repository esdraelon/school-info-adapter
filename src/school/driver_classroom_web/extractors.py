"""To-do extractors: pure functions over the TodoPage protocol.

Pattern: Strategy (one extractor shape per tab family, over mapped
pure functions) implementing docs/FIXTURES.md, the captured
accessibility-tree map. Two separations keep this testable without a
browser:

- ``extract_todo`` talks only to the TodoPage protocol (expand every
  group, then read its rows);
- name/date parsing (``parse_row_name``, ``parse_due_text``,
  ``parse_done_tail``) is pure text-in/value-out, fixture-tabled
  against the exact strings the capture recorded.

Course splitting: a row's accessible name is title + course + tail
with NO delimiter, so the course is recovered by matching the
LONGEST known course name contained in the string; the course list
comes from the page's own Class filter (falling back to no match ->
the whole pre-tail text is the title, course ""). Splitting the tail
first (it has a tight grammar) keeps dates/grades out of titles.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from school.domain import (
    CourseRef,
    DriverKind,
    DriverRecord,
    ItemStatus,
    SourceKind,
    StudentRef,
)
from school.driver_classroom_web.pages import TodoPage
from school.driver_classroom_web.tabs import TodoTab
from school.driver_web.dates import WEEKDAYS, infer_year

_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

_RELATIVE_DAY = {"Today": 0, "Tomorrow": 1, "Yesterday": -1}

_TIME_RE = r"(\d{1,2}):(\d{2})\s+(AM|PM)"
_DUE_TAIL_RES = (
    re.compile(
        rf"^(?P<day>Today|Tomorrow|Yesterday),?\s+{_TIME_RE}$"
    ),
    re.compile(rf"^(?P<weekday>[A-Za-z]+),?\s+{_TIME_RE}$"),
    re.compile(
        r"^(?P<weekday>[A-Za-z]+),\s+(?P<month>[A-Za-z]{3})\s+"
        r"(?P<daynum>\d{1,2})$"
    ),
)
_POSTED_TAIL_RE = re.compile(
    r"^Posted\s+(?P<weekday>[A-Za-z]+),\s+(?P<month>[A-Za-z]{3})\s+"
    r"(?P<daynum>\d{1,2})$"
)
_GRADE_TAIL_RE = re.compile(
    r"^(?P<grade>[\d,]+(?:\.\d+)?\s+points out of possible\s+"
    r"[\d,]+(?:\.\d+)?)\s*(?P<status>.*)$"
)

_KIND_RULES: tuple[tuple[re.Pattern[str], DriverKind], ...] = (
    (re.compile(r"\bquiz\b", re.IGNORECASE), DriverKind.QUIZ),
    (re.compile(r"\btest\b|\bassessment\b", re.IGNORECASE), DriverKind.TEST),
    (re.compile(r"\bproject\b", re.IGNORECASE), DriverKind.PROJECT),
    (
        re.compile(r"\bworkbook\b|\bpractice\b", re.IGNORECASE),
        DriverKind.PRACTICE,
    ),
)


@dataclass(frozen=True, kw_only=True)
class RawRow:
    """One extracted row, pre-mapping (extractor output)."""

    group: str
    title: str
    course: str
    tail: str
    href: str | None = None


@dataclass(frozen=True, kw_only=True)
class DueParse:
    """A parsed right-column tail.

    ``due_at`` is None exactly for "Posted ..." tails (no due date);
    ``posted_on`` then carries the posted date. Time-of-day for a
    date-only tail is end-of-day (23:59), matching how Classroom
    displays an unset due time; relative/weekday tails carry the
    exact time the page showed. All values take the reference's
    tzinfo, so due_at and scraped_at share a clock.
    """

    due_at: datetime | None
    posted_on: date | None = None


def _clock_time(match: re.Match[str], first_group: int) -> time:
    hour = int(match.group(first_group)) % 12
    if match.group(first_group + 2) == "PM":
        hour += 12
    return time(hour, int(match.group(first_group + 1)))


def _weekday_on_or_near(weekday: str, reference: date) -> date | None:
    """The date with ``weekday`` closest to ``reference`` (+-3 days
    covers every case; a tie resolves to the earlier date)."""
    if weekday not in WEEKDAYS:
        return None
    target = WEEKDAYS.index(weekday)
    candidates = [
        reference + timedelta(days=offset)
        for offset in range(-6, 7)
        if (reference + timedelta(days=offset)).weekday() == target
    ]
    return min(
        candidates, key=lambda c: (abs((c - reference).days), c.toordinal())
    )


def _month_day(month: str, daynum: str, reference: date) -> date | None:
    month_number = _MONTHS.get(month)
    if month_number is None:
        return None
    return infer_year(month_number, int(daynum), reference=reference)


def parse_due_text(tail: str, *, reference: datetime) -> DueParse:
    """Parse a right-column tail per the FIXTURES.md grammar.

    Unrecognized tails degrade to a no-due parse rather than raising:
    one oddly formatted row must not sink a whole tab.
    """
    ref_date = reference.date()
    posted = _POSTED_TAIL_RE.match(tail)
    if posted:
        posted_on = _month_day(
            posted.group("month"), posted.group("daynum"), ref_date
        )
        return DueParse(due_at=None, posted_on=posted_on)
    relative, weekday_time, date_only = _DUE_TAIL_RES
    match = relative.match(tail)
    if match:
        day = ref_date + timedelta(
            days=_RELATIVE_DAY[match.group("day")]
        )
        return DueParse(
            due_at=datetime.combine(
                day, _clock_time(match, 2), tzinfo=reference.tzinfo
            )
        )
    match = weekday_time.match(tail)
    if match:
        weekday_day = _weekday_on_or_near(match.group("weekday"), ref_date)
        if weekday_day is None:
            return DueParse(due_at=None)
        return DueParse(
            due_at=datetime.combine(
                weekday_day,
                _clock_time(match, 2),
                tzinfo=reference.tzinfo,
            )
        )
    match = date_only.match(tail)
    if match:
        month_day = _month_day(
            match.group("month"), match.group("daynum"), ref_date
        )
        if month_day is None:
            return DueParse(due_at=None)
        return DueParse(
            due_at=datetime.combine(
                month_day, time(23, 59), tzinfo=reference.tzinfo
            )
        )
    return DueParse(due_at=None)


def parse_done_tail(tail: str) -> str | None:
    """The grade text of a Done-tab tail ("87.77 points out of
    possible 100"), or None when the tail carries no grade."""
    match = _GRADE_TAIL_RE.match(tail)
    return match.group("grade") if match else None


def _split_tail(name: str) -> tuple[str, str]:
    """Split ``name`` into (head, tail) at the tail grammar.

    Scans word-aligned suffixes longest-first: the tail is the
    longest trailing span matching a due form, a Posted form, or a
    grade form. No match -> the whole string is the head, tail "".
    """
    words = name.split(" ")
    for start in range(1, len(words)):
        candidate = " ".join(words[start:])
        if (
            any(pattern.match(candidate) for pattern in _DUE_TAIL_RES)
            or _POSTED_TAIL_RE.match(candidate)
            or _GRADE_TAIL_RE.match(candidate)
        ):
            return " ".join(words[:start]), candidate
    return name, ""


def parse_row_name(
    name: str, course_names: list[str]
) -> tuple[str, str, str]:
    """Split a row's accessible name into (title, course, tail).

    The course is the LONGEST known course name contained in what
    remains after the tail is split off; title is the text before it,
    tail the text after it. No known course -> course "" and the
    whole head is the title.
    """
    head, tail = _split_tail(name)
    matches = [course for course in course_names if course and course in head]
    if not matches:
        return head, "", tail
    course = max(matches, key=len)
    before, _, after = head.partition(course)
    remainder_tail = " ".join(
        part for part in (after.strip(), tail) if part
    )
    return before.strip(), course, remainder_tail


def extract_todo(
    page: TodoPage, tab: TodoTab, *, reference: datetime
) -> list[RawRow]:
    """Every row on ``tab``: expand each group, then read its rows.

    Extraction is uniform across tabs today (tab is carried for the
    mapping step and any future per-tab page quirk); ``reference`` is
    likewise the caller's scrape clock, accepted so the driver passes
    one time source through the whole scrape — parsing, which does
    need it, is the separate step below.
    """
    _ = (tab, reference)  # uniform today; see docstring
    courses = page.course_names()
    rows: list[RawRow] = []
    for heading in page.group_headings():
        page.expand_group(heading)
        for link in page.rows_in_group(heading):
            title, course, tail = parse_row_name(link.name, courses)
            rows.append(
                RawRow(
                    group=heading,
                    title=title,
                    course=course,
                    tail=tail,
                    href=link.href,
                )
            )
    return rows


def kind_for_title(title: str) -> DriverKind:
    """Kind from the title via the ordered rule table (mirrors
    driver_web's kind_for_text; kept local so drivers stay decoupled).
    """
    for pattern, kind in _KIND_RULES:
        if pattern.search(title):
            return kind
    return DriverKind.ASSIGNMENT


def record_for_row(
    row: RawRow,
    *,
    tab: TodoTab,
    student: StudentRef,
    scraped_at: datetime,
) -> DriverRecord:
    """Map one raw row to a DriverRecord, per-tab (FIXTURES.md):

    ASSIGNED -> ASSIGNED (due from the tail; "Posted ..." -> no due);
    MISSING -> MISSING with its (past) due; DONE -> GRADED when the
    tail carries a grade (score_text = the grade text; the trailing
    status segment never changes GRADED), else DONE, never dated.
    """
    if tab is TodoTab.DONE:
        score = parse_done_tail(row.tail)
        status = ItemStatus.GRADED if score is not None else ItemStatus.DONE
        return DriverRecord(
            student=student,
            course=CourseRef(name=row.course),
            title=row.title,
            kind=kind_for_title(row.title),
            status=status,
            source_kind=SourceKind.CLASSROOM_WEB,
            scraped_at=scraped_at,
            score_text=score,
            source_ref=row.href,
        )
    due = parse_due_text(row.tail, reference=scraped_at)
    status = (
        ItemStatus.MISSING if tab is TodoTab.MISSING else ItemStatus.ASSIGNED
    )
    return DriverRecord(
        student=student,
        course=CourseRef(name=row.course),
        title=row.title,
        kind=kind_for_title(row.title),
        status=status,
        source_kind=SourceKind.CLASSROOM_WEB,
        scraped_at=scraped_at,
        due_at=due.due_at,
        source_ref=row.href,
    )
