"""Pipeline/compose/pipe: order, identity, immutability (C3)."""

from dataclasses import dataclass, replace

from school.spi import Pipeline, ScrapePipeline, compose, pipe


def _inc(x: int) -> int:
    return x + 1


def _double(x: int) -> int:
    return x * 2


def test_compose_applies_right_function_first() -> None:
    assert compose(_double, _inc)(3) == 8  # double(inc(3))
    assert compose(_inc, _double)(3) == 7  # inc(double(3))


def test_pipe_runs_left_to_right() -> None:
    assert pipe(3, _inc, _double) == 8
    assert pipe(3, _double, _inc) == 7


def test_pipe_with_no_stages_is_identity() -> None:
    assert pipe(5) == 5
    assert Pipeline[int]().run(5) == 5


def test_pipeline_runs_stages_in_order() -> None:
    pipeline = Pipeline([_inc, _double, _inc])
    assert pipeline.run(3) == 9
    assert pipeline.stages == (_inc, _double, _inc)


def test_then_returns_a_new_pipeline() -> None:
    base = Pipeline([_inc])
    extended = base.then(_double)
    assert base.stages == (_inc,)
    assert extended.stages == (_inc, _double)
    assert base.run(3) == 4
    assert extended.run(3) == 8


@dataclass(frozen=True)
class _Work:
    """A driver's evolving working value, evolved via replace."""

    fetched: str = ""
    extracted: int = 0


def test_scrape_pipeline_evolves_a_working_value() -> None:
    pipeline = (
        ScrapePipeline[_Work]()
        .then(lambda w: replace(w, fetched="raw"))
        .then(lambda w: replace(w, extracted=len(w.fetched)))
        .then(lambda w: replace(w, extracted=w.extracted * 10))
    )
    assert isinstance(pipeline, Pipeline)
    assert pipeline.run(_Work()) == _Work(fetched="raw", extracted=30)
