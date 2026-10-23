"""Team-table deck parsing ("team-table-v1"). Pattern: Strategy.

The team agenda deck (all core subjects in one deck) does not lay its
grid out as separate text blocks like the per-subject decks: each
slide's whole table is ONE text block, flattened row-major —

    <title block> "8th Grade Team Weekly Agenda ... Week 10/19 - 10/23"
    <table block> day headers ("Monday 10/19" ... "Friday 10/23"),
    then per subject section: a header paragraph ("ELA", "Math",
    "Social Studies", "Science" — the header may carry the section's
    link label as an extra line, e.g. "ELA\\nSlides"), then the day
    cells as paragraphs in Mon..Fri order. A section with two tracks
    (Math: Math 8 + Algebra I) has two paragraphs per day.

Cells are ``Topic:`` / ``Agenda:`` / ``Due:`` / ``Homework:`` line
groups (Science cells are free text under the same prefixes). The
Math sections are stubs — every cell's agenda is "Class Calendar",
a pointer at the per-subject decks — and are skipped by rule.

``split_table`` is shared with the tutorial parser (same table shape).
Due grammar: an ``FD`` date overrides; a bare M/D is used as-is; a
weekday name means that day of this week; "Next week" means next
Friday; anything else (including "NA" and prose) falls back to the
cell's own day.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from school.domain import (
    CourseRef,
    DriverRecord,
    ItemStatus,
    ResourceKind,
    ResourceRecord,
    SourceKind,
    StudentRef,
)
from school.driver_web.dates import (
    as_datetime,
    infer_year,
    weekday_date,
)
from school.driver_web.parsers import SlideRecords, kind_for_text
from school.driver_web.slides import SlideAlt

DEFAULT_SUBJECTS = ("ELA", "Math", "Social Studies", "Science")

_DAY_HEADER_RE = re.compile(
    r"^(Monday|Tuesday|Wednesday|Thursday|Friday)\s+(\d{1,2})/(\d{1,2})$"
)
_FIELD_RE = re.compile(
    r"^(topic|agenda|due|homework)\s*:\s*(.*)$", re.IGNORECASE
)
_FD_RE = re.compile(r"\bFD\b\s*:?\s*(\d{1,2})/(\d{1,2})", re.IGNORECASE)
_MD_RE = re.compile(r"(\d{1,2})/(\d{1,2})")


@dataclass(frozen=True, kw_only=True)
class TableSection:
    """One subject's row: its cell paragraphs, ungrouped."""

    subject: str
    paragraphs: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class TableSplit:
    """A flattened table: raw day headers + subject sections."""

    days: tuple[tuple[str, int, int], ...]  # (weekday name, month, day)
    sections: tuple[TableSection, ...]


def split_table(text: str, *, subjects: tuple[str, ...]) -> TableSplit:
    """Split flattened table text into day headers + subject sections."""
    days: list[tuple[str, int, int]] = []
    sections: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    for raw in text.split("\n\n"):
        paragraph = raw.strip()
        day = _DAY_HEADER_RE.match(paragraph)
        if day and current is None:
            days.append(
                (day.group(1), int(day.group(2)), int(day.group(3)))
            )
            continue
        first_line = paragraph.split("\n", 1)[0].strip()
        if first_line in subjects:
            current = []
            sections.append((first_line, current))
            continue
        if current is not None:
            current.append(paragraph)
    return TableSplit(
        days=tuple(days),
        sections=tuple(
            TableSection(subject=subject, paragraphs=tuple(paras))
            for subject, paras in sections
        ),
    )


def resolve_days(
    split: TableSplit, *, reference: date
) -> tuple[date, ...]:
    """Resolve raw day headers to dates (year inferred off Monday)."""
    if not split.days:
        return ()
    name0, month0, day0 = split.days[0]
    anchor = infer_year(month0, day0, reference=reference)
    return tuple(
        infer_year(month, day, reference=anchor)
        for _name, month, day in split.days
    )


def group_cells(paragraphs: tuple[str, ...]) -> tuple[str, ...] | None:
    """Group a section's paragraphs into exactly 5 day cells.

    5 paragraphs -> one per day; a multiple of 5 -> that many tracks
    per day, joined. Anything else cannot be day-aligned from flat
    text and returns None.
    """
    count = len(paragraphs)
    if count == 5:
        return paragraphs
    if count > 5 and count % 5 == 0:
        per_day = count // 5
        return tuple(
            "\n\n".join(paragraphs[i * per_day : (i + 1) * per_day])
            for i in range(5)
        )
    return None


def find_table_text(slide: SlideAlt, *, subjects: tuple[str, ...]) -> str | None:
    """The slide's flattened-table text block, if it has one."""
    for text in slide.texts:
        if "Monday" in text.text and any(
            f"\n{subject}" in f"\n{text.text}" for subject in subjects
        ):
            return text.text
    return None


