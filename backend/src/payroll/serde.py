"""Conversion of domain objects to and from JSON-compatible dicts."""

from __future__ import annotations

from datetime import date
from typing import Any

from payroll.accounts import AccountNumber, BankAccount
from payroll.batch import Classification
from payroll.calendar_cz import Shift
from payroll.models import (
    DueDateRule,
    Employee,
    EmployeeAccount,
    Group,
    KnownAccount,
    Settings,
)


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def employee_to_dict(e: Employee) -> dict[str, Any]:
    return {
        "id": e.id,
        "number": e.number,
        "first_name": e.first_name,
        "last_name": e.last_name,
        "email": e.email,
        "active": e.active,
        "accounts": [
            {
                "account": str(a.account),
                "valid_from": a.valid_from.isoformat(),
                "valid_to": a.valid_to.isoformat() if a.valid_to else None,
            }
            for a in e.accounts
        ],
    }


def employee_from_dict(d: dict[str, Any]) -> Employee:
    return Employee(
        id=d["id"],
        number=str(d["number"]).strip(),
        first_name=d.get("first_name", "").strip(),
        last_name=d.get("last_name", "").strip(),
        email=d.get("email", "").strip(),
        active=bool(d.get("active", True)),
        accounts=tuple(
            EmployeeAccount(
                account=BankAccount.parse(a["account"]),
                valid_from=date.fromisoformat(a["valid_from"]),
                valid_to=_date(a.get("valid_to")),
            )
            for a in d.get("accounts", [])
        ),
    )


def settings_to_dict(s: Settings) -> dict[str, Any]:
    return {
        "payer_account": str(s.payer_account) if s.payer_account else "",
        "deduction_accounts": [
            {"account": str(k.account), "label": k.label} for k in s.deduction_accounts
        ],
        "due_dates": {
            group.value: {
                "day": rule.day,
                "month_offset": rule.month_offset,
                "shift": rule.shift.value,
            }
            for group, rule in s.due_dates.items()
        },
        "account_change_warning_days": s.account_change_warning_days,
        "salary_change_warning_pct": s.salary_change_warning_pct,
        "payslip_number_pattern": s.payslip_number_pattern,
        "email_sender": s.email_sender,
        "email_subject": s.email_subject,
        "email_body": s.email_body,
    }


def settings_from_dict(d: dict[str, Any]) -> Settings:
    defaults = Settings()
    due_dates = dict(defaults.due_dates)
    for key, rule in (d.get("due_dates") or {}).items():
        due_dates[Group(key)] = DueDateRule(
            day=int(rule["day"]),
            month_offset=int(rule.get("month_offset", 1)),
            shift=Shift(rule.get("shift", Shift.BACKWARD)),
        )
    return Settings(
        payer_account=AccountNumber.parse(d["payer_account"]) if d.get("payer_account") else None,
        deduction_accounts=tuple(
            KnownAccount(BankAccount.parse(k["account"]), k.get("label", "").strip())
            for k in d.get("deduction_accounts", [])
        ),
        due_dates=due_dates,
        account_change_warning_days=int(
            d.get("account_change_warning_days", defaults.account_change_warning_days)
        ),
        salary_change_warning_pct=int(
            d.get("salary_change_warning_pct", defaults.salary_change_warning_pct)
        ),
        payslip_number_pattern=d.get("payslip_number_pattern") or defaults.payslip_number_pattern,
        email_sender=d.get("email_sender", defaults.email_sender).strip(),
        email_subject=d.get("email_subject") or defaults.email_subject,
        email_body=d.get("email_body") or defaults.email_body,
    )


def classification_to_dict(c: Classification) -> dict[str, Any]:
    return {
        "items": [
            {
                "index": ci.index,
                "payee": str(ci.item.payee),
                "payer": str(ci.payer) if ci.payer else "",
                "amount": ci.item.amount,
                "vs": ci.item.vs,
                "ks": ci.item.ks,
                "ss": ci.item.ss,
                "message": ci.item.message,
                "group": ci.group.value if ci.group else None,
                "employee_id": ci.employee_id,
                "label": ci.label,
                "overridden": ci.overridden,
            }
            for ci in c.items
        ],
        "issues": [
            {
                "severity": i.severity.value,
                "code": i.code,
                "message": i.message,
                "item": i.item,
                "params": i.params,
            }
            for i in c.issues
        ],
        "totals": {
            group.value: {"count": count, "amount": amount}
            for group, (count, amount) in c.totals().items()
        },
        "input_total": c.input_total,
        "has_errors": c.has_errors,
    }
