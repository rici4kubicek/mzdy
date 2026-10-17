"""HTTP API Lambda: authentication, employees, settings, batches and payslips."""

from __future__ import annotations

import base64
import json
import os
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import boto3

from payroll import abo, auth
from payroll.abo import AboParseError
from payroll.accounts import BankAccount, InvalidAccountError
from payroll.api.router import HttpError, Request, Response, Router, session_cookie
from payroll.batch import (
    Classification,
    SplitError,
    classify,
    default_due_dates,
    output_filename,
    split,
)
from payroll.calendar_cz import is_business_day
from payroll.models import Employee, Group, Settings
from payroll.payslips import split_payslips
from payroll.repository import Repository, utcnow
from payroll.serde import (
    classification_to_dict,
    employee_from_dict,
    employee_to_dict,
    settings_from_dict,
    settings_to_dict,
)

router = Router()

_repo: Repository | None = None
_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def repo() -> Repository:
    global _repo
    if _repo is None:
        _repo = Repository()
    return _repo


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    return router.handle(event)


def _today() -> date:
    return datetime.now(UTC).date()


# --- authentication ----------------------------------------------------------


@router.route("POST", "/api/auth/login", public=True)
def login(req: Request) -> Response:
    body = req.json()
    username = str(body.get("username", "")).strip().lower()
    password = str(body.get("password", ""))
    user = repo().get_user(username) if username else None
    now = datetime.now(UTC)

    if user and user.get("locked_until") and datetime.fromisoformat(user["locked_until"]) > now:
        raise HttpError(429, "Account is temporarily locked, try again later")

    ok = auth.verify_password(password, user["password_hash"] if user else auth.DUMMY_HASH)
    if not user or not ok:
        if user:
            user["failed_attempts"] = user.get("failed_attempts", 0) + 1
            if user["failed_attempts"] >= auth.MAX_FAILED_ATTEMPTS:
                locked = now + timedelta(minutes=auth.LOCKOUT_MINUTES)
                user["locked_until"] = locked.isoformat()
                user["failed_attempts"] = 0
                repo().audit(username, "user.locked")
            repo().put_user(user)
        raise HttpError(401, "Invalid username or password")

    user["failed_attempts"] = 0
    user["locked_until"] = None
    user["last_login"] = utcnow()
    repo().put_user(user)
    repo().audit(username, "user.login")
    token = auth.issue_token(username, user.get("role", "user"))
    return Response(
        {"username": username, "role": user.get("role", "user")},
        cookies=[session_cookie(token, auth.SESSION_HOURS * 3600)],
    )


@router.route("POST", "/api/auth/logout", public=True)
def logout(req: Request) -> Response:
    return Response({"ok": True}, cookies=[session_cookie("", 0)])


@router.route("GET", "/api/auth/me")
def me(req: Request) -> dict[str, Any]:
    return {"username": req.user["sub"], "role": req.user.get("role", "user")}


# --- employees ---------------------------------------------------------------


def _settings_accounts(settings: Settings) -> dict[BankAccount, str]:
    """Deduction and pension accounts with their labels."""
    accounts = {k.account: k.label for k in settings.deduction_accounts}
    accounts.update({k.account: k.label for k in settings.pension_accounts})
    return accounts


def _validate_employee(employee: Employee, others: list[Employee]) -> None:
    if not employee.number:
        raise HttpError(400, "Employee number is required")
    if not employee.last_name:
        raise HttpError(400, "Last name is required")
    if employee.email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", employee.email):
        raise HttpError(400, f"Invalid e-mail address {employee.email}")
    for acc in employee.accounts:
        if not acc.account.is_valid:
            raise HttpError(400, f"Invalid account number {acc.account}")
        if acc.valid_to and acc.valid_to < acc.valid_from:
            raise HttpError(400, f"Account {acc.account}: valid to is before valid from")
    settings_accounts = _settings_accounts(repo().get_settings())
    for acc in employee.accounts:
        if acc.account in settings_accounts:
            raise HttpError(
                400,
                f"Account {acc.account} is configured in settings as "
                f"{settings_accounts[acc.account]}",
            )
    for other in others:
        if other.id == employee.id:
            continue
        if other.number.lstrip("0") == employee.number.lstrip("0"):
            raise HttpError(400, f"Employee number {employee.number} is already used")
        taken = {a.account for a in other.accounts}
        for acc in employee.accounts:
            if acc.account in taken:
                raise HttpError(400, f"Account {acc.account} already belongs to {other.full_name}")


