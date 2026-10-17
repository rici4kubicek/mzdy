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
    payment_comment,
    period_code,
    split,
)
from payroll.models import EmployeeAccount, Group, Settings
from tests.conftest import NOVAK

TODAY = date(2026, 11, 2)
PERIOD = "2026-10"


def run(sample_abo, employees, settings, **kwargs):
    return classify(abo.parse(sample_abo), employees, settings, TODAY, PERIOD, **kwargs)


def codes(classification, severity=None):
    return [i.code for i in classification.issues if severity in (None, i.severity)]


def test_classifies_three_groups(sample_abo, employees, settings):
    c = run(sample_abo, employees, settings)
    assert [i.group for i in c.items] == (
        [Group.SALARIES] * 2 + [Group.DEDUCTIONS] * 3 + [Group.PENSION] * 2
    )
    assert [i.employee_id for i in c.items[:2]] == ["e1", "e2"]
    assert [i.label for i in c.items] == [
        "Jan Novák",
        "Eva Svobodová",
        "VZP",
        "ČSSZ",
        "Finanční úřad",
        "Dvořák",
        "Černý",
    ]
    assert c.issues == []
    assert c.totals() == {
        Group.SALARIES: (2, 5_700_000),
        Group.DEDUCTIONS: (3, 1_850_000),
        Group.PENSION: (2, 250_000),
    }


def test_comments(sample_abo, employees, settings):
    c = run(sample_abo, employees, settings)
    assert [i.comment for i in c.items] == [
        "AV: Mzda 1026 Jan Novák NP: Mzda 1026 Jan Novák",
        "AV: Mzda 1026 Eva Svobodová NP: Mzda 1026 Eva Svobodová",
        "NP: VZP",
        "NP: ČSSZ",
        "NP: Finanční úřad",
        "NP: Dvořák důch. připojištění",
        "NP: Černý důch. připojištění",
    ]


def test_period_code_and_manual_comment():
    assert period_code("2026-04") == "0426"
    assert payment_comment(Group.DEDUCTIONS, PERIOD, "", "Exekuce 123") == "NP: Exekuce 123"
    assert payment_comment(Group.DEDUCTIONS, PERIOD, "", "") == ""
    assert payment_comment(None, PERIOD, "", "x") == ""


def test_shared_pension_account_without_matching_vs(sample_abo, employees, settings):
    text = sample_abo.replace("0900011111", "0900099999")
    c = run(text, employees, settings)
    assert codes(c, "error") == ["pension_vs_not_found"]
    assert c.items[5].group is None


def test_pension_rule_without_vs_matches_any_payment(sample_abo, employees, settings):
    rule = replace(settings.pension_accounts[0], vs="")
    settings = replace(settings, pension_accounts=(rule,))
    c = run(sample_abo, employees, settings)
    assert [i.label for i in c.items[5:]] == ["Dvořák", "Dvořák"]
    assert not c.has_errors


def test_unknown_account_is_blocking_until_assigned(sample_abo, employees, settings):
    settings = replace(settings, deduction_accounts=settings.deduction_accounts[:2])
    c = run(sample_abo, employees, settings)
    assert c.has_errors
    assert codes(c) == ["unassigned"]
    assert c.items[4].group is None

    c = run(sample_abo, employees, settings, overrides={4: Group.DEDUCTIONS})
    assert not c.has_errors
    assert c.items[4].group is Group.DEDUCTIONS
    assert c.items[4].overridden
    assert c.items[4].comment == "NP: Dan ze zavisle cinnosti"


def test_unknown_account_cannot_be_forced_into_salaries(sample_abo, employees, settings):
    settings = replace(settings, deduction_accounts=())
    c = run(sample_abo, employees, settings, overrides={2: Group.SALARIES})
    assert c.items[2].group is None
    assert "salary_to_unknown_account" in codes(c, "error")
    # The rejected override is reported once, not again as "unassigned".
    assert 2 not in [i.item for i in c.issues if i.code == "unassigned"]


def test_spoofed_salary_account(sample_abo, employees, settings):
    """A salary sent to an account nobody registered must not slip through."""
    text = sample_abo.replace("000000-1000000013 000003200000", "000000-1000000064 000003200000")
    c = run(text, employees, settings)
    assert "unassigned" in codes(c, "error")
    assert "missing_salary" in codes(c, "warning")


def test_expired_employee_account(sample_abo, employees, settings):
    employees[0] = replace(
        employees[0],
        accounts=(EmployeeAccount(NOVAK, date(2024, 1, 1), date(2026, 9, 30)),),
    )
    c = run(sample_abo, employees, settings)
    assert codes(c, "error") == ["expired_account"]
    assert c.items[0].group is None


