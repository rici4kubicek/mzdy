"""E-mailing payslips.

Messages go through the SMTP server named by ``SMTP_HOST`` (self-hosted) or through
Amazon SES. ``handler`` is the SQS consumer Lambda on AWS; the self-hosted server
calls :func:`send_one` from its own worker thread.

SMTP settings: ``SMTP_HOST``, ``SMTP_PORT`` (default 587), ``SMTP_USERNAME``,
``SMTP_PASSWORD`` and ``SMTP_SECURITY``: ``starttls`` (default), ``ssl`` or ``none``.
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Any

from payroll.models import Employee, Settings
from payroll.repository import Repository, create_repository, utcnow

logger = logging.getLogger(__name__)

_repo: Repository | None = None


def repo() -> Repository:
    global _repo
    if _repo is None:
        _repo = create_repository()
    return _repo


def is_configured() -> bool:
    """Self-hosted installations need an SMTP server; on AWS, SES is always there."""
    return bool(os.environ.get("SMTP_HOST")) or not os.environ.get("DATA_DIR")


def _send_smtp(message: EmailMessage) -> None:
    host = os.environ["SMTP_HOST"]
    security = os.environ.get("SMTP_SECURITY", "starttls").lower()
    port = int(os.environ.get("SMTP_PORT") or (465 if security == "ssl" else 587))
    context = ssl.create_default_context()
    if security == "ssl":
        client: smtplib.SMTP = smtplib.SMTP_SSL(host, port, context=context, timeout=30)
    else:
        client = smtplib.SMTP(host, port, timeout=30)
    with client:
        if security == "starttls":
            client.starttls(context=context)
        if os.environ.get("SMTP_USERNAME"):
            client.login(os.environ["SMTP_USERNAME"], os.environ.get("SMTP_PASSWORD", ""))
        client.send_message(message)


def deliver(message: EmailMessage) -> None:
    if os.environ.get("SMTP_HOST"):
        _send_smtp(message)
        return
    if os.environ.get("DATA_DIR"):
        raise RuntimeError("SMTP server is not configured (SMTP_HOST)")
    import boto3

    boto3.client("ses").send_raw_email(
        Source=message["From"],
        Destinations=[message["To"].addresses[0].addr_spec],
        RawMessage={"Data": message.as_bytes()},
    )


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
        deliver(message)
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
