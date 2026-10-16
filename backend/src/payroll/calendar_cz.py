"""Czech business days and payment due dates."""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from enum import StrEnum
from functools import cache


class Shift(StrEnum):
    """Which way to move a due date that falls on a non-business day."""

    BACKWARD = "backward"
    FORWARD = "forward"


def easter_sunday(year: int) -> date:
    # Anonymous Gregorian algorithm (Meeus/Jones/Butcher).
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return date(year, month, day + 1)


@cache
def public_holidays(year: int) -> frozenset[date]:
    easter = easter_sunday(year)
    fixed = [
        (1, 1),  # Restoration Day of the Independent Czech State, New Year's Day
        (5, 1),  # Labour Day
        (5, 8),  # Liberation Day
        (7, 5),  # Saints Cyril and Methodius Day
        (7, 6),  # Jan Hus Day
        (9, 28),  # Czech Statehood Day
        (10, 28),  # Independent Czechoslovak State Day
        (11, 17),  # Struggle for Freedom and Democracy Day
        (12, 24),  # Christmas Eve
        (12, 25),  # Christmas Day
        (12, 26),  # St. Stephen's Day
    ]
    days = {date(year, month, day) for month, day in fixed}
    days.add(easter - timedelta(days=2))  # Good Friday
    days.add(easter + timedelta(days=1))  # Easter Monday
    return frozenset(days)


def is_business_day(day: date) -> bool:
    return day.weekday() < 5 and day not in public_holidays(day.year)


def to_business_day(day: date, shift: Shift) -> date:
    step = timedelta(days=-1 if shift is Shift.BACKWARD else 1)
    while not is_business_day(day):
        day += step
    return day


def due_date(period: str, day: int, month_offset: int, shift: Shift) -> date:
    """Due date for a payroll period.

    ``period`` is ``YYYY-MM``. Payroll for October is usually paid in November, so
    ``month_offset=1`` moves to the following month. ``day`` larger than the month
    length means the last day of the month.
    """
    year, month = (int(part) for part in period.split("-"))
    month += month_offset
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return to_business_day(date(year, month, min(day, last_day)), shift)
