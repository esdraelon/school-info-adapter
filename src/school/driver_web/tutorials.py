"""Tutorial schedule parsing. Pattern: pure parser over the shared
team-table split (see table_parser).

The Tutorial Schedule deck uses the same flattened-table slides as the
team agenda: per week, a section per subject whose cells hold session
text ("AM (8:35-9:00)\\nJordan (2140)", "Goodie PM (4:20-4:50)").
Each non-empty cell that isn't a "No tutorials" marker becomes one
TUTORIAL ResourceRecord naming subject, day, and session text.

Two honesty constraints, both documented in the output titles:

- ResourceRecords have no session-date field, so the day lives in the
  title text ("Math tutorials — Tuesday 10/20: ...").
- When a section's paragraphs don't group into exactly 5 day cells
  (Math runs AM *and* PM sessions, flattening to 7 paragraphs), day
  alignment is unrecoverable from flat text — those sessions are
  emitted day-less, titled with the week instead.

Windowing: the deck accumulates a year of weekly slides. Only slides
whose week starts within the 7 days before the reference date (or
later) are parsed — history belongs to earlier scrapes' stores.
"""

from datetime import date, datetime, timedelta

from school.domain import (
    CourseRef,
    ResourceKind,
    ResourceRecord,
    SourceKind,
)
from school.driver_web.slides import extract_slide_alts
from school.driver_web.table_parser import (
    DEFAULT_SUBJECTS,
    find_table_text,
    group_cells,
    resolve_days,
    split_table,
)

_WINDOW = timedelta(days=7)


def parse_tutorials(
    page_html: str,
    *,
    deck_url: str,
    scraped_at: datetime,
    reference: date,
    subjects: tuple[str, ...] = DEFAULT_SUBJECTS,
) -> tuple[tuple[ResourceRecord, ...], tuple[str, ...]]:
    """Parse a tutorial deck page into (resources, notes)."""
    resources: list[ResourceRecord] = []
    notes: list[str] = []
    for slide in extract_slide_alts(page_html):
        table_text = find_table_text(slide, subjects=subjects)
        if table_text is None:
            continue
        split = split_table(table_text, subjects=subjects)
        days = resolve_days(split, reference=reference)
        if not days:
            notes.append("tutorial slide without day headers; skipped")
            continue
        monday = days[0] - timedelta(days=days[0].weekday())
        if monday < reference - _WINDOW:
            continue  # an old week; not this scrape's business
        for section in split.sections:
            resources.extend(
                _section_resources(
                    section.subject,
                    section.paragraphs,
                    days=days,
                    monday=monday,
                    deck_url=deck_url,
                    scraped_at=scraped_at,
                )
            )
    return tuple(resources), tuple(notes)


def _section_resources(
    subject: str,
    paragraphs: tuple[str, ...],
    *,
    days: tuple[date, ...],
    monday: date,
    deck_url: str,
    scraped_at: datetime,
) -> list[ResourceRecord]:
    cells = group_cells(paragraphs)
    resources: list[ResourceRecord] = []
    if cells is not None:
        for cell, day in zip(cells, days, strict=False):
            text = _session_text(cell)
            if text is None:
                continue
            title = (
                f"{subject} tutorials — "
                f"{day.strftime('%A')} {day.month}/{day.day}: {text}"
            )
            resources.append(_record(subject, title, deck_url, scraped_at))
        return resources
    for paragraph in paragraphs:
        text = _session_text(paragraph)
        if text is None:
            continue
        title = (
            f"{subject} tutorials (week of "
            f"{monday.month}/{monday.day}): {text}"
        )
        resources.append(_record(subject, title, deck_url, scraped_at))
    return resources


def _session_text(cell: str) -> str | None:
    """A cell's session text on one line; None for no-session cells."""
    text = " ".join(cell.split())
    if not text or text.lower().startswith("no tutorials"):
        return None
    return text


def _record(
    subject: str, title: str, deck_url: str, scraped_at: datetime
) -> ResourceRecord:
    return ResourceRecord(
        title=title,
        kind=ResourceKind.TUTORIAL,
        url=deck_url,
        source_kind=SourceKind.TEAM_SITE,
        scraped_at=scraped_at,
        course=CourseRef(name=subject),
    )
