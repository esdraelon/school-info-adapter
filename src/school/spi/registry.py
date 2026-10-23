"""DriverRegistry. Pattern: Registry (convention C2).

The composition root builds the registry from a plain mapping of
SourceKind -> driver. Selection is a mapping lookup — there is no if/else
(or match) on kind anywhere in selection code, so adding or swapping a
driver is a wiring change, never a logic change.
"""

from collections.abc import Mapping

from school.domain import Found, Lookup, Missing, SourceKind
from school.spi.driver import SourceDriver


class DriverRegistry:
    """An immutable kind -> driver mapping with Lookup-based access."""

    def __init__(self, drivers: Mapping[SourceKind, SourceDriver]) -> None:
        self._drivers: dict[SourceKind, SourceDriver] = dict(drivers)

    def driver_for(self, kind: SourceKind) -> Lookup[SourceDriver]:
        """The driver registered for ``kind``, or Missing with the reason."""
        driver = self._drivers.get(kind)
        if driver is None:
            return Missing(reason=f"no source driver registered for {kind.value}")
        return Found(value=driver)

    def kinds(self) -> tuple[SourceKind, ...]:
        """Registered kinds, deterministically ordered by value."""
        return tuple(sorted(self._drivers, key=lambda kind: kind.value))
