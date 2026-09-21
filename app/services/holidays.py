"""Iranian public holiday warnings for the calendar (handoff section 11,
phase 2: "هشدار تعطیلات و مناسبت‌ها").

Only the **fixed-date** national holidays are included — the ones pinned to
a specific day of the Jalali (Persian solar) calendar, so they land on the
same Jalali month/day every year: Nowruz (1-4 Farvardin), the Islamic
Republic and Nature Day anniversaries, the death of Imam Khomeini, the 15
Khordad uprising, the Islamic Revolution anniversary and Oil Industry
Nationalization Day. Iran's other public holidays (Eid al-Fitr, Ashura,
Tasua, Arbaeen, Mab'ath, Ghadir, ...) follow the Hijri **lunar** calendar,
which shifts roughly 11 days earlier every Gregorian year and needs
region-specific moon-sighting data to place precisely — data this platform
has no reliable, verifiable source for, so they are left out rather than
guessed at and silently shown as wrong.

The table below maps each fixed holiday's Gregorian date, for
2024-01-01 through 2041-12-31, to its name. It was generated once from
ICU's own Persian calendar (Node's ``Intl.DateTimeFormat`` with
``calendar: "persian"``, the same authority the panel's own Jalali display
already trusts — see ``formatScheduled()`` in the package detail page) — a
verified oracle, not a hand-derived approximation of Jalali/Gregorian
conversion, which is where subtle calendar-math bugs usually creep in.
Extend the table for years beyond 2041 by re-running the same generation
approach (walk each Gregorian year's days through
``Intl.DateTimeFormat("en-US-u-ca-persian")`` and keep the ones matching
the fixed Jalali month/day list above).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Holiday:
    date: date
    name_fa: str
    name_en: str


_RAW: tuple[tuple[str, str, str], ...] = (
    ("2024-03-20", "نوروز", "Nowruz"),
    ("2024-03-21", "نوروز", "Nowruz"),
    ("2024-03-22", "نوروز", "Nowruz"),
    ("2024-03-23", "نوروز", "Nowruz"),
    ("2024-03-31", "روز جمهوری اسلامی", "Islamic Republic Day"),
    ("2024-04-01", "سیزده‌به‌در", "Nature Day"),
    ("2024-06-03", "رحلت امام خمینی", "Death of Imam Khomeini"),
    ("2024-06-04", "قیام ۱۵ خرداد", "15 Khordad Uprising"),
    ("2025-02-10", "پیروزی انقلاب اسلامی", "Anniversary of the Islamic Revolution"),
    ("2025-03-19", "ملی‌شدن صنعت نفت", "Oil Industry Nationalization Day"),
    ("2025-03-21", "نوروز", "Nowruz"),
    ("2025-03-22", "نوروز", "Nowruz"),
    ("2025-03-23", "نوروز", "Nowruz"),
    ("2025-03-24", "نوروز", "Nowruz"),
    ("2025-04-01", "روز جمهوری اسلامی", "Islamic Republic Day"),
    ("2025-04-02", "سیزده‌به‌در", "Nature Day"),
    ("2025-06-04", "رحلت امام خمینی", "Death of Imam Khomeini"),
    ("2025-06-05", "قیام ۱۵ خرداد", "15 Khordad Uprising"),
    ("2026-02-11", "پیروزی انقلاب اسلامی", "Anniversary of the Islamic Revolution"),
    ("2026-03-20", "ملی‌شدن صنعت نفت", "Oil Industry Nationalization Day"),
    ("2026-03-21", "نوروز", "Nowruz"),
    ("2026-03-22", "نوروز", "Nowruz"),
    ("2026-03-23", "نوروز", "Nowruz"),
    ("2026-03-24", "نوروز", "Nowruz"),
    ("2026-04-01", "روز جمهوری اسلامی", "Islamic Republic Day"),
    ("2026-04-02", "سیزده‌به‌در", "Nature Day"),
    ("2026-06-04", "رحلت امام خمینی", "Death of Imam Khomeini"),
    ("2026-06-05", "قیام ۱۵ خرداد", "15 Khordad Uprising"),
    ("2027-02-11", "پیروزی انقلاب اسلامی", "Anniversary of the Islamic Revolution"),
    ("2027-03-20", "ملی‌شدن صنعت نفت", "Oil Industry Nationalization Day"),
    ("2027-03-21", "نوروز", "Nowruz"),
    ("2027-03-22", "نوروز", "Nowruz"),
    ("2027-03-23", "نوروز", "Nowruz"),
    ("2027-03-24", "نوروز", "Nowruz"),
    ("2027-04-01", "روز جمهوری اسلامی", "Islamic Republic Day"),
    ("2027-04-02", "سیزده‌به‌در", "Nature Day"),
    ("2027-06-04", "رحلت امام خمینی", "Death of Imam Khomeini"),
    ("2027-06-05", "قیام ۱۵ خرداد", "15 Khordad Uprising"),
    ("2028-02-11", "پیروزی انقلاب اسلامی", "Anniversary of the Islamic Revolution"),
    ("2028-03-19", "ملی‌شدن صنعت نفت", "Oil Industry Nationalization Day"),
    ("2028-03-20", "نوروز", "Nowruz"),
    ("2028-03-21", "نوروز", "Nowruz"),
    ("2028-03-22", "نوروز", "Nowruz"),
    ("2028-03-23", "نوروز", "Nowruz"),
    ("2028-03-31", "روز جمهوری اسلامی", "Islamic Republic Day"),
    ("2028-04-01", "سیزده‌به‌در", "Nature Day"),
    ("2028-06-03", "رحلت امام خمینی", "Death of Imam Khomeini"),
    ("2028-06-04", "قیام ۱۵ خرداد", "15 Khordad Uprising"),
    ("2029-02-10", "پیروزی انقلاب اسلامی", "Anniversary of the Islamic Revolution"),
    ("2029-03-19", "ملی‌شدن صنعت نفت", "Oil Industry Nationalization Day"),
    ("2029-03-20", "نوروز", "Nowruz"),
    ("2029-03-21", "نوروز", "Nowruz"),
    ("2029-03-22", "نوروز", "Nowruz"),
    ("2029-03-23", "نوروز", "Nowruz"),
    ("2029-03-31", "روز جمهوری اسلامی", "Islamic Republic Day"),
    ("2029-04-01", "سیزده‌به‌در", "Nature Day"),
    ("2029-06-03", "رحلت امام خمینی", "Death of Imam Khomeini"),
    ("2029-06-04", "قیام ۱۵ خرداد", "15 Khordad Uprising"),
    ("2030-02-10", "پیروزی انقلاب اسلامی", "Anniversary of the Islamic Revolution"),
    ("2030-03-19", "ملی‌شدن صنعت نفت", "Oil Industry Nationalization Day"),
    ("2030-03-21", "نوروز", "Nowruz"),
    ("2030-03-22", "نوروز", "Nowruz"),
    ("2030-03-23", "نوروز", "Nowruz"),
    ("2030-03-24", "نوروز", "Nowruz"),
    ("2030-04-01", "روز جمهوری اسلامی", "Islamic Republic Day"),
    ("2030-04-02", "سیزده‌به‌در", "Nature Day"),
    ("2030-06-04", "رحلت امام خمینی", "Death of Imam Khomeini"),
    ("2030-06-05", "قیام ۱۵ خرداد", "15 Khordad Uprising"),
    ("2031-02-11", "پیروزی انقلاب اسلامی", "Anniversary of the Islamic Revolution"),
    ("2031-03-20", "ملی‌شدن صنعت نفت", "Oil Industry Nationalization Day"),
    ("2031-03-21", "نوروز", "Nowruz"),
    ("2031-03-22", "نوروز", "Nowruz"),
    ("2031-03-23", "نوروز", "Nowruz"),
    ("2031-03-24", "نوروز", "Nowruz"),
    ("2031-04-01", "روز جمهوری اسلامی", "Islamic Republic Day"),
    ("2031-04-02", "سیزده‌به‌در", "Nature Day"),
    ("2031-06-04", "رحلت امام خمینی", "Death of Imam Khomeini"),
    ("2031-06-05", "قیام ۱۵ خرداد", "15 Khordad Uprising"),
    ("2032-02-11", "پیروزی انقلاب اسلامی", "Anniversary of the Islamic Revolution"),
    ("2032-03-19", "ملی‌شدن صنعت نفت", "Oil Industry Nationalization Day"),
    ("2032-03-20", "نوروز", "Nowruz"),
    ("2032-03-21", "نوروز", "Nowruz"),
    ("2032-03-22", "نوروز", "Nowruz"),
    ("2032-03-23", "نوروز", "Nowruz"),
    ("2032-03-31", "روز جمهوری اسلامی", "Islamic Republic Day"),
    ("2032-04-01", "سیزده‌به‌در", "Nature Day"),
    ("2032-06-03", "رحلت امام خمینی", "Death of Imam Khomeini"),
    ("2032-06-04", "قیام ۱۵ خرداد", "15 Khordad Uprising"),
    ("2033-02-10", "پیروزی انقلاب اسلامی", "Anniversary of the Islamic Revolution"),
    ("2033-03-19", "ملی‌شدن صنعت نفت", "Oil Industry Nationalization Day"),
    ("2033-03-20", "نوروز", "Nowruz"),
    ("2033-03-21", "نوروز", "Nowruz"),
    ("2033-03-22", "نوروز", "Nowruz"),
    ("2033-03-23", "نوروز", "Nowruz"),
    ("2033-03-31", "روز جمهوری اسلامی", "Islamic Republic Day"),
    ("2033-04-01", "سیزده‌به‌در", "Nature Day"),
    ("2033-06-03", "رحلت امام خمینی", "Death of Imam Khomeini"),
    ("2033-06-04", "قیام ۱۵ خرداد", "15 Khordad Uprising"),
    ("2034-02-10", "پیروزی انقلاب اسلامی", "Anniversary of the Islamic Revolution"),
    ("2034-03-19", "ملی‌شدن صنعت نفت", "Oil Industry Nationalization Day"),
    ("2034-03-21", "نوروز", "Nowruz"),
    ("2034-03-22", "نوروز", "Nowruz"),
    ("2034-03-23", "نوروز", "Nowruz"),
    ("2034-03-24", "نوروز", "Nowruz"),
    ("2034-04-01", "روز جمهوری اسلامی", "Islamic Republic Day"),
    ("2034-04-02", "سیزده‌به‌در", "Nature Day"),
    ("2034-06-04", "رحلت امام خمینی", "Death of Imam Khomeini"),
    ("2034-06-05", "قیام ۱۵ خرداد", "15 Khordad Uprising"),
    ("2035-02-11", "پیروزی انقلاب اسلامی", "Anniversary of the Islamic Revolution"),
    ("2035-03-20", "ملی‌شدن صنعت نفت", "Oil Industry Nationalization Day"),
    ("2035-03-21", "نوروز", "Nowruz"),
    ("2035-03-22", "نوروز", "Nowruz"),
    ("2035-03-23", "نوروز", "Nowruz"),
    ("2035-03-24", "نوروز", "Nowruz"),
    ("2035-04-01", "روز جمهوری اسلامی", "Islamic Republic Day"),
    ("2035-04-02", "سیزده‌به‌در", "Nature Day"),
    ("2035-06-04", "رحلت امام خمینی", "Death of Imam Khomeini"),
    ("2035-06-05", "قیام ۱۵ خرداد", "15 Khordad Uprising"),
    ("2036-02-11", "پیروزی انقلاب اسلامی", "Anniversary of the Islamic Revolution"),
    ("2036-03-19", "ملی‌شدن صنعت نفت", "Oil Industry Nationalization Day"),
    ("2036-03-20", "نوروز", "Nowruz"),
    ("2036-03-21", "نوروز", "Nowruz"),
    ("2036-03-22", "نوروز", "Nowruz"),
    ("2036-03-23", "نوروز", "Nowruz"),
    ("2036-03-31", "روز جمهوری اسلامی", "Islamic Republic Day"),
    ("2036-04-01", "سیزده‌به‌در", "Nature Day"),
    ("2036-06-03", "رحلت امام خمینی", "Death of Imam Khomeini"),
    ("2036-06-04", "قیام ۱۵ خرداد", "15 Khordad Uprising"),
    ("2037-02-10", "پیروزی انقلاب اسلامی", "Anniversary of the Islamic Revolution"),
    ("2037-03-19", "ملی‌شدن صنعت نفت", "Oil Industry Nationalization Day"),
    ("2037-03-20", "نوروز", "Nowruz"),
    ("2037-03-21", "نوروز", "Nowruz"),
    ("2037-03-22", "نوروز", "Nowruz"),
    ("2037-03-23", "نوروز", "Nowruz"),
    ("2037-03-31", "روز جمهوری اسلامی", "Islamic Republic Day"),
    ("2037-04-01", "سیزده‌به‌در", "Nature Day"),
    ("2037-06-03", "رحلت امام خمینی", "Death of Imam Khomeini"),
    ("2037-06-04", "قیام ۱۵ خرداد", "15 Khordad Uprising"),
    ("2038-02-10", "پیروزی انقلاب اسلامی", "Anniversary of the Islamic Revolution"),
    ("2038-03-19", "ملی‌شدن صنعت نفت", "Oil Industry Nationalization Day"),
    ("2038-03-21", "نوروز", "Nowruz"),
    ("2038-03-22", "نوروز", "Nowruz"),
    ("2038-03-23", "نوروز", "Nowruz"),
    ("2038-03-24", "نوروز", "Nowruz"),
    ("2038-04-01", "روز جمهوری اسلامی", "Islamic Republic Day"),
    ("2038-04-02", "سیزده‌به‌در", "Nature Day"),
    ("2038-06-04", "رحلت امام خمینی", "Death of Imam Khomeini"),
    ("2038-06-05", "قیام ۱۵ خرداد", "15 Khordad Uprising"),
    ("2039-02-11", "پیروزی انقلاب اسلامی", "Anniversary of the Islamic Revolution"),
    ("2039-03-20", "ملی‌شدن صنعت نفت", "Oil Industry Nationalization Day"),
    ("2039-03-21", "نوروز", "Nowruz"),
    ("2039-03-22", "نوروز", "Nowruz"),
    ("2039-03-23", "نوروز", "Nowruz"),
    ("2039-03-24", "نوروز", "Nowruz"),
    ("2039-04-01", "روز جمهوری اسلامی", "Islamic Republic Day"),
    ("2039-04-02", "سیزده‌به‌در", "Nature Day"),
    ("2039-06-04", "رحلت امام خمینی", "Death of Imam Khomeini"),
    ("2039-06-05", "قیام ۱۵ خرداد", "15 Khordad Uprising"),
    ("2040-02-11", "پیروزی انقلاب اسلامی", "Anniversary of the Islamic Revolution"),
    ("2040-03-19", "ملی‌شدن صنعت نفت", "Oil Industry Nationalization Day"),
    ("2040-03-20", "نوروز", "Nowruz"),
    ("2040-03-21", "نوروز", "Nowruz"),
    ("2040-03-22", "نوروز", "Nowruz"),
    ("2040-03-23", "نوروز", "Nowruz"),
    ("2040-03-31", "روز جمهوری اسلامی", "Islamic Republic Day"),
    ("2040-04-01", "سیزده‌به‌در", "Nature Day"),
    ("2040-06-03", "رحلت امام خمینی", "Death of Imam Khomeini"),
    ("2040-06-04", "قیام ۱۵ خرداد", "15 Khordad Uprising"),
    ("2041-02-10", "پیروزی انقلاب اسلامی", "Anniversary of the Islamic Revolution"),
    ("2041-03-19", "ملی‌شدن صنعت نفت", "Oil Industry Nationalization Day"),
    ("2041-03-20", "نوروز", "Nowruz"),
    ("2041-03-21", "نوروز", "Nowruz"),
    ("2041-03-22", "نوروز", "Nowruz"),
    ("2041-03-23", "نوروز", "Nowruz"),
    ("2041-03-31", "روز جمهوری اسلامی", "Islamic Republic Day"),
    ("2041-04-01", "سیزده‌به‌در", "Nature Day"),
    ("2041-06-03", "رحلت امام خمینی", "Death of Imam Khomeini"),
    ("2041-06-04", "قیام ۱۵ خرداد", "15 Khordad Uprising"),
)

HOLIDAYS: tuple[Holiday, ...] = tuple(
    Holiday(date=date.fromisoformat(iso), name_fa=name_fa, name_en=name_en)
    for iso, name_fa, name_en in _RAW
)
_BY_DATE: dict[date, Holiday] = {h.date: h for h in HOLIDAYS}


def holidays_in_range(start: date, end: date) -> list[Holiday]:
    """Every known holiday with ``start <= date <= end``, in order.
    ``start``/``end`` are inclusive Gregorian dates."""
    return sorted((h for h in HOLIDAYS if start <= h.date <= end), key=lambda h: h.date)


def holiday_on(day: date) -> Holiday | None:
    return _BY_DATE.get(day)


__all__ = ["Holiday", "HOLIDAYS", "holiday_on", "holidays_in_range"]
