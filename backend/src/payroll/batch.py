"""Classification of ABO payments into groups, checks, and splitting into output files."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from payroll import abo
from payroll.abo import AboFile, AboGroup, AboItem
from payroll.accounts import AccountNumber, BankAccount
from payroll.calendar_cz import due_date
from payroll.models import Employee, Group, PensionAccount, Settings

# Output file name stems, prefixed with the nominal due day (e.g. "15_mzdy_2026-04.kpc").
FILE_NAMES = {
    Group.SALARIES: "mzdy",
    Group.DEDUCTIONS: "odvody",
    Group.PENSION: "duchodove-pripojisteni",
}


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Issue:
    severity: Severity
    code: str
    message: str
    item: int | None = None
    params: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ClassifiedItem:
    index: int
    item: AboItem
    payer: AccountNumber | None
    group: Group | None
    employee_id: str | None = None
    label: str = ""
    overridden: bool = False
    comment: str = ""


@dataclass
class Classification:
    items: list[ClassifiedItem]
    issues: list[Issue] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(issue.severity is Severity.ERROR for issue in self.issues)

    def totals(self) -> dict[Group, tuple[int, int]]:
        """Item count and amount per group."""
        result: dict[Group, tuple[int, int]] = {}
        for group in Group:
            members = [c.item.amount for c in self.items if c.group is group]
            result[group] = (len(members), sum(members))
        return result

    @property
    def input_total(self) -> int:
        return sum(c.item.amount for c in self.items)


def _strs(params: dict[str, object]) -> dict[str, str]:
    return {key: str(value) for key, value in params.items()}


def _line_numbers(item: AboItem) -> set[str]:
    """All numbers on the item's line, without leading zeros."""
    text = item.raw or " ".join([item.vs, item.ks, item.ss, item.message])
    return {n.lstrip("0") for n in re.findall(r"\d+", text)}


def _match_pension(
    item: AboItem, rules: list[PensionAccount]
) -> tuple[PensionAccount | None, bool]:
    """Find the pension rule for an item; the flag says whether the account is a pension one.

    A rule with a variable symbol matches only when that number appears anywhere
    on the payment line; a rule without one matches any payment to the account.
    """
    candidates = [r for r in rules if r.account == item.payee]
    if not candidates:
        return None, False
    numbers = _line_numbers(item)
    for rule in candidates:
        if rule.vs and rule.vs.lstrip("0") in numbers:
            return rule, True
    return next((r for r in candidates if not r.vs), None), True


def period_code(period: str) -> str:
    """``2026-04`` -> ``0426`` (MMYY), as used in salary payment comments."""
    return period[5:7] + period[2:4]


def payment_comment(group: Group | None, period: str, label: str, original: str) -> str:
    """Message written to the output line for a payment of the given group."""
    if group is Group.SALARIES:
        return f"AV: Mzda {period_code(period)} NP: {label}"
    if group is Group.PENSION and label:
        return f"NP: {label} důch. připojištění"
    if group in (Group.DEDUCTIONS, Group.PENSION):
        if label:
            return f"NP: {label}"
        # Manually assigned payment without a known payee keeps its original text.
        if not original or original.startswith(("AV:", "NP:")):
            return original
        return f"NP: {original}"
    return ""


def _employee_lookup(
    employees: list[Employee], on: date
) -> tuple[dict[BankAccount, Employee], dict[BankAccount, Employee]]:
    """Map currently valid accounts and expired accounts to their employees."""
    valid: dict[BankAccount, Employee] = {}
    expired: dict[BankAccount, Employee] = {}
    for employee in employees:
        for acc in employee.accounts:
            if acc.is_valid_on(on) and employee.active:
                valid[acc.account] = employee
            elif acc.valid_to is not None and acc.valid_to < on:
                expired.setdefault(acc.account, employee)
    return valid, expired


