"""HAC source driver: wraps the existing grades cache into normalized records.

Pattern: Adapter over the haliffer-grades-mcp cache (HacCacheAdapter owns
the file; mapping owns the raw dicts; HacDriver is the SourceDriver
strategy the registry hands out).
"""

from school.driver_hac.adapter import (
    CacheLoadError,
    CacheSnapshot,
    HacCacheAdapter,
)
from school.driver_hac.driver import HacDriver

__all__ = [
    "CacheLoadError",
    "CacheSnapshot",
    "HacCacheAdapter",
    "HacDriver",
]