def _parse_employee(data: dict[str, Any], employee_id: str) -> Employee:
    try:
        return employee_from_dict({**data, "id": employee_id})
    except InvalidAccountError as exc:
        raise HttpError(400, str(exc)) from None
    except (KeyError, ValueError, TypeError) as exc:
        raise HttpError(400, f"Invalid employee data: {exc}") from None


def _account_set(employee: Employee | None) -> list[dict[str, Any]]:
    return employee_to_dict(employee)["accounts"] if employee else []


def _save_employee(req: Request, employee_id: str, before: Employee | None) -> dict[str, Any]:
    employee = _parse_employee(req.json(), employee_id)
    _validate_employee(employee, repo().list_employees())
    repo().put_employee(employee)
    action = "employee.update" if before else "employee.create"
    detail: dict[str, Any] = {"employee": employee.full_name, "number": employee.number}
    if _account_set(before) != _account_set(employee):
        detail["accounts_before"] = _account_set(before)
        detail["accounts_after"] = _account_set(employee)
        action += ".accounts"
    repo().audit(req.username, action, **detail)
    return employee_to_dict(employee)


@router.route("GET", "/api/employees")
def list_employees(req: Request) -> list[dict[str, Any]]:
    return [employee_to_dict(e) for e in repo().list_employees()]


@router.route("POST", "/api/employees")
def create_employee(req: Request) -> Response:
    return Response(_save_employee(req, uuid.uuid4().hex, None), 201)


@router.route("PUT", "/api/employees/{employee_id}")
def update_employee(req: Request) -> dict[str, Any]:
    before = repo().get_employee(req.params["employee_id"])
    if before is None:
        raise HttpError(404, "Employee not found")
    return _save_employee(req, before.id, before)


@router.route("DELETE", "/api/employees/{employee_id}")
def delete_employee(req: Request) -> dict[str, Any]:
    employee = repo().get_employee(req.params["employee_id"])
    if employee is None:
        raise HttpError(404, "Employee not found")
    repo().delete_employee(employee.id)
    repo().audit(
        req.username, "employee.delete", employee=employee.full_name, number=employee.number
    )
    return {"ok": True}


# --- settings ----------------------------------------------------------------


@router.route("GET", "/api/settings")
def get_settings(req: Request) -> dict[str, Any]:
    return settings_to_dict(repo().get_settings())


@router.route("PUT", "/api/settings")
def put_settings(req: Request) -> dict[str, Any]:
    try:
        settings = settings_from_dict(req.json())
    except (KeyError, ValueError, TypeError) as exc:
        raise HttpError(400, f"Invalid settings: {exc}") from None
    if settings.payer_account and not settings.payer_account.is_valid:
        raise HttpError(400, f"Invalid company account {settings.payer_account}")
    for known in (*settings.deduction_accounts, *settings.pension_accounts):
        if not known.account.is_valid:
            raise HttpError(400, f"Invalid account number {known.account}")
    _validate_settings_accounts(settings)
    for rule in settings.due_dates.values():
        if not 1 <= rule.day <= 31 or not 0 <= rule.month_offset <= 2:
            raise HttpError(400, "Invalid due date rule")
    try:
        if re.compile(settings.payslip_number_pattern).groups != 1:
            raise HttpError(400, "Payslip pattern must contain exactly one group")
    except re.error as exc:
        raise HttpError(400, f"Invalid payslip pattern: {exc}") from None
    before = settings_to_dict(repo().get_settings())
    repo().put_settings(settings)
    after = settings_to_dict(settings)
    changed = {k: {"before": before[k], "after": v} for k, v in after.items() if before[k] != v}
    repo().audit(req.username, "settings.update", changes=changed)
    return after


def _validate_settings_accounts(settings: Settings) -> None:
    deduction = [k.account for k in settings.deduction_accounts]
    if len(set(deduction)) != len(deduction):
        raise HttpError(400, "A deduction account is listed more than once")
    pension: dict[BankAccount, list[str]] = {}
    for rule in settings.pension_accounts:
        pension.setdefault(rule.account, []).append(rule.vs.lstrip("0"))
    for account, symbols in pension.items():
        if account in deduction:
            raise HttpError(400, f"Account {account} is both a deduction and a pension account")
        # A shared pension account needs a distinct variable symbol for every entry.
        if len(symbols) > 1 and (not all(symbols) or len(set(symbols)) != len(symbols)):
            raise HttpError(
                400,
                f"Pension account {account} is used more than once; every entry needs "
                "its own variable symbol",
            )
    for employee in repo().list_employees():
        for acc in employee.accounts:
            if acc.account in deduction or acc.account in pension:
                raise HttpError(
                    400, f"Account {acc.account} already belongs to {employee.full_name}"
                )


