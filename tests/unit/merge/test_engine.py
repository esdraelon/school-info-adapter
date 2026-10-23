"""Merge engine: precedence strategy, status tiebreak, non-merge cases."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from school.domain import (
    CourseRef,
    DriverKind,
    DriverRecord,
    ItemStatus,
    SourceKind,
    StudentRef,
)
from school.merge import DEFAULT_PRECEDENCE, merge_driver_records

UTC = timezone.utc
T0 = datetime(2026, 10, 22, 8, 0, tzinfo=UTC)
DUE = datetime(2026, 10, 23, 23, 59, tzinfo=UTC)
SAM = StudentRef(id="sam", display_name="Sam")
MATH = CourseRef(name="Math 8")


def sighting(
    source: SourceKind,
    *,
    title: str = "Workbook p. 42",
    course: CourseRef = MATH,
    student: StudentRef = SAM,
    status: ItemStatus = ItemStatus.ASSIGNED,
    due_at: datetime | None = DUE,
    score_text: str | None = None,
    scraped_at: datetime = T0,
) -> DriverRecord:
    return DriverRecord(
        student=student,
        course=course,
        title=title,
        kind=DriverKind.ASSIGNMENT,
        status=status,
        source_kind=source,
        scraped_at=scraped_at,
        due_at=due_at,
        score_text=score_text,
    )


def test_empty_input_merges_to_empty() -> None:
    assert merge_driver_records([]) == []


def test_single_sighting_passes_through() -> None:
    record = sighting(SourceKind.HAC)
    assert merge_driver_records([record]) == [record]


@pytest.mark.parametrize(
    ("sources", "expected_source"),
    [
        ([SourceKind.HAC, SourceKind.CLASSROOM_WEB], SourceKind.CLASSROOM_WEB),
        ([SourceKind.CLASSROOM_WEB, SourceKind.HAC], SourceKind.CLASSROOM_WEB),
        ([SourceKind.AGENDA, SourceKind.HAC], SourceKind.HAC),
        ([SourceKind.TEAM_SITE, SourceKind.AGENDA], SourceKind.AGENDA),
        (
            [SourceKind.TEAM_SITE, SourceKind.AGENDA, SourceKind.HAC],
            SourceKind.HAC,
        ),
    ],
)
def test_higher_precedence_source_wins(
    sources: list[SourceKind], expected_source: SourceKind
) -> None:
    merged = merge_driver_records([sighting(source) for source in sources])
    assert len(merged) == 1
    assert merged[0].source_kind is expected_source


def test_winning_fields_come_from_the_winner() -> None:
    hac = sighting(
        SourceKind.HAC, status=ItemStatus.GRADED, score_text="9/10"
    )
    classroom = sighting(SourceKind.CLASSROOM_WEB, status=ItemStatus.DONE)
    merged = merge_driver_records([hac, classroom])
    assert merged[0].status is ItemStatus.DONE
    assert merged[0].score_text is None  # winner's fields, wholesale


def test_injected_reversed_precedence_flips_the_winner() -> None:
    """C2 proof: same inputs, different injected policy, different output."""
    records = [
        sighting(SourceKind.HAC, status=ItemStatus.GRADED, score_text="9/10"),
        sighting(SourceKind.CLASSROOM_WEB, status=ItemStatus.DONE),
    ]
    default = merge_driver_records(records)
    reversed_policy = {kind: -rank for kind, rank in DEFAULT_PRECEDENCE.items()}
    flipped = merge_driver_records(records, precedence=reversed_policy)
    assert default[0].source_kind is SourceKind.CLASSROOM_WEB
    assert flipped[0].source_kind is SourceKind.HAC
    assert flipped[0].score_text == "9/10"


def test_status_richness_breaks_equal_rank_ties() -> None:
    """Two kinds at the same injected rank: GRADED beats ASSIGNED."""
    equal_ranks = {SourceKind.HAC: 5, SourceKind.AGENDA: 5}
    graded = sighting(SourceKind.HAC, status=ItemStatus.GRADED)
    assigned = sighting(SourceKind.AGENDA, status=ItemStatus.ASSIGNED)
    merged = merge_driver_records([assigned, graded], precedence=equal_ranks)
    assert merged[0].status is ItemStatus.GRADED
    assert merged[0].source_kind is SourceKind.HAC


def test_fresher_sighting_breaks_full_ties() -> None:
    older = sighting(SourceKind.HAC, title="Workbook p. 42", scraped_at=T0)
    fresher = sighting(
        SourceKind.HAC,
        title="workbook p 42",
        scraped_at=T0 + timedelta(hours=3),
    )
    merged = merge_driver_records([older, fresher])
    assert len(merged) == 1
    assert merged[0].title == "workbook p 42"


def test_merged_scraped_at_is_the_group_max() -> None:
    stale_classroom = sighting(SourceKind.CLASSROOM_WEB, scraped_at=T0)
    fresh_hac = sighting(
        SourceKind.HAC, scraped_at=T0 + timedelta(hours=6)
    )
    merged = merge_driver_records([stale_classroom, fresh_hac])
    assert merged[0].source_kind is SourceKind.CLASSROOM_WEB  # precedence
    assert merged[0].scraped_at == T0 + timedelta(hours=6)  # but freshest stamp


def test_conflicting_due_times_same_day_resolve_by_precedence() -> None:
    hac_midnight = sighting(
        SourceKind.HAC, due_at=datetime(2026, 10, 23, 0, 0, tzinfo=UTC)
    )
    classroom_eod = sighting(SourceKind.CLASSROOM_WEB, due_at=DUE)
    merged = merge_driver_records([hac_midnight, classroom_eod])
    assert len(merged) == 1
    assert merged[0].due_at == DUE


@pytest.mark.parametrize(
    "other",
    [
        sighting(SourceKind.HAC, due_at=datetime(2026, 10, 24, tzinfo=UTC)),
        sighting(SourceKind.HAC, course=CourseRef(name="Science")),
        sighting(
            SourceKind.HAC, student=StudentRef(id="owen", display_name="Owen")
        ),
        sighting(SourceKind.HAC, title="Workbook p. 43"),
        sighting(SourceKind.HAC, due_at=None),
    ],
    ids=["different-day", "different-course", "different-student", "different-title", "undated"],
)
def test_items_that_must_not_merge(other: DriverRecord) -> None:
    base = sighting(SourceKind.CLASSROOM_WEB)
    merged = merge_driver_records([base, other])
    assert len(merged) == 2


def test_output_order_is_deterministic() -> None:
    records = [
        sighting(SourceKind.HAC, title="B item"),
        sighting(SourceKind.HAC, title="A item"),
        replace(
            sighting(SourceKind.HAC, title="A item"),
            course=CourseRef(name="Art"),
        ),
    ]
    forward = merge_driver_records(records)
    backward = merge_driver_records(list(reversed(records)))
    assert forward == backward
    assert [r.course.name for r in forward] == ["Art", "Math 8", "Math 8"]
