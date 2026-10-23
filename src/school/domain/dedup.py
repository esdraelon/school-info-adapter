"""DedupKey: the cross-source identity of a due item.

Pattern: derived value object. The same workbook page shows up in HAC, in
the agenda deck, and in Classroom with slightly different strings; a
DedupKey is how merge (and the store's UNIQUE index) recognize them as one
item.

A key is (student id, normalized course name, normalized title, due date).
The due *date* — not the timestamp — is part of the key: sources routinely
disagree on the time (Classroom says 11:59 PM, HAC says date-only) while
agreeing on the day, and items due on different days are different items
even when everything else matches (a daily practice log is the canonical
counter-example).
"""

import json
import unicodedata
from dataclasses import dataclass
from datetime import date

from school.domain.records import DriverRecord


def normalize_text(text: str) -> str:
    """Normalize a course/title string for cross-source comparison.

    Steps, in order: Unicode NFKD decomposition (accents split off their
    base letters), casefold, every non-alphanumeric character replaced by
    a space, whitespace runs collapsed, ends trimmed. So "Unit 3,  Part 1!"
    and "unit 3 part 1" normalize identically.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    folded = decomposed.casefold()
    alnum_or_space = "".join(ch if ch.isalnum() else " " for ch in folded)
    return " ".join(alnum_or_space.split())


@dataclass(frozen=True, kw_only=True)
class DedupKey:
    """The identity of a DriverRecord across sources (see module docstring)."""

    student_id: str
    course_key: str
    title_key: str
    due_date: date | None = None

    @classmethod
    def from_record(cls, record: DriverRecord) -> "DedupKey":
        """Derive the key for a record using its normalized course/title."""
        return cls(
            student_id=record.student.id,
            course_key=normalize_text(record.course.name),
            title_key=normalize_text(record.title),
            due_date=record.due_at.date() if record.due_at is not None else None,
        )

    def to_storage(self) -> str:
        """Deterministic string form, used as the store's primary key.

        JSON of the components (due date as ISO text or null): stable
        across processes and runs, and debuggable by eye in the database.
        """
        return json.dumps(
            [
                self.student_id,
                self.course_key,
                self.title_key,
                self.due_date.isoformat() if self.due_date is not None else None,
            ],
            separators=(",", ":"),
        )
