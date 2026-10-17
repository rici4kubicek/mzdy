from datetime import date

from payroll.calendar_cz import Shift, due_date, easter_sunday, is_business_day, to_business_day


def test_easter():
    assert easter_sunday(2025) == date(2025, 4, 20)
    assert easter_sunday(2026) == date(2026, 4, 5)
    assert easter_sunday(2027) == date(2027, 3, 28)


def test_holidays_and_weekends():
    assert not is_business_day(date(2026, 4, 3))  # Good Friday
    assert not is_business_day(date(2026, 4, 6))  # Easter Monday
    assert not is_business_day(date(2026, 10, 28))
    assert not is_business_day(date(2026, 10, 17))  # Saturday
    assert is_business_day(date(2026, 10, 16))


def test_shift_directions():
    saturday = date(2026, 10, 17)
    assert to_business_day(saturday, Shift.BACKWARD) == date(2026, 10, 16)
    assert to_business_day(saturday, Shift.FORWARD) == date(2026, 10, 19)


def test_due_date_next_month():
    # October 2026 payroll, paid on 12 Nov 2026 (Thursday).
    assert due_date("2026-10", 12, 1, Shift.BACKWARD) == date(2026, 11, 12)


def test_due_date_skips_christmas_and_wraps_year():
    # 24-26 Dec 2026 are holidays, 27 Dec is Sunday -> back to 23 Dec.
    assert due_date("2026-11", 26, 1, Shift.BACKWARD) == date(2026, 12, 23)
    assert due_date("2026-12", 20, 1, Shift.FORWARD) == date(2027, 1, 20)


def test_due_date_clamps_to_month_end():
    # 31 Feb -> 28 Feb 2027 (Sunday) -> 26 Feb.
    assert due_date("2027-01", 31, 1, Shift.BACKWARD) == date(2027, 2, 26)


def test_easter_gauss_exceptions():
    assert easter_sunday(1981) == date(1981, 4, 19)
    assert easter_sunday(1954) == date(1954, 4, 18)
    assert easter_sunday(2049) == date(2049, 4, 18)
    assert easter_sunday(2076) == date(2076, 4, 19)


def test_payroll_april_due_dates_in_may():
    # April 2026 payroll: 15 May (Fri), 20 May (Wed), 25 May (Mon).
    assert due_date("2026-04", 15, 1, Shift.BACKWARD) == date(2026, 5, 15)
    assert due_date("2026-04", 25, 1, Shift.BACKWARD) == date(2026, 5, 25)
    # 8 May 2027 is a holiday on Saturday; 15 May 2027 is Saturday -> 14 May.
    assert due_date("2027-04", 15, 1, Shift.BACKWARD) == date(2027, 5, 14)
