import io
import zipfile

from pypdf import PdfReader

from payroll.models import Settings
from payroll.payslips import split_payslips
from tests.pdfgen import make_pdf

PATTERN = Settings().payslip_number_pattern


def page_texts(data: bytes) -> list[str]:
    return [p.extract_text() for p in PdfReader(io.BytesIO(data)).pages]


def test_split_combined_pdf_by_employee_number(employees):
    pdf = make_pdf(
        [
            "Vyplatni paska 10/2026 Osobni cislo: 1001",
            "pokracovani - strana 2",
            "Vyplatni paska 10/2026 OSOBNI CISLO 01002",
        ]
    )
    result = split_payslips(pdf, employees, PATTERN)
    assert set(result.payslips) == {"e1", "e2"}
    assert len(page_texts(result.payslips["e1"])) == 2
    assert "01002" in page_texts(result.payslips["e2"])[0]
    assert result.unmatched == []
    assert result.missing == []


def test_fallback_to_name_and_report_unmatched(employees):
    pdf = make_pdf(["Rekapitulace mezd", "Zamestnanec: Svobodova Eva"])
    result = split_payslips(pdf, employees, PATTERN)
    assert list(result.payslips) == ["e2"]
    assert result.unmatched == ["page 1"]
    assert result.missing == ["e1"]


def test_zip_matched_by_file_name(employees):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("pasky/1001_novak.pdf", make_pdf(["a"]))
        archive.writestr("9999_nekdo.pdf", make_pdf(["b"]))
        archive.writestr("readme.txt", "ignored")
    result = split_payslips(buffer.getvalue(), employees, PATTERN)
    assert list(result.payslips) == ["e1"]
    assert result.unmatched == ["9999_nekdo.pdf"]
    assert result.missing == ["e2"]
