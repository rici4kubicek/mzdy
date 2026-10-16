"""Domain models shared by the batch logic, the API and storage."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from payroll.accounts import AccountNumber, BankAccount
from payroll.calendar_cz import Shift


class Group(StrEnum):
    SALARIES = "salaries"
    DEDUCTIONS = "deductions"


@dataclass(frozen=True)
class EmployeeAccount:
    account: BankAccount
    valid_from: date
    valid_to: date | None = None

    def is_valid_on(self, day: date) -> bool:
        return self.valid_from <= day and (self.valid_to is None or day <= self.valid_to)


@dataclass(frozen=True)
class Employee:
    id: str
    number: str
    first_name: str
    last_name: str
    email: str = ""
    active: bool = True
    accounts: tuple[EmployeeAccount, ...] = ()

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


@dataclass(frozen=True)
class KnownAccount:
    """A non-employee payee, e.g. a health insurer or the tax office."""

    account: BankAccount
    label: str


@dataclass(frozen=True)
class DueDateRule:
    day: int
    month_offset: int = 1
    shift: Shift = Shift.BACKWARD


@dataclass(frozen=True)
class Settings:
    payer_account: AccountNumber | None = None
    deduction_accounts: tuple[KnownAccount, ...] = ()
    due_dates: dict[Group, DueDateRule] = field(
        default_factory=lambda: {
            Group.SALARIES: DueDateRule(day=12),
            Group.DEDUCTIONS: DueDateRule(day=20),
        }
    )
    account_change_warning_days: int = 30
    salary_change_warning_pct: int = 30
    payslip_number_pattern: str = r"osobni\s+cislo\W{0,5}(\d+)"
    email_sender: str = ""
    email_subject: str = "Výplatní páska {period}"
    email_body: str = (
        "Dobrý den {name},\n\nv příloze zasíláme výplatní pásku za období {period}.\n\n"
        "S pozdravem\nmzdová účtárna"
    )
