"""Source-driver plugin interface: SourceDriver, DriverRegistry, ScrapePipeline.

Patterns: Strategy + Registry (config-driven selection without if/else,
convention C2); composed function Pipeline (C3).
"""

from school.spi.driver import ScrapeContext, ScrapeResult, SourceDriver
from school.spi.pipeline import Pipeline, ScrapePipeline, compose, pipe
from school.spi.registry import DriverRegistry

__all__ = [
    "DriverRegistry",
    "Pipeline",
    "ScrapeContext",
    "ScrapePipeline",
    "ScrapeResult",
    "SourceDriver",
    "compose",
    "pipe",
]
