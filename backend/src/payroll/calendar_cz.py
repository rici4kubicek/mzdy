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
    """Easter Sunday in the Gregorian calendar using Gauss's algorithm."""
    a = year % 19
    b = year % 4
    c = year % 7
    k = year // 100
    p = (13 + 8 * k) // 25
    q = k // 4
    m = (15 - p + k - q) % 30
    n = (4 + k - q) % 7
    d = (19 * a + m) % 30
    e = (2 * b + 4 * c + 6 * d + n) % 7
    # Gauss's two exceptions keep Easter on or before 25 April.
    if d == 29 and e == 6:
        return date(year, 4, 19)
    if d == 28 and e == 6 and (11 * m + 11) % 30 < 19:
        return date(year, 4, 18)
    return date(year, 3, 22) + timedelta(days=d + e)


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