def classify(
    source: AboFile,
    employees: list[Employee],
    settings: Settings,
    on: date,
    period: str,
    overrides: dict[int, Group] | None = None,
    previous_salaries: dict[str, int] | None = None,
) -> Classification:
    """Assign every payment to a group, compute its comment and run all checks.

    ``on`` is the date against which account validity is evaluated (normally the
    import date); ``period`` (``YYYY-MM``) is the payroll month used in comments.
    ``overrides`` maps item index to a manually chosen group.
    ``previous_salaries`` maps employee id to last month's salary total.
    """
    overrides = overrides or {}
    valid, expired = _employee_lookup(employees, on)
    known = {k.account: k.label for k in settings.deduction_accounts}
    employees_by_id = {e.id: e for e in employees}

    items: list[ClassifiedItem] = []
    issues: list[Issue] = []

    # ``params`` let the frontend render a localized message for each issue code.
    def error(code: str, message: str, item: int | None = None, **params: object) -> None:
        issues.append(Issue(Severity.ERROR, code, message, item, _strs(params)))

    def warning(code: str, message: str, item: int | None = None, **params: object) -> None:
        issues.append(Issue(Severity.WARNING, code, message, item, _strs(params)))

    for group in source.groups:
        if group.total != group.items_total:
            error(
                "group_total_mismatch",
                f"Group total {group.total} does not match the sum of its items "
                f"{group.items_total}.",
                expected=group.total,
                actual=group.items_total,
            )

    flat: list[tuple[AboGroup, AboItem]] = [(g, i) for g in source.groups for i in g.items]
    for index, (group, item) in enumerate(flat):
        payer = item.payer or group.payer
        payee = item.payee

        if not payee.is_valid:
            error("invalid_account", f"Invalid payee account {payee}.", index, account=payee)
        elif not payee.is_known_bank:
            warning(
                "unknown_bank", f"Unknown bank code {payee.bank_code}.", index, code=payee.bank_code
            )

        if settings.payer_account and payer and payer != settings.payer_account:
            error(
                "payer_mismatch",
                f"Payer account {payer} differs from the company account {settings.payer_account}.",
                index,
                account=payer,
                expected=settings.payer_account,
            )

        group_for_item: Group | None = None
        employee_id: str | None = None
        label = ""

        if payee in valid:
            employee = valid[payee]
            group_for_item, employee_id, label = Group.SALARIES, employee.id, employee.full_name
            changed = next(a for a in employee.accounts if a.account == payee and a.is_valid_on(on))
            if (on - changed.valid_from).days <= settings.account_change_warning_days:
                warning(
                    "recent_account_change",
                    f"Account of {employee.full_name} was changed on "
                    f"{changed.valid_from.isoformat()}.",
                    index,
                    name=employee.full_name,
                    date=changed.valid_from.isoformat(),
                )
        elif payee in known:
            group_for_item, label = Group.DEDUCTIONS, known[payee]
        elif (pension := _match_pension(item, list(settings.pension_accounts)))[1]:
            rule = pension[0]
            if rule is None:
                error(
                    "pension_vs_not_found",
                    f"Payment to pension account {payee} does not contain any of the "
                    "configured variable symbols.",
                    index,
                    account=payee,
                )
            else:
                group_for_item, label = Group.PENSION, rule.label
        elif payee in expired:
            error(
                "expired_account",
                f"Account {payee} is no longer valid for {expired[payee].full_name}.",
                index,
                account=payee,
                name=expired[payee].full_name,
            )

        overridden = False
        if index in overrides:
            target = overrides[index]
            if target is Group.SALARIES and employee_id is None:
                error(
                    "salary_to_unknown_account",
                    f"Account {payee} is not registered to any employee; "
                    "add it to the employee first.",
                    index,
                    account=payee,
                )
            else:
                overridden = target is not group_for_item
                group_for_item = target

        if group_for_item is None and not any(
            i.item == index
            and i.code in {"expired_account", "salary_to_unknown_account", "pension_vs_not_found"}
            for i in issues
        ):
            error(
                "unassigned",
                f"Payment to unknown account {payee} must be assigned.",
                index,
                account=payee,
            )

        comment = payment_comment(group_for_item, period, label, item.message)
        items.append(
            ClassifiedItem(
                index, item, payer, group_for_item, employee_id, label, overridden, comment
            )
        )

    paid = defaultdict(int)
    for c in items:
        if c.group is Group.SALARIES and c.employee_id:
            paid[c.employee_id] += c.item.amount

    for employee in employees:
        if employee.active and employee.id not in paid:
            warning(
                "missing_salary",
                f"No salary payment for {employee.full_name}.",
                name=employee.full_name,
            )

    threshold = settings.salary_change_warning_pct
    for employee_id, amount in paid.items():
        previous = (previous_salaries or {}).get(employee_id)
        if previous and abs(amount - previous) * 100 > previous * threshold:
            warning(
                "salary_change",
                f"Salary of {employees_by_id[employee_id].full_name} changed from "
                f"{previous / 100:.2f} to {amount / 100:.2f} CZK.",
                name=employees_by_id[employee_id].full_name,
                previous=previous,
                current=amount,
            )

    return Classification(items, issues)


def default_due_dates(period: str, settings: Settings) -> dict[Group, date]:
    return {
        group: due_date(period, rule.day, rule.month_offset, rule.shift)
        for group, rule in settings.due_dates.items()
    }


class SplitError(RuntimeError):
    pass


def split(
    source: AboFile,
    classification: Classification,
    due_dates: dict[Group, date],
    created: date,
) -> dict[Group, bytes]:
    """Build one ABO file per non-empty group. Refuses to run with blocking errors.

    Lines are copied from the input with the computed comment; see
    :func:`payroll.abo.write_subset` for how the headers are rewritten.
    """
    if classification.has_errors:
        raise SplitError("The batch has blocking errors.")

    flat = [(g, i) for g in source.groups for i in g.items]
    outputs: dict[Group, bytes] = {}
    for group in Group:
        members = [c for c in classification.items if c.group is group]
        if not members:
            continue
        # Keep the input's group structure (one "2" line per source group).
        parts: dict[int, tuple[AboGroup, list[tuple[AboItem, str]]]] = {}
        for c in members:
            source_group, item = flat[c.index]
            parts.setdefault(id(source_group), (source_group, []))[1].append((item, c.comment))
        outputs[group] = abo.write_subset(source, list(parts.values()), due_dates[group], created)

    # Re-read every output to make sure it is well-formed and nothing was lost.
    parsed = [abo.parse(data) for data in outputs.values()]
    output_total = sum(f.total for f in parsed)
    output_count = sum(len(f.items) for f in parsed)
    if output_total != source.total or output_count != len(source.items):
        raise SplitError(
            f"Output files ({output_count} items, {output_total}) do not match the input "
            f"({len(source.items)} items, {source.total})."
        )
    for f in parsed:
        if any(g.total != g.items_total for g in f.groups):
            raise SplitError("Output group total does not match its items.")
    return outputs


def output_filename(group: Group, period: str, day: int) -> str:
    return f"{day:02d}_{FILE_NAMES[group]}_{period}.kpc"
