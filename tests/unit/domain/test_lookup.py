"""Lookup[T]: Found/Missing behavior, exhaustive match, unwrap contract."""

import pytest

from school.domain import Found, Lookup, Missing, MissingRecordError


def _describe(lookup: Lookup[int]) -> str:
    match lookup:
        case Found(value=value):
            return f"found {value}"
        case Missing(reason=reason):
            return f"missing: {reason}"


def test_found_carries_value() -> None:
    lookup: Lookup[int] = Found(value=41)
    assert lookup.found
    assert lookup.unwrap() == 41
    assert _describe(lookup) == "found 41"


def test_missing_carries_reason_and_unwrap_raises() -> None:
    lookup: Lookup[int] = Missing(reason="no driver for key")
    assert not lookup.found
    assert _describe(lookup) == "missing: no driver for key"
    with pytest.raises(MissingRecordError, match="no driver for key"):
        lookup.unwrap()


def test_lookup_variants_are_distinct_frozen_values() -> None:
    assert Found(value=1) == Found(value=1)
    assert Found(value=1) != Missing(reason="1")
    assert Missing(reason="x") == Missing(reason="x")