# --- batches -----------------------------------------------------------------


def _period(req: Request) -> str:
    period = req.params["period"]
    if not _PERIOD_RE.match(period):
        raise HttpError(400, "Period must be YYYY-MM")
    return period


def _batch(period: str) -> dict[str, Any]:
    batch = repo().get_batch(period)
    if batch is None:
        raise HttpError(404, "Batch not found")
    return batch


def _ensure_not_uploaded(batch: dict[str, Any]) -> None:
    """Changing a batch whose files are already in the bank risks double payments."""
    uploaded = [g for g, o in batch.get("outputs", {}).items() if o.get("uploaded_at")]
    if uploaded:
        raise HttpError(
            409,
            "Some output files are already marked as uploaded to the bank: " + ", ".join(uploaded),
        )


def _status(batch: dict[str, Any]) -> str:
    outputs = batch.get("outputs") or {}
    if not batch.get("abo_key"):
        return "new"
    if not outputs:
        return "imported"
    if all(o.get("uploaded_at") for o in outputs.values()):
        return "uploaded"
    return "generated"


def _previous_period(period: str) -> str:
    year, month = (int(p) for p in period.split("-"))
    return f"{year - 1}-12" if month == 1 else f"{year}-{month - 1:02d}"


def _classify(batch: dict[str, Any]) -> tuple[abo.AboFile, Classification]:
    source = abo.parse(repo().get_file(batch["abo_key"]))
    previous = repo().get_batch(_previous_period(batch["period"])) or {}
    overrides = {int(k): Group(v) for k, v in (batch.get("overrides") or {}).items()}
    classification = classify(
        source,
        repo().list_employees(),
        repo().get_settings(),
        on=date.fromisoformat(batch["imported_at"][:10]),
        period=batch["period"],
        overrides=overrides,
        previous_salaries=previous.get("salaries_by_employee"),
    )
    return source, classification


def _due_dates(batch: dict[str, Any]) -> dict[Group, date]:
    # Groups added after the batch was imported fall back to the configured rule.
    due_dates = default_due_dates(batch["period"], repo().get_settings())
    due_dates.update({Group(k): date.fromisoformat(v) for k, v in batch["due_dates"].items()})
    return due_dates


def _batch_summary(batch: dict[str, Any]) -> dict[str, Any]:
    return {**batch, "status": _status(batch)}


@router.route("GET", "/api/batches")
def list_batches(req: Request) -> list[dict[str, Any]]:
    return [_batch_summary(b) for b in repo().list_batches()]


@router.route("PUT", "/api/batches/{period}/abo")
def upload_abo(req: Request) -> dict[str, Any]:
    period = _period(req)
    body = req.json()
    filename = os.path.basename(str(body.get("filename") or "input.abo"))
    try:
        content = base64.b64decode(body["content_base64"], validate=True)
    except (KeyError, ValueError):
        raise HttpError(400, "content_base64 is required") from None
    try:
        source = abo.parse(content)
    except AboParseError as exc:
        raise HttpError(400, f"Invalid ABO file: {exc}") from None

    batch = repo().get_batch(period) or {"period": period}
    _ensure_not_uploaded(batch)
    key = f"batches/{period}/input/{uuid.uuid4().hex[:8]}-{filename}"
    repo().put_file(key, content, "text/plain")
    settings = repo().get_settings()
    batch.update(
        abo_key=key,
        abo_filename=filename,
        imported_at=utcnow(),
        imported_by=req.username,
        overrides={},
        due_dates={g.value: d.isoformat() for g, d in default_due_dates(period, settings).items()},
        outputs={},
        input_count=len(source.items),
        input_total=source.total,
    )
    repo().put_batch(batch)
    repo().audit(
        req.username,
        "batch.import",
        period=period,
        filename=filename,
        count=len(source.items),
        total=source.total,
    )
    return get_batch(req)


@router.route("GET", "/api/batches/{period}")
def get_batch(req: Request) -> dict[str, Any]:
    period = _period(req)
    batch = _batch(period)
    _, classification = _classify(batch)
    batch["due_dates"] = {g.value: d.isoformat() for g, d in _due_dates(batch).items()}
    return {
        "batch": _batch_summary(batch),
        "classification": classification_to_dict(classification),
    }


