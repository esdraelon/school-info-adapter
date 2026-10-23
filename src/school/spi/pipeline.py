"""Function composition for scrape lifecycles. Pattern: Pipeline (C3).

A driver's scrape is a pipeline of stage callables — fetch -> extract ->
normalize -> persist — *composed*, never inherited: there is no base
class with a template method. Stages are plain callables over one working
value type chosen by the driver (often a small frozen dataclass evolved
with ``dataclasses.replace``); these helpers wire them together.
"""

from collections.abc import Callable, Iterable
from typing import Generic, TypeVar

T = TypeVar("T")
A = TypeVar("A")
B = TypeVar("B")
C = TypeVar("C")


def compose(f: Callable[[B], C], g: Callable[[A], B]) -> Callable[[A], C]:
    """Compose two functions: ``compose(f, g)(x) == f(g(x))``."""

    def composed(value: A) -> C:
        return f(g(value))

    return composed


def pipe(value: T, *stages: Callable[[T], T]) -> T:
    """Run ``value`` through ``stages`` left to right; no stages = identity."""
    result = value
    for stage in stages:
        result = stage(result)
    return result


class Pipeline(Generic[T]):
    """An immutable, reusable sequence of same-type stages.

    Pattern: Pipeline. ``then`` returns a *new* pipeline (the original is
    unchanged), so a composition root can share a base pipeline and extend
    it per driver without aliasing surprises.
    """

    def __init__(self, stages: Iterable[Callable[[T], T]] = ()) -> None:
        self._stages: tuple[Callable[[T], T], ...] = tuple(stages)

    @property
    def stages(self) -> tuple[Callable[[T], T], ...]:
        """The stages, in run order."""
        return self._stages

    def then(self, stage: Callable[[T], T]) -> "Pipeline[T]":
        """A new pipeline with ``stage`` appended."""
        return Pipeline((*self._stages, stage))

    def run(self, value: T) -> T:
        """Run the stages over ``value``, left to right."""
        return pipe(value, *self._stages)


class ScrapePipeline(Pipeline[T]):
    """A Pipeline staged as a scrape lifecycle (fetch -> extract -> ...).

    Semantically identical to Pipeline; the distinct name lets driver code
    declare intent, and gives later milestones one place to grow
    scrape-specific behavior if it is ever needed.
    """
