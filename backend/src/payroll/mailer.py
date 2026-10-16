"""SQS consumer Lambda that e-mails payslips through Amazon SES."""

from __future__ import annotations

import json
import logging
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Any

import boto3

from payroll.models import Employee, Settings
from payroll.repository import Repository, utcnow

logger = logging.getLogger(__name__)

_repo: Repository | None = None


def repo() -> Repository:
    global _repo
    if _repo is None:
        _repo = Repository()
    return _repo


def _render(template: str, employee: Employee, period: str) -> str:
    year, month = period.split("-")
    values = {"name": employee.full_name, "first_name": employee.first_name}
    values["period"] = f"{int(month)}/{year}"
    # Unknown placeholders are left as they are instead of failing the send.
    for key, value in values.items():
        template = template.replace("{" + key + "}", value)
    return template


def build_message(settings: Settings, employee: Employee, period: str, pdf: bytes) -> EmailMessage:
    message = EmailMessage()
    message["From"] = settings.email_sender
    message["To"] = formataddr((employee.full_name, employee.email))
    message["Subject"] = _render(settings.email_subject, employee, period)
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain=settings.email_sender.rpartition("@")[2] or None)
    message.set_content(_render(settings.email_body, employee, period))
    message.add_attachment(
        pdf,
        maintype="application",
        subtype="pdf",
        filename=f"vyplatni-paska-{period}.pdf",
    )
    return message


def send_one(period: str, employee_id: str) -> None:
    r = repo()
    payslip = r.get_payslip(period, employee_id)
    employee = r.get_employee(employee_id)
    if payslip is None or employee is None or not employee.email:
        logger.warning("Skipping %s/%s: payslip or e-mail missing", period, employee_id)
        return
    settings = r.get_settings()
    message = build_message(settings, employee, period, r.get_file(payslip["key"]))
    try:
        boto3.client("ses").send_raw_email(
            Source=settings.email_sender,
            Destinations=[employee.email],
            RawMessage={"Data": message.as_bytes()},
        )
    except Exception as exc:
        payslip.update(status="failed", error=str(exc))
        r.put_payslip(period, payslip)
        raise
    payslip.update(status="sent", sent_at=utcnow(), sent_to=employee.email, error=None)
    r.put_payslip(period, payslip)
    r.audit("system", "payslip.sent", period=period, employee=employee.full_name, to=employee.email)


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Process SQS records; failed records are retried by SQS (partial batch response)."""
    failures = []
    for record in event.get("Records", []):
        try:
            body = json.loads(record["body"])
            send_one(body["period"], body["employee_id"])
        except Exception:
            logger.exception("Failed to send payslip")
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
