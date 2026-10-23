"""Lookup[T]: the single-item absence type (convention C1, decision D-1).

Pattern: sum type (two frozen dataclasses + a union alias), consumed by
exhaustive ``match``. Public APIs in this codebase signal absence exactly
three ways:

- collection queries return a list (empty when nothing matches);
- single-item reads return ``Lookup[T]`` — ``Found(value)`` or
  ``Missing(reason)``;
- a caller who requires presence unwraps and gets ``MissingRecordError``.

There is no other absence mechanism in public APIs (no ``None`` returns
from lookups, no sentinel objects).
"""

from dataclasses import dataclass
from typing import Generic, NoReturn, TypeAlias, TypeVar

from school.domain.errors import MissingRecordError

T = TypeVar("T")


@dataclass(frozen=True, kw_only=True)
class Found(Generic[T]):
    """A lookup that hit. Pattern: sum-type variant (success case)."""

    value: T

    @property
    def found(self) -> bool:
        """True for Found, False for Missing — cheap pre-match check."""
        return True

    def unwrap(self) -> T:
        """Return the value (present by construction)."""
        return self.value


@dataclass(frozen=True, kw_only=True)
class Missing:
    """A lookup that missed, with a human-readable reason.

    Pattern: sum-type variant (absence case). ``reason`` says *why* the
    record is absent (unknown key, no runs yet, ...) so health surfaces
    can show it instead of a bare "not found".
    """

    reason: str

    @property
    def found(self) -> bool:
        """True for Found, False for Missing — cheap pre-match check."""
        return False

    def unwrap(self) -> NoReturn:
        """Raise MissingRecordError: absence treated as an error (C1)."""
        raise MissingRecordError(self.reason)


#: Single-item lookup result: ``Found(value)`` or ``Missing(reason)``.
Lookup: TypeAlias = Found[T] | Missing