@router.route("PUT", "/api/batches/{period}/overrides")
def put_overrides(req: Request) -> dict[str, Any]:
    period = _period(req)
    batch = _batch(period)
    _ensure_not_uploaded(batch)
    try:
        overrides = {
            str(int(k)): Group(v).value for k, v in (req.json().get("overrides") or {}).items() if v
        }
    except ValueError:
        raise HttpError(400, "Invalid overrides") from None
    batch["overrides"] = overrides
    batch["outputs"] = {}
    repo().put_batch(batch)
    repo().audit(req.username, "batch.overrides", period=period, overrides=overrides)
    return get_batch(req)


@router.route("PUT", "/api/batches/{period}/due-dates")
def put_due_dates(req: Request) -> dict[str, Any]:
    period = _period(req)
    batch = _batch(period)
    _ensure_not_uploaded(batch)
    due_dates = {g.value: d.isoformat() for g, d in _due_dates(batch).items()}
    for key, value in (req.json().get("due_dates") or {}).items():
        try:
            group, day = Group(key), date.fromisoformat(value)
        except ValueError:
            raise HttpError(400, f"Invalid due date {key}={value}") from None
        if not is_business_day(day):
            raise HttpError(400, f"{day.isoformat()} is not a business day")
        due_dates[group.value] = day.isoformat()
    batch["due_dates"] = due_dates
    batch["outputs"] = {}
    repo().put_batch(batch)
    repo().audit(req.username, "batch.due_dates", period=period, due_dates=due_dates)
    return get_batch(req)


@router.route("POST", "/api/batches/{period}/outputs")
def generate_outputs(req: Request) -> dict[str, Any]:
    period = _period(req)
    batch = _batch(period)
    _ensure_not_uploaded(batch)
    source, classification = _classify(batch)
    due_dates = _due_dates(batch)
    try:
        files = split(source, classification, due_dates, created=_today())
    except SplitError as exc:
        raise HttpError(409, str(exc)) from None

    settings = repo().get_settings()
    outputs: dict[str, Any] = {}
    for group, data in files.items():
        filename = output_filename(group, period, settings.due_dates[group].day)
        key = f"batches/{period}/output/{filename}"
        repo().put_file(key, data, "text/plain")
        output = abo.parse(data)
        outputs[group.value] = {
            "filename": filename,
            "key": key,
            "due_date": due_dates[group].isoformat(),
            "count": len(output.items),
            "total": output.total,
            "generated_at": utcnow(),
            "uploaded_at": None,
        }
    salaries: dict[str, int] = {}
    for c in classification.items:
        if c.group is Group.SALARIES and c.employee_id:
            salaries[c.employee_id] = salaries.get(c.employee_id, 0) + c.item.amount
    batch["outputs"] = outputs
    batch["salaries_by_employee"] = salaries
    repo().put_batch(batch)
    repo().audit(
        req.username,
        "batch.generate",
        period=period,
        outputs={g: {"count": o["count"], "total": o["total"]} for g, o in outputs.items()},
    )
    return _batch_summary(batch)


def _output(batch: dict[str, Any], group: str) -> dict[str, Any]:
    output = (batch.get("outputs") or {}).get(group)
    if output is None:
        raise HttpError(404, "Output file not found")
    return output


@router.route("GET", "/api/batches/{period}/outputs/{group}/download")
def download_output(req: Request) -> dict[str, Any]:
    period = _period(req)
    output = _output(_batch(period), req.params["group"])
    repo().audit(req.username, "batch.download", period=period, file=output["filename"])
    return {"url": repo().download_url(output["key"], output["filename"])}


@router.route("POST", "/api/batches/{period}/outputs/{group}/uploaded")
def mark_uploaded(req: Request) -> dict[str, Any]:
    period = _period(req)
    batch = _batch(period)
    output = _output(batch, req.params["group"])
    uploaded = bool(req.json().get("uploaded", True))
    output["uploaded_at"] = utcnow() if uploaded else None
    output["uploaded_by"] = req.username if uploaded else None
    repo().put_batch(batch)
    repo().audit(
        req.username,
        "batch.mark_uploaded" if uploaded else "batch.unmark_uploaded",
        period=period,
        file=output["filename"],
    )
    return _batch_summary(batch)


