"""Splitting payslip PDFs and matching them to employees."""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from pypdf import PdfReader, PdfWriter

from payroll.abo import to_ascii
from payroll.models import Employee


@dataclass
class PayslipMatch:
    payslips: dict[str, bytes] = field(default_factory=dict)  # employee id -> PDF
    unmatched: list[str] = field(default_factory=list)  # pages or file names
    missing: list[str] = field(default_factory=list)  # active employee ids without payslip


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", to_ascii(text)).lower()


def _find_employee(
    text: str, pattern: re.Pattern[str], by_number: dict[str, Employee], employees: list[Employee]
) -> Employee | None:
    normalized = _normalize(text)
    for match in pattern.finditer(normalized):
        employee = by_number.get(match.group(1).lstrip("0"))
        if employee:
            return employee
    # Fallback: "Novák Jan" or "Jan Novák" appearing in the text.
    for employee in employees:
        first, last = _normalize(employee.first_name), _normalize(employee.last_name)
        if f"{first} {last}" in normalized or f"{last} {first}" in normalized:
            return employee
    return None


def _missing(employees: list[Employee], found: dict[str, bytes]) -> list[str]:
    return [e.id for e in employees if e.active and e.id not in found]


def split_pdf(data: bytes, employees: list[Employee], number_pattern: str) -> PayslipMatch:
    """Split a combined PDF into per-employee payslips.

    Each page is matched by the employee number found with ``number_pattern``
    (applied to lowercase text without diacritics) or by full name. A page with no
    match continues the previous payslip; leading unmatched pages are reported.
    """
    pattern = re.compile(number_pattern)
    by_number = {e.number.lstrip("0"): e for e in employees}
    reader = PdfReader(io.BytesIO(data))

    pages: dict[str, list[int]] = {}
    result = PayslipMatch()
    current: str | None = None
    for index, page in enumerate(reader.pages):
        employee = _find_employee(page.extract_text() or "", pattern, by_number, employees)
        if employee:
            current = employee.id
        if current is None:
            result.unmatched.append(f"page {index + 1}")
            continue
        pages.setdefault(current, []).append(index)

    for employee_id, indexes in pages.items():
        writer = PdfWriter()
        for index in indexes:
            writer.add_page(reader.pages[index])
        out = io.BytesIO()
        writer.write(out)
        result.payslips[employee_id] = out.getvalue()

    result.missing = _missing(employees, result.payslips)
    return result


def split_zip(data: bytes, employees: list[Employee]) -> PayslipMatch:
    """Match PDFs in a ZIP by the employee number at the start of the file name."""
    by_number = {e.number.lstrip("0"): e for e in employees}
    result = PayslipMatch()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            name = PurePosixPath(info.filename).name
            if info.is_dir() or not name.lower().endswith(".pdf"):
                continue
            match = re.match(r"0*(\d+)", name)
            employee = by_number.get(match.group(1)) if match else None
            if employee is None or employee.id in result.payslips:
                result.unmatched.append(name)
                continue
            result.payslips[employee.id] = archive.read(info)
    result.missing = _missing(employees, result.payslips)
    return result


def split_payslips(data: bytes, employees: list[Employee], number_pattern: str) -> PayslipMatch:
    if data[:4] == b"PK\x03\x04":
        return split_zip(data, employees)
    return split_pdf(data, employees, number_pattern)
