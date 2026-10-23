"""The agenda date grammar: year inference + due-date precedence."""

from datetime import date

from school.driver_web.dates import (
    first_md,
    infer_year,
    resolve_due,
    weekday_date,
)

REFERENCE = date(2026, 10, 22)


def test_infer_year_picks_closest_year() -> None:
    assert infer_year(10, 19, reference=REFERENCE) == date(2026, 10, 19)
    # A January date seen from December belongs to next year.
    assert infer_year(1, 12, reference=date(2026, 12, 20)) \
        == date(2027, 1, 12)
    # A December date seen from January belongs to last year.
    assert infer_year(12, 15, reference=date(2027, 1, 10)) \
        == date(2026, 12, 15)
    # Closest wins even across the year boundary: from May 2027,
    # August 2027 (future) is nearer than August 2026.
    assert infer_year(8, 24, reference=date(2027, 5, 1)) == date(2027, 8, 24)


def test_first_md() -> None:
    assert first_md("Page 8 (due 10/21)", reference=REFERENCE) \
        == date(2026, 10, 21)
    assert first_md("no dates here", reference=REFERENCE) is None


def test_resolve_due_precedence() -> None:
    default = date(2026, 10, 16)
    # FD (final deadline) overrides the plain date.
    assert resolve_due(
        "Quiz 10/16, Retake FD 10/26", default=default, reference=REFERENCE
    ) == date(2026, 10, 26)
    # An explicit "due" date beats an incidental date.
    assert resolve_due(
        "From 10/19: workbook due 10/21", default=default,
        reference=REFERENCE,
    ) == date(2026, 10, 21)
    # Any remaining M/D wins over the default.
    assert resolve_due(
        "Workbook check 10/16", default=date(2026, 10, 14),
        reference=REFERENCE,
    ) == date(2026, 10, 16)
    # No date at all -> the default (the cell's own day).
    assert resolve_due(
        "Workbook due for completion check", default=default,
        reference=REFERENCE,
    ) == default


def test_weekday_date() -> None:
    monday = date(2026, 10, 19)
    assert weekday_date("Friday", week_monday=monday) == date(2026, 10, 23)
    assert weekday_date("Monday", week_monday=monday) == monday
    assert weekday_date("Someday", week_monday=monday) is None