# --- payslips ----------------------------------------------------------------


@router.route("POST", "/api/batches/{period}/payslips/upload-url")
def payslip_upload_url(req: Request) -> dict[str, Any]:
    period = _period(req)
    _batch(period)
    content_type = req.json().get("content_type", "application/pdf")
    if content_type not in ("application/pdf", "application/zip", "application/x-zip-compressed"):
        raise HttpError(400, "Only PDF or ZIP files are accepted")
    key = f"batches/{period}/payslips/source/{uuid.uuid4().hex}"
    return {"url": repo().upload_url(key, content_type), "key": key}


@router.route("POST", "/api/batches/{period}/payslips/process")
def process_payslips(req: Request) -> dict[str, Any]:
    period = _period(req)
    batch = _batch(period)
    key = str(req.json().get("key", ""))
    if not key.startswith(f"batches/{period}/payslips/source/"):
        raise HttpError(400, "Invalid file key")
    data = repo().get_file(key)
    employees = repo().list_employees()
    try:
        result = split_payslips(data, employees, repo().get_settings().payslip_number_pattern)
    except Exception as exc:
        raise HttpError(400, f"Cannot read the payslip file: {exc}") from None

    repo().delete_payslips(period)
    for employee_id, pdf in result.payslips.items():
        slip_key = f"batches/{period}/payslips/{employee_id}.pdf"
        repo().put_file(slip_key, pdf, "application/pdf")
        repo().put_payslip(
            period,
            {
                "employee_id": employee_id,
                "key": slip_key,
                "status": "ready",
                "sent_at": None,
                "sent_to": None,
                "error": None,
            },
        )
    batch["payslips"] = {
        "source_key": key,
        "processed_at": utcnow(),
        "unmatched": result.unmatched,
        "missing": result.missing,
    }
    repo().put_batch(batch)
    repo().audit(
        req.username,
        "payslips.process",
        period=period,
        matched=len(result.payslips),
        unmatched=result.unmatched,
        missing=result.missing,
    )
    return list_payslips(req)


@router.route("GET", "/api/batches/{period}/payslips")
def list_payslips(req: Request) -> dict[str, Any]:
    period = _period(req)
    batch = _batch(period)
    return {"summary": batch.get("payslips"), "payslips": repo().list_payslips(period)}


@router.route("GET", "/api/batches/{period}/payslips/{employee_id}/preview")
def preview_payslip(req: Request) -> dict[str, Any]:
    period = _period(req)
    payslip = repo().get_payslip(period, req.params["employee_id"])
    if payslip is None:
        raise HttpError(404, "Payslip not found")
    return {"url": repo().download_url(payslip["key"], f"payslip-{period}.pdf", inline=True)}


@router.route("POST", "/api/batches/{period}/payslips/send")
def send_payslips(req: Request) -> dict[str, Any]:
    period = _period(req)
    _batch(period)
    if not repo().get_settings().email_sender:
        raise HttpError(400, "Sender e-mail address is not configured in settings")
    requested = req.json().get("employee_ids")
    employees = {e.id: e for e in repo().list_employees()}
    sqs = boto3.client("sqs")
    queued: list[str] = []
    skipped: list[dict[str, str]] = []
    for payslip in repo().list_payslips(period):
        employee_id = payslip["employee_id"]
        if requested is not None and employee_id not in requested:
            continue
        # Without an explicit selection, already sent payslips are not sent again.
        if requested is None and payslip["status"] in ("sent", "queued"):
            continue
        employee = employees.get(employee_id)
        if employee is None or not employee.email:
            skipped.append({"employee_id": employee_id, "reason": "missing_email"})
            continue
        payslip.update(status="queued", error=None)
        repo().put_payslip(period, payslip)
        sqs.send_message(
            QueueUrl=os.environ["QUEUE_URL"],
            MessageBody=json.dumps({"period": period, "employee_id": employee_id}),
        )
        queued.append(employee_id)
    repo().audit(req.username, "payslips.send", period=period, queued=queued, skipped=skipped)
    return {"queued": queued, "skipped": skipped}


# --- audit -------------------------------------------------------------------


@router.route("GET", "/api/audit")
def list_audit(req: Request) -> list[dict[str, Any]]:
    month = req.query.get("month") or _today().isoformat()[:7]
    if not _PERIOD_RE.match(month):
        raise HttpError(400, "Month must be YYYY-MM")
    return repo().list_audit(month)
