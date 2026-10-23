"""Date grammar for agenda text. Pattern: pure functions.

Deck text carries dates as bare ``M/D`` (no year) plus a small grammar
of due-date forms: ``FD 10/26`` (final deadline), ``due 10/21``,
``(FD 10/26)`` trailing a plain date, weekday names ("Due: Friday"),
and "Next week". Year inference is always relative to an injected
reference date (the scrape context's clock, never the wall clock):
the candidate year putting the date closest to the reference wins —
which also handles December/January week spans correctly.
"""

import re
from datetime import date, datetime, timedelta

_MD_RE = re.compile(r"(\d{1,2})/(\d{1,2})")
_FD_RE = re.compile(r"\bFD\b\s*:?\s*(\d{1,2})/(\d{1,2})", re.IGNORECASE)
_DUE_RE = re.compile(r"\bdue\b[^0-9]{0,16}?(\d{1,2})/(\d{1,2})", re.IGNORECASE)

WEEKDAYS = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
    "Saturday", "Sunday",
)


def infer_year(month: int, day: int, *, reference: date) -> date:
    """The date for (month, day) in the year closest to ``reference``."""
    candidates: list[date] = []
    for year in (reference.year - 1, reference.year, reference.year + 1):
        try:
            candidates.append(date(year, month, day))
        except ValueError:  # e.g. Feb 29 in a non-leap year
            continue
    return min(candidates, key=lambda c: abs((c - reference).days))


def _md_to_date(match: re.Match[str], reference: date) -> date:
    return infer_year(
        int(match.group(1)), int(match.group(2)), reference=reference
    )


def first_md(text: str, *, reference: date) -> date | None:
    """The first ``M/D`` date in ``text``, year-inferred, if any."""
    match = _MD_RE.search(text)
    return _md_to_date(match, reference) if match else None


def resolve_due(text: str, *, default: date, reference: date) -> date:
    """A cell's due date from its text.

    Precedence: an ``FD`` (final-deadline) date overrides everything;
    then an explicit ``due M/D``; then any other ``M/D`` in the text;
    then ``default`` (the cell's own day).
    """
    fd = _FD_RE.search(text)
    if fd:
        return _md_to_date(fd, reference)
    due = _DUE_RE.search(text)
    if due:
        return _md_to_date(due, reference)
    any_md = first_md(text, reference=reference)
    return any_md if any_md is not None else default


def weekday_date(name: str, *, week_monday: date) -> date | None:
    """The date of weekday ``name`` in the week starting ``week_monday``."""
    if name not in WEEKDAYS:
        return None
    return week_monday + timedelta(days=WEEKDAYS.index(name))


def as_datetime(day: date) -> datetime:
    """A due date as a datetime: midnight at the start of the day.

    Matches the HAC mapping's convention (``strptime`` date-only), so
    records from every source share one due_at shape.
    """
    return datetime(day.year, day.month, day.day)
