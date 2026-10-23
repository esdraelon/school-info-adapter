"""Domain exceptions.

Pattern: domain error taxonomy. Raised when a caller treats absence as an
error (convention C1): single-item lookups return ``Lookup`` instead of
raising, but a caller who explicitly unwraps a ``Missing`` gets this.
"""


class MissingRecordError(LookupError):
    """A required record was absent (raised by ``Missing.unwrap``)."""
