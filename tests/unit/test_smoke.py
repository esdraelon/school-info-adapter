"""SCAFFOLD ONLY — smoke test for school.domain.smoke. Deleted in M1."""

from school.domain.smoke import smoke_message


def test_smoke_message() -> None:
    assert smoke_message() == "school adapter scaffold"
