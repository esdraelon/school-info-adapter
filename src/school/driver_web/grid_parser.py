"""Weekly-grid deck parser ("weekly-grid-v1"). Pattern: Strategy.

The per-subject decks (Math 8, Algebra I) lay one week out as a grid:
a row of day-header text blocks ("Monday 10/19", ..., each carrying
its own M/D date), then per day column a topic block (top) and a
practice block (bottom). Geometry assigns content to days: blocks and
links snap to the nearest day header's column by x; within a column,
document order by y separates topic (first) from practice (second).

Cell -> records:
- topic block -> a driver titled by its first line (kind from the
  block text), due per the date grammar (an ``FD`` date overrides);
  its ``Notes: X`` lines pair with the cell's links, in order, as
  NOTES resources, and leftover links become LINK resources.
- practice block -> a PRACTICE driver (title = the block's text),
  due likewise; its links become DOCUMENT resources when the block
  is marked ``(key)``, LINK otherwise.
- "No School" / "None" cells produce nothing.

The week anchor is the Monday header's date; a slide without one is
skipped with a note (the decks' last slide is a blank template).
"""

import re
from dataclasses import dataclass
from datetime import date, datetime

from school.domain import (
    CourseRef,
    DriverRecord,
    ItemStatus,
    ResourceKind,
    ResourceRecord,
    SourceKind,
    StudentRef,
)
from school.driver_web.dates import as_datetime, infer_year, resolve_due
from school.driver_web.parsers import SlideRecords, kind_for_text
from school.driver_web.slides import SlideAlt, SlideLink, SlideText

_HEADER_RE = re.compile(
    r"^(Monday|Tuesday|Wednesday|Thursday|Friday)\s+(\d{1,2})/(\d{1,2})\s*$"
)
_NOTES_RE = re.compile(r"^notes?\s*:\s*(.+)$", re.IGNORECASE)
_PRACTICE_PREFIX_RE = re.compile(r"^indiv\.\s*practice\s*:\s*", re.IGNORECASE)


@dataclass(frozen=True, kw_only=True)
class _Column:
    name: str
    day: date
    center_x: float


@dataclass(kw_only=True)
class _Cell:
    blocks: list[SlideText]
    links: list[SlideLink]


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.split("\n") if line.strip()]


