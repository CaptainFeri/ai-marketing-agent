"""Fixed-date Iranian public holidays (handoff section 11: "هشدار تعطیلات
و مناسبت‌ها"). The table itself was generated from ICU's own Persian
calendar (see app/services/holidays.py's module docstring); these tests
check a handful of entries and the range/lookup helpers, not the whole
table.
"""

from __future__ import annotations

from datetime import date

from app.services.holidays import HOLIDAYS, holiday_on, holidays_in_range


def test_nowruz_2026_starts_on_the_right_gregorian_date() -> None:
    holiday = holiday_on(date(2026, 3, 21))
    assert holiday is not None
    assert holiday.name_en == "Nowruz"


def test_a_non_holiday_date_is_none() -> None:
    assert holiday_on(date(2026, 5, 5)) is None


def test_holidays_in_range_is_inclusive_and_sorted() -> None:
    # 2026-03-20 is 29 Esfand 1404 (Oil Industry Nationalization Day) --
    # the day right before Nowruz, both in this window.
    results = holidays_in_range(date(2026, 3, 20), date(2026, 3, 24))
    assert [h.date for h in results] == [
        date(2026, 3, 20),
        date(2026, 3, 21),
        date(2026, 3, 22),
        date(2026, 3, 23),
        date(2026, 3, 24),
    ]


def test_holidays_in_range_excludes_dates_outside_it() -> None:
    results = holidays_in_range(date(2026, 3, 22), date(2026, 3, 22))
    assert [h.date for h in results] == [date(2026, 3, 22)]


def test_every_holiday_has_both_names() -> None:
    for holiday in HOLIDAYS:
        assert holiday.name_fa
        assert holiday.name_en


def test_the_table_covers_at_least_the_current_decade() -> None:
    years = {h.date.year for h in HOLIDAYS}
    assert {2026, 2027, 2028, 2029, 2030} <= years
