"""Enum vocabularies: values, and ItemStatus richness ordering."""

from school.domain import ItemStatus, RunOutcome, SourceKind


def test_source_kind_values_are_storage_strings() -> None:
    assert {kind.value for kind in SourceKind} == {
        "hac",
        "team_site",
        "agenda",
        "classroom_web",
    }
    assert SourceKind("classroom_web") is SourceKind.CLASSROOM_WEB


def test_item_status_is_ordered_by_information_richness() -> None:
    assert ItemStatus.ASSIGNED < ItemStatus.MISSING
    assert ItemStatus.MISSING < ItemStatus.DONE
    assert ItemStatus.DONE < ItemStatus.GRADED
    assert max(ItemStatus) is ItemStatus.GRADED
    assert min(ItemStatus) is ItemStatus.ASSIGNED


def test_run_outcome_values() -> None:
    assert {outcome.value for outcome in RunOutcome} == {
        "success",
        "partial",
        "failure",
    }
