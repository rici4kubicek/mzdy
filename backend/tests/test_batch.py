from dataclasses import replace
from datetime import date

import pytest

from payroll import abo
from payroll.accounts import AccountNumber
from payroll.batch import (
    SplitError,
    classify,
    default_due_dates,
    output_filename,
    split,
)
from payroll.models import EmployeeAccount, Group, Settings
from tests.conftest import NOVAK

TODAY = date(2026, 11, 2)


def codes(classification, severity=None):
    return [i.code for i in classification.issues if severity in (None, i.severity)]


def test_classifies_salaries_and_deductions(sample_abo, employees, settings):
    c = classify(abo.parse(sample_abo), employees, settings, TODAY)
    assert [i.group for i in c.items] == [Group.SALARIES] * 2 + [Group.DEDUCTIONS] * 3
    assert [i.employee_id for i in c.items[:2]] == ["e1", "e2"]
    assert [i.label for i in c.items] == [
        "Jan Novák",
        "Eva Svobodová",
        "VZP",
        "ČSSZ",
        "Finanční úřad",
    ]
    assert c.issues == []
    assert c.totals() == {Group.SALARIES: (2, 5_700_000), Group.DEDUCTIONS: (3, 1_850_000)}


def test_unknown_account_is_blocking_until_assigned(sample_abo, employees, settings):
    settings = replace(settings, deduction_accounts=settings.deduction_accounts[:2])
    source = abo.parse(sample_abo)
    c = classify(source, employees, settings, TODAY)
    assert c.has_errors
    assert codes(c) == ["unassigned"]
    assert c.items[4].group is None

    c = classify(source, employees, settings, TODAY, overrides={4: Group.DEDUCTIONS})
    assert not c.has_errors
    assert c.items[4].group is Group.DEDUCTIONS
    assert c.items[4].overridden


def test_unknown_account_cannot_be_forced_into_salaries(sample_abo, employees, settings):
    settings = replace(settings, deduction_accounts=())
    c = classify(abo.parse(sample_abo), employees, settings, TODAY, overrides={2: Group.SALARIES})
    assert c.items[2].group is None
    assert "salary_to_unknown_account" in codes(c, "error")
    # The rejected override is reported once, not again as "unassigned".
    assert 2 not in [i.item for i in c.issues if i.code == "unassigned"]


def test_spoofed_salary_account(sample_abo, employees, settings):
    """A salary sent to an account nobody registered must not slip through."""
    text = sample_abo.replace("000000-1000000013 000003200000", "000000-1000000064 000003200000")
    c = classify(abo.parse(text), employees, settings, TODAY)
    assert "unassigned" in codes(c, "error")
    assert "missing_salary" in codes(c, "warning")


def test_expired_employee_account(sample_abo, employees, settings):
    employees[0] = replace(
        employees[0],
        accounts=(EmployeeAccount(NOVAK, date(2024, 1, 1), date(2026, 9, 30)),),
    )
    c = classify(abo.parse(sample_abo), employees, settings, TODAY)
    assert codes(c, "error") == ["expired_account"]
    assert c.items[0].group is None


def test_recent_account_change_warning(sample_abo, employees, settings):
    employees[0] = replace(employees[0], accounts=(EmployeeAccount(NOVAK, date(2026, 10, 20)),))
    c = classify(abo.parse(sample_abo), employees, settings, TODAY)
    assert codes(c) == ["recent_account_change"]
    assert c.issues[0].params == {"name": "Jan Novák", "date": "2026-10-20"}
    assert not c.has_errors


def test_inactive_employee_is_not_matched(sample_abo, employees, settings):
    employees[1] = replace(employees[1], active=False)
    c = classify(abo.parse(sample_abo), employees, settings, TODAY)
    assert codes(c, "error") == ["unassigned"]


def test_payer_mismatch(sample_abo, employees, settings):
    settings = replace(settings, payer_account=AccountNumber("", "1000000072"))
    c = classify(abo.parse(sample_abo), employees, settings, TODAY)
    assert codes(c, "error") == ["payer_mismatch"] * 5


def test_group_total_mismatch(sample_abo, employees, settings):
    text = sample_abo.replace("00000007550000", "00000007550001")
    c = classify(abo.parse(text), employees, settings, TODAY)
    assert codes(c, "error") == ["group_total_mismatch"]


def test_invalid_account_and_unknown_bank(sample_abo, employees, settings):
    text = sample_abo.replace(
        "000000-1000000056 000000300000 0000011111 0710",
        "000000-1000000057 000000300000 0000011111 9999",
    )
    c = classify(abo.parse(text), employees, settings, TODAY)
    assert "invalid_account" in codes(c, "error")


def test_salary_change_warning(sample_abo, employees, settings):
    c = classify(
        abo.parse(sample_abo),
        employees,
        settings,
        TODAY,
        previous_salaries={"e1": 2_000_000, "e2": 2_400_000},
    )
    assert codes(c) == ["salary_change"]
    assert c.issues[0].params["name"] == "Jan Novák"


def test_default_due_dates(settings):
    assert default_due_dates("2026-10", settings) == {
        Group.SALARIES: date(2026, 11, 12),
        Group.DEDUCTIONS: date(2026, 11, 20),
    }


def test_split_produces_one_file_per_group(sample_abo, employees, settings):
    source = abo.parse(sample_abo)
    c = classify(source, employees, settings, TODAY)
    dues = {Group.SALARIES: date(2026, 11, 12), Group.DEDUCTIONS: date(2026, 11, 20)}
    outputs = split(source, c, dues, created=TODAY)

    assert set(outputs) == {Group.SALARIES, Group.DEDUCTIONS}
    salaries = abo.parse(abo.serialize(outputs[Group.SALARIES]))
    deductions = abo.parse(abo.serialize(outputs[Group.DEDUCTIONS]))
    assert salaries.groups[0].due_date == date(2026, 11, 12)
    assert deductions.groups[0].due_date == date(2026, 11, 20)
    assert salaries.total + deductions.total == source.total
    assert [i.vs for i in salaries.items] == ["1001", "1002"]
    assert salaries.groups[0].payer == AccountNumber("", "1000000005")
    assert salaries.header.created == TODAY
    assert salaries.file_number != deductions.file_number


def test_split_skips_empty_groups(sample_abo, employees, settings):
    source = abo.parse(sample_abo)
    overrides = {i: Group.DEDUCTIONS for i in range(5)}
    c = classify(source, employees, settings, TODAY, overrides=overrides)
    outputs = split(source, c, default_due_dates("2026-10", settings), created=TODAY)
    assert list(outputs) == [Group.DEDUCTIONS]
    assert len(outputs[Group.DEDUCTIONS].items) == 5


def test_split_refuses_blocking_errors(sample_abo, employees):
    source = abo.parse(sample_abo)
    c = classify(source, employees, Settings(), TODAY)
    with pytest.raises(SplitError):
        split(source, c, {}, created=TODAY)


def test_output_filename():
    assert (
        output_filename(Group.SALARIES, "2026-10", date(2026, 11, 12))
        == "salaries_2026-10_due-2026-11-12.kpc"
    )
