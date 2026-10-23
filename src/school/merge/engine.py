"""Cross-source merge of DriverRecords. Pattern: Strategy (precedence policy).

Pure functions, no I/O. Records that share a DedupKey are sightings of the
same item from different sources; each group collapses to one record whose
fields come from the *winning* sighting, where winning is decided by an
injected precedence strategy (convention C2 — the policy is a parameter,
never an if/else chain in this code):

1. higher ``precedence`` rank of the record's source kind wins
   (default: CLASSROOM_WEB > HAC > AGENDA > TEAM_SITE; a source kind
   missing from the map ranks below every listed kind);
2. at equal rank, the richer ItemStatus wins (GRADED > DONE > MISSING >
   ASSIGNED — the enum's own ordering);
3. at equal rank and status, the most recently scraped sighting wins.

The merged record's ``scraped_at`` is the max across the group (C10: the
merged view is exactly as fresh as its freshest sighting).
"""

from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import date, datetime
from types import MappingProxyType

from school.domain import DedupKey, DriverRecord, SourceKind

#: Default source precedence for merged field selection. Classroom's To-do
#: page has the fullest lifecycle view; HAC is next (authoritative grades,
#: laggy); the agenda warns earliest but knows no status; the team site is
#: the hub of last resort.
DEFAULT_PRECEDENCE: Mapping[SourceKind, int] = MappingProxyType(
    {
        SourceKind.CLASSROOM_WEB: 3,
        SourceKind.HAC: 2,
        SourceKind.AGENDA: 1,
        SourceKind.TEAM_SITE: 0,
    }
)


def _winner_key(
    record: DriverRecord, precedence: Mapping[SourceKind, int]
) -> tuple[int, int, datetime]:
    return (
        precedence.get(record.source_kind, -1),
        int(record.status),
        record.scraped_at,
    )


def _merge_group(
    records: list[DriverRecord], precedence: Mapping[SourceKind, int]
) -> DriverRecord:
    winner = max(records, key=lambda r: _winner_key(r, precedence))
    freshest = max(r.scraped_at for r in records)
    return replace(winner, scraped_at=freshest)


def merge_driver_records(
    records: Iterable[DriverRecord],
    *,
    precedence: Mapping[SourceKind, int] | None = None,
) -> list[DriverRecord]:
    """Collapse cross-source sightings of the same due item into one record.

    ``precedence`` is the selection strategy (see module docstring); pass a
    different mapping to get different output with no code change (C2).
    Output is deterministically ordered by (student, course, due date,
    title) using the normalized dedup-key components.
    """
    policy = DEFAULT_PRECEDENCE if precedence is None else precedence
    groups: dict[DedupKey, list[DriverRecord]] = {}
    for record in records:
        groups.setdefault(DedupKey.from_record(record), []).append(record)
    merged = [
        (key, _merge_group(group, policy)) for key, group in groups.items()
    ]
    merged.sort(
        key=lambda item: (
            item[0].student_id,
            item[0].course_key,
            item[0].due_date or date.min,
            item[0].title_key,
        )
    )
    return [record for _, record in merged]