def test_recent_account_change_warning(sample_abo, employees, settings):
    employees[0] = replace(employees[0], accounts=(EmployeeAccount(NOVAK, date(2026, 10, 20)),))
    c = run(sample_abo, employees, settings)
    assert codes(c) == ["recent_account_change"]
    assert c.issues[0].params == {"name": "Jan Novák", "date": "2026-10-20"}
    assert not c.has_errors


def test_inactive_employee_is_not_matched(sample_abo, employees, settings):
    employees[1] = replace(employees[1], active=False)
    c = run(sample_abo, employees, settings)
    assert codes(c, "error") == ["unassigned"]


def test_payer_mismatch(sample_abo, employees, settings):
    settings = replace(settings, payer_account=AccountNumber("", "1000000072"))
    c = run(sample_abo, employees, settings)
    assert codes(c, "error") == ["payer_mismatch"] * 7


def test_group_total_mismatch(sample_abo, employees, settings):
    text = sample_abo.replace("00000007800000", "00000007800001")
    c = run(text, employees, settings)
    assert codes(c, "error") == ["group_total_mismatch"]


def test_invalid_account(sample_abo, employees, settings):
    text = sample_abo.replace(
        "000000-1000000056 000000300000 0000011111 0710",
        "000000-1000000057 000000300000 0000011111 9999",
    )
    c = run(text, employees, settings)
    assert "invalid_account" in codes(c, "error")


def test_salary_change_warning(sample_abo, employees, settings):
    c = run(
        sample_abo,
        employees,
        settings,
        previous_salaries={"e1": 2_000_000, "e2": 2_400_000},
    )
    assert codes(c) == ["salary_change"]
    assert c.issues[0].params["name"] == "Jan Novák"


def test_default_due_dates(settings):
    # 15 Nov 2026 is a Sunday -> Friday 13 Nov.
    assert default_due_dates(PERIOD, settings) == {
        Group.SALARIES: date(2026, 11, 13),
        Group.DEDUCTIONS: date(2026, 11, 20),
        Group.PENSION: date(2026, 11, 25),
    }


def test_split_produces_one_file_per_group(sample_abo, employees, settings):
    source = abo.parse(sample_abo)
    c = classify(source, employees, settings, TODAY, PERIOD)
    dues = default_due_dates(PERIOD, settings)
    outputs = split(source, c, dues, created=TODAY)

    assert list(outputs) == [Group.SALARIES, Group.DEDUCTIONS, Group.PENSION]
    parsed = {g: abo.parse(data) for g, data in outputs.items()}
    assert sum(f.total for f in parsed.values()) == source.total
    for group, f in parsed.items():
        assert f.groups[0].due_date == dues[group]
        assert f.groups[0].payer == AccountNumber("", "1000000005")
        assert f.header.created == TODAY
        assert f.file_number == source.file_number

    salaries = outputs[Group.SALARIES].decode("ascii").split("\r\n")
    assert salaries[:3] == [
        "UHL1021126Firma s.r.o.        0000123456001999000000000000",
        "1 1501 001000 0300",
        "2 000000-1000000005 00000005700000 131126",
    ]
    assert salaries[3] == (
        "000000-1000000013 000003200000 0000001001 08000000 0000000000 "
        "AV: Mzda 1026 Jan Novak NP: Mzda 1026 Jan Novak"
    )
    assert salaries[-3:] == ["3 +", "5 +", ""]
    pension = outputs[Group.PENSION].decode("ascii")
    assert "0900022222 NP: Cerny duch. pripojisteni\r\n" in pension
    assert [i.message for i in parsed[Group.DEDUCTIONS].items] == [
        "NP: VZP",
        "NP: CSSZ",
        "NP: Financni urad",
    ]


def test_split_skips_empty_groups(sample_abo, employees, settings):
    source = abo.parse(sample_abo)
    overrides = {i: Group.DEDUCTIONS for i in range(7)}
    c = classify(source, employees, settings, TODAY, PERIOD, overrides=overrides)
    outputs = split(source, c, default_due_dates(PERIOD, settings), created=TODAY)
    assert list(outputs) == [Group.DEDUCTIONS]
    assert len(abo.parse(outputs[Group.DEDUCTIONS]).items) == 7


def test_split_refuses_blocking_errors(sample_abo, employees):
    source = abo.parse(sample_abo)
    c = classify(source, employees, Settings(), TODAY, PERIOD)
    with pytest.raises(SplitError):
        split(source, c, {}, created=TODAY)


def test_output_filename():
    assert output_filename(Group.SALARIES, PERIOD, 15) == "15_mzdy_2026-10.kpc"
    assert output_filename(Group.PENSION, PERIOD, 25) == "25_duchodove-pripojisteni_2026-10.kpc"
