from datetime import date

import pytest

from payroll.accounts import AccountNumber, BankAccount
from payroll.models import Employee, EmployeeAccount, KnownAccount, PensionAccount, Settings

COMPANY = AccountNumber("", "1000000005")
NOVAK = BankAccount.parse("1000000013/0800")
SVOBODOVA = BankAccount.parse("19-2000145399/0100")
VZP = BankAccount.parse("1000000021/0710")
CSSZ = BankAccount.parse("1000000048/0710")
TAX_OFFICE = BankAccount.parse("1000000056/0710")
PENSION_FUND = BankAccount.parse("27-1840220297/0100")

SAMPLE_ABO = (
    "UHL1151026Firma s.r.o.        0000123456001999000000000000\r\n"
    "1 1501 001000 0300\r\n"
    "2 000000-1000000005 00000007800000 121126\r\n"
    "000000-1000000013 000003200000 0000001001 08000000 0000000000 AV:Mzda 10/2026\r\n"
    "000019-2000145399 000002500000 0000001002 01000000 0000000000 AV:Mzda 10/2026\r\n"
    "000000-1000000021 000000850000 0000012345 07100558 0000000000 AV:Zdravotni pojisteni\r\n"
    "000000-1000000048 000000700000 0000098765 07100558 0000000000 AV:Socialni pojisteni\r\n"
    "000000-1000000056 000000300000 0000011111 07101148 0000000000 AV:Dan ze zavisle cinnosti\r\n"
    "000027-1840220297 000000100000 0900011111 01000000 0000000000 AV:Penzijni pripojisteni\r\n"
    # The second contract number is in the specific symbol field, not the VS.
    "000027-1840220297 000000150000 0000000000 01000000 0900022222 AV:Penzijni pripojisteni\r\n"
    "3 +\r\n"
    "5 +\r\n"
)


@pytest.fixture
def sample_abo() -> str:
    return SAMPLE_ABO


@pytest.fixture
def employees() -> list[Employee]:
    return [
        Employee(
            id="e1",
            number="1001",
            first_name="Jan",
            last_name="Novák",
            email="jan.novak@example.com",
            accounts=(EmployeeAccount(NOVAK, date(2024, 1, 1)),),
        ),
        Employee(
            id="e2",
            number="1002",
            first_name="Eva",
            last_name="Svobodová",
            email="eva.svobodova@example.com",
            accounts=(EmployeeAccount(SVOBODOVA, date(2024, 1, 1)),),
        ),
    ]


@pytest.fixture
def settings() -> Settings:
    return Settings(
        payer_account=COMPANY,
        deduction_accounts=(
            KnownAccount(VZP, "VZP"),
            KnownAccount(CSSZ, "ČSSZ"),
            KnownAccount(TAX_OFFICE, "Finanční úřad"),
        ),
        pension_accounts=(
            PensionAccount(PENSION_FUND, "Dvořák", "900011111"),
            PensionAccount(PENSION_FUND, "Černý", "900022222"),
        ),
    )