class WeeklyGridParser:
    """DeckSlideParser strategy for the per-subject weekly-grid decks."""

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
        columns = self._columns(slide, reference)
        monday = next((c for c in columns if c.name == "Monday"), None)
        if monday is None or len(columns) < 3:
            return SlideRecords(
                notes=("no Monday week anchor; slide skipped",)
            )
        cells = self._cells(slide, columns)
        drivers: list[DriverRecord] = []
        resources: list[ResourceRecord] = []
        assert course is not None  # grid decks always carry a course
        for column, cell in zip(columns, cells, strict=True):
            made_drivers, made_resources = self._cell_records(
                cell,
                column=column,
                course=course,
                student=student,
                source_kind=source_kind,
                source_url=source_url,
                scraped_at=scraped_at,
                reference=reference,
            )
            drivers.extend(made_drivers)
            resources.extend(made_resources)
        # Links that snapped to no column still surface, course-level.
        for link in self._orphan_links(slide, columns):
            resources.append(
                ResourceRecord(
                    title=link.label or "Link",
                    kind=ResourceKind.LINK,
                    url=link.url,
                    source_kind=source_kind,
                    scraped_at=scraped_at,
                    student=student,
                    course=course,
                )
            )
        return SlideRecords(
            drivers=tuple(drivers),
            resources=tuple(resources),
            week_monday=monday.day,
        )

    def _columns(
        self, slide: SlideAlt, reference: date
    ) -> list[_Column]:
        headers: list[tuple[str, int, int, float]] = []
        for text in slide.texts:
            if text.rect is None:
                continue
            lines = _lines(text.text)
            if not lines:
                continue
            match = _HEADER_RE.match(lines[0])
            if match:
                headers.append(
                    (
                        match.group(1),
                        int(match.group(2)),
                        int(match.group(3)),
                        text.rect.center_x,
                    )
                )
        monday_md = next(
            ((m, d) for name, m, d, _x in headers if name == "Monday"), None
        )
        if monday_md is None:
            return []
        monday = infer_year(monday_md[0], monday_md[1], reference=reference)
        columns = [
            _Column(
                name=name,
                day=infer_year(month, day, reference=monday),
                center_x=x,
            )
            for name, month, day, x in headers
        ]
        return sorted(columns, key=lambda c: c.center_x)

    def _cells(
        self, slide: SlideAlt, columns: list[_Column]
    ) -> list[_Cell]:
        header_y = min(
            text.rect.y
            for text in slide.texts
            if text.rect is not None and _lines(text.text)
            and _HEADER_RE.match(_lines(text.text)[0])
        )
        spacing = self._spacing(columns)
        cells = [_Cell(blocks=[], links=[]) for _ in columns]

        def column_for(center_x: float) -> int | None:
            best = min(
                range(len(columns)),
                key=lambda i: abs(columns[i].center_x - center_x),
            )
            if abs(columns[best].center_x - center_x) <= spacing * 0.6:
                return best
            return None

        for text in slide.texts:
            if text.rect is None or text.rect.y <= header_y + 5:
                continue
            if _HEADER_RE.match((_lines(text.text) or [""])[0]):
                continue
            index = column_for(text.rect.center_x)
            if index is not None:
                cells[index].blocks.append(text)
        for cell in cells:
            cell.blocks.sort(key=lambda b: (b.rect.y if b.rect else 0.0))
        for link in slide.links:
            if link.rect is None:
                continue
            index = column_for(link.rect.center_x)
            if index is not None:
                cells[index].links.append(link)
        return cells

    @staticmethod
    def _spacing(columns: list[_Column]) -> float:
        gaps = [
            b.center_x - a.center_x
            for a, b in zip(columns, columns[1:], strict=False)
        ]
        return sorted(gaps)[len(gaps) // 2] if gaps else 192.0

    def _orphan_links(
        self, slide: SlideAlt, columns: list[_Column]
    ) -> list[SlideLink]:
        spacing = self._spacing(columns)
        orphans = []
        for link in slide.links:
            if link.rect is None:
                continue
            nearest = min(
                abs(c.center_x - link.rect.center_x) for c in columns
            )
            if nearest > spacing * 0.6:
                orphans.append(link)
        return orphans

    def _cell_records(
        self,
        cell: _Cell,
        *,
        column: _Column,
        course: CourseRef,
        student: StudentRef,
        source_kind: SourceKind,
        source_url: str,
        scraped_at: datetime,
        reference: date,
    ) -> tuple[list[DriverRecord], list[ResourceRecord]]:
        if not cell.blocks:
            return [], []
        topic, *rest = cell.blocks
        practice = rest[0] if rest else None
        if len(rest) > 1:  # template drift: fold extras into practice
            extra = "\n".join(block.text for block in rest[1:])
            assert practice is not None
            practice = SlideText(
                text=practice.text + "\n" + extra, rect=practice.rect
            )
        topic_links, practice_links = self._split_links(cell, topic, practice)
        drivers: list[DriverRecord] = []
        resources: list[ResourceRecord] = []
        made = self._topic_records(
            topic, topic_links, day=column.day, course=course,
            student=student, source_kind=source_kind,
            source_url=source_url, scraped_at=scraped_at,
            reference=reference,
        )
        drivers.extend(made[0])
        resources.extend(made[1])
        if practice is not None:
            made = self._practice_records(
                practice, practice_links, day=column.day, course=course,
                student=student, source_kind=source_kind,
                source_url=source_url, scraped_at=scraped_at,
                reference=reference,
            )
            drivers.extend(made[0])
            resources.extend(made[1])
        return drivers, resources

    @staticmethod
    def _split_links(
        cell: _Cell, topic: SlideText, practice: SlideText | None
    ) -> tuple[list[SlideLink], list[SlideLink]]:
        """Assign a cell's links to its topic/practice blocks by y:
        a link belongs to the last block whose top is at or above it."""
        if practice is None or practice.rect is None or topic.rect is None:
            return list(cell.links), []
        boundary = practice.rect.y
        topic_links = [
            link for link in cell.links
            if link.rect is None or link.rect.y < boundary
        ]
        practice_links = [
            link for link in cell.links
            if link.rect is not None and link.rect.y >= boundary
        ]
        return topic_links, practice_links

    def _topic_records(
        self,
        block: SlideText,
        links: list[SlideLink],
        *,
        day: date,
        course: CourseRef,
        student: StudentRef,
        source_kind: SourceKind,
        source_url: str,
        scraped_at: datetime,
        reference: date,
    ) -> tuple[list[DriverRecord], list[ResourceRecord]]:
        content = _lines(block.text)
        if not content or content[0].lower().startswith("no school"):
            return [], []
        title = content[0]
        driver = DriverRecord(
            student=student,
            course=course,
            title=title,
            kind=kind_for_text(block.text),
            status=ItemStatus.ASSIGNED,
            source_kind=source_kind,
            scraped_at=scraped_at,
            due_at=as_datetime(
                resolve_due(block.text, default=day, reference=reference)
            ),
            source_ref=source_url,
        )
        resources: list[ResourceRecord] = []
        remaining = list(links)
        for line in content:
            notes = _NOTES_RE.match(line)
            if notes and remaining:
                link = remaining.pop(0)
                resources.append(
                    ResourceRecord(
                        title=notes.group(1).strip(),
                        kind=ResourceKind.NOTES,
                        url=link.url,
                        source_kind=source_kind,
                        scraped_at=scraped_at,
                        student=student,
                        course=course,
                    )
                )
        for link in remaining:
            resources.append(
                ResourceRecord(
                    title=link.label or title,
                    kind=ResourceKind.LINK,
                    url=link.url,
                    source_kind=source_kind,
                    scraped_at=scraped_at,
                    student=student,
                    course=course,
                )
            )
        return [driver], resources

    def _practice_records(
        self,
        block: SlideText,
        links: list[SlideLink],
        *,
        day: date,
        course: CourseRef,
        student: StudentRef,
        source_kind: SourceKind,
        source_url: str,
        scraped_at: datetime,
        reference: date,
    ) -> tuple[list[DriverRecord], list[ResourceRecord]]:
        title = " ".join(_lines(block.text))
        probe = _PRACTICE_PREFIX_RE.sub("", title).strip().lower()
        if not probe or probe == "none" or probe.startswith("no school"):
            return [], []
        driver = DriverRecord(
            student=student,
            course=course,
            title=title,
            kind=kind_for_text(block.text),
            status=ItemStatus.ASSIGNED,
            source_kind=source_kind,
            scraped_at=scraped_at,
            due_at=as_datetime(
                resolve_due(block.text, default=day, reference=reference)
            ),
            source_ref=source_url,
        )
        is_key = "(key)" in block.text.lower()
        resources = [
            ResourceRecord(
                title=link.label or title,
                kind=ResourceKind.DOCUMENT if is_key else ResourceKind.LINK,
                url=link.url,
                source_kind=source_kind,
                scraped_at=scraped_at,
                student=student,
                course=course,
            )
            for link in links
        ]
        return [driver], resources