class TeamTableParser:
    """DeckSlideParser strategy for the team agenda deck.

    The deck-level ``course`` is ignored: the table's sections *are*
    the courses (ELA / Math / Social Studies / Science).
    """

    def __init__(
        self, *, subjects: tuple[str, ...] = DEFAULT_SUBJECTS
    ) -> None:
        self._subjects = subjects

    def parse_slide(
        self,
        slide: SlideAlt,
        *,
        course: CourseRef | None,
        student: StudentRef,
        source_kind: SourceKind,
        source_url: str,
        scraped_at: datetime,
        reference: date,
    ) -> SlideRecords:
        table_text = find_table_text(slide, subjects=self._subjects)
        if table_text is None:
            return SlideRecords(notes=("no table block; slide skipped",))
        split = split_table(table_text, subjects=self._subjects)
        days = resolve_days(split, reference=reference)
        monday = self._monday(days)
        if monday is None or not days:
            return SlideRecords(notes=("no week anchor; slide skipped",))
        drivers: list[DriverRecord] = []
        notes: list[str] = []
        for section in split.sections:
            cells = group_cells(section.paragraphs)
            if cells is None:  # tolerate: align by order, note it
                cells = self._loose_cells(section.paragraphs)
                notes.append(
                    f"{section.subject}: {len(section.paragraphs)} cells "
                    f"for 5 days; aligned by order"
                )
            drivers.extend(
                self._section_drivers(
                    section.subject, cells, days,
                    monday=monday, student=student,
                    source_kind=source_kind, source_url=source_url,
                    scraped_at=scraped_at, reference=reference,
                )
            )
        return SlideRecords(
            drivers=tuple(drivers),
            resources=self._slide_resources(
                slide, student=student, source_kind=source_kind,
                scraped_at=scraped_at,
            ),
            week_monday=monday,
            notes=tuple(notes),
        )

    @staticmethod
    def _monday(days: tuple[date, ...]) -> date | None:
        """The Monday of the week the resolved day headers belong to."""
        if not days:
            return None
        first = days[0]
        return first - timedelta(days=first.weekday())

    @staticmethod
    def _loose_cells(paragraphs: tuple[str, ...]) -> tuple[str, ...]:
        cells = list(paragraphs[:5])
        if len(paragraphs) > 5:
            cells[4] = "\n\n".join(paragraphs[4:])
        while len(cells) < 5:
            cells.append("")
        return tuple(cells)

    def _section_drivers(
        self,
        subject: str,
        cells: tuple[str, ...],
        days: tuple[date, ...],
        *,
        monday: date,
        student: StudentRef,
        source_kind: SourceKind,
        source_url: str,
        scraped_at: datetime,
        reference: date,
    ) -> list[DriverRecord]:
        course = CourseRef(name=subject)
        drivers: list[DriverRecord] = []
        for cell, day in zip(cells, days, strict=False):
            driver = self._cell_driver(
                cell,
                course=course,
                day=day,
                monday=monday,
                student=student,
                source_kind=source_kind,
                source_url=source_url,
                scraped_at=scraped_at,
                reference=reference,
            )
            if driver is not None:
                drivers.append(driver)
        return drivers

    def _cell_driver(
        self,
        cell: str,
        *,
        course: CourseRef,
        day: date,
        monday: date,
        student: StudentRef,
        source_kind: SourceKind,
        source_url: str,
        scraped_at: datetime,
        reference: date,
    ) -> DriverRecord | None:
        fields: dict[str, str] = {}
        loose: list[str] = []
        current: str | None = None
        for line in (ln.strip() for ln in cell.split("\n")):
            match = _FIELD_RE.match(line)
            if match:
                current = match.group(1).lower()
                fields[current] = match.group(2).strip()
            elif line and current is not None:
                fields[current] = f"{fields[current]} {line}".strip()
            elif line:
                loose.append(line)
        agenda = fields.get("agenda", "").strip()
        topic = fields.get("topic", "").strip()
        # Repeated-prefix artefact in the source ("Topic: Topic: ...").
        while topic.lower().startswith("topic:"):
            topic = topic[6:].strip()
        if agenda.lower() == "class calendar":
            return None  # stub cell: detail lives in the subject deck
        title = agenda or topic or (loose[0] if loose else "")
        # Decorative marker tokens ("*GRADED* Quiz 8.4A") aren't title.
        title = re.sub(r"^\*[^*]*\*\s*", "", title.strip()).strip()
        if not title:
            return None
        due = self._cell_due(
            fields.get("due", ""), day=day, monday=monday,
            reference=reference,
        )
        return DriverRecord(
            student=student,
            course=course,
            title=title,
            kind=kind_for_text(f"{title} {topic}"),
            status=ItemStatus.ASSIGNED,
            source_kind=source_kind,
            scraped_at=scraped_at,
            due_at=as_datetime(due),
            source_ref=source_url,
        )

    @staticmethod
    def _cell_due(
        due_text: str, *, day: date, monday: date, reference: date
    ) -> date:
        text = due_text.strip()
        if not text:
            return day
        fd = _FD_RE.search(text)
        if fd:
            return infer_year(
                int(fd.group(1)), int(fd.group(2)), reference=reference
            )
        md = _MD_RE.search(text)
        if md:
            return infer_year(
                int(md.group(1)), int(md.group(2)), reference=reference
            )
        if "next week" in text.lower():
            return monday + timedelta(days=11)  # next Friday
        for name in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday"):
            if name.lower() in text.lower():
                resolved = weekday_date(name, week_monday=monday)
                if resolved is not None:
                    return resolved
        return day

    @staticmethod
    def _slide_resources(
        slide: SlideAlt,
        *,
        student: StudentRef,
        source_kind: SourceKind,
        scraped_at: datetime,
    ) -> tuple[ResourceRecord, ...]:
        """The slide's links as resources (deduped by URL).

        Links can't be attributed to a table cell from flat text, so
        they surface course-less; their labels ("Class Calendar",
        "Revolutionary War Webquest", ...) carry the context.
        """
        resources: list[ResourceRecord] = []
        seen: set[str] = set()
        for link in slide.links:
            if link.url in seen:
                continue
            seen.add(link.url)
            resources.append(
                ResourceRecord(
                    title=link.label or "Link",
                    kind=(
                        ResourceKind.DOCUMENT
                        if "docs.google.com" in link.url
                        else ResourceKind.LINK
                    ),
                    url=link.url,
                    source_kind=source_kind,
                    scraped_at=scraped_at,
                    student=student,
                )
            )
        return tuple(resources)
