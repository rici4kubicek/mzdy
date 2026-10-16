from datetime import date

import pytest

from payroll import abo
from payroll.abo import AboParseError
from payroll.accounts import AccountNumber, BankAccount


def test_parse_sample(sample_abo):
    f = abo.parse(sample_abo)
    assert f.header.created == date(2026, 10, 15)
    assert f.header.client_name == "Firma s.r.o."
    assert f.header.client_number == "0000123456"
    assert f.bank_code == "0300"
    assert f.file_number == "001000"
    assert len(f.groups) == 1
    group = f.groups[0]
    assert group.payer == AccountNumber("", "1000000005")
    assert group.due_date == date(2026, 11, 12)
    assert group.total == group.items_total == 7_550_000
    first = group.items[0]
    assert first.payee == BankAccount.parse("1000000013/0800")
    assert first.amount == 3_200_000
    assert first.vs == "1001"
    assert first.ks == ""
    assert first.message == "Mzda 10/2026"
    assert group.items[2].ks == "558"


def test_round_trip(sample_abo):
    parsed = abo.parse(sample_abo)
    assert abo.parse(abo.serialize(parsed)) == parsed


def test_serialize_uses_fixed_widths(sample_abo):
    lines = abo.serialize(abo.parse(sample_abo)).decode().split("\r\n")
    assert lines[0] == "UHL1151026Firma s.r.o.        0000123456001999000000000000"
    assert lines[1] == "1 1501 001000 0300"
    assert lines[2] == "2 000000-1000000005 00000007550000 121126"
    assert lines[3] == (
        "000000-1000000013 000003200000 0000001001 08000000 0000000000 AV:Mzda 10/2026"
    )
    assert lines[-2:] == ["5 +", ""]


def test_windows_1250_and_diacritics_are_transliterated(sample_abo):
    data = sample_abo.replace("Mzda 10/2026", "Mzda říjen").encode("cp1250")
    parsed = abo.parse(data)
    assert parsed.items[0].message == "Mzda říjen"
    assert b"AV:Mzda rijen" in abo.serialize(parsed)


def test_group_header_without_payer_and_payer_per_item():
    text = (
        "UHL1151026Firma\r\n"
        "1 1501 001000 0300\r\n"
        "2 00000000100000 121126\r\n"
        "000000-1000000005 000000-1000000013 000000100000 0000000001 08000000 0000000000\r\n"
        "3 +\r\n5 +\r\n"
    )
    f = abo.parse(text)
    assert f.groups[0].payer is None
    item = f.items[0]
    assert item.payer == AccountNumber("", "1000000005")
    assert item.payee == BankAccount.parse("1000000013/0800")
    assert item.message == ""


def test_item_without_specific_symbol(sample_abo):
    text = sample_abo.replace(" 0000000000 AV:Mzda 10/2026", " AV:Mzda 10/2026", 1)
    item = abo.parse(text).items[0]
    assert item.ss == ""
    assert item.message == "Mzda 10/2026"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda s: s.replace("UHL1", "XXX1"), "missing UHL1"),
        (lambda s: s.replace("5 +\r\n", ""), "truncated"),
        (lambda s: s.replace("1 1501", "1 1502"), "unsupported file type"),
        (lambda s: s.replace(" 121126", " 321326"), "invalid date"),
        (lambda s: s.replace("000003200000", "0000032000x0"), "invalid amount"),
        (lambda s: s.replace("3 +\r\n", ""), "file end inside an open group"),
        (lambda s: s + "000000-1000000013 1 1 08000000 1\r\n", "data after end of file"),
    ],
)
def test_parse_errors(sample_abo, mutate, message):
    with pytest.raises(AboParseError, match=message):
        abo.parse(mutate(sample_abo))


def test_empty_file():
    with pytest.raises(AboParseError, match="empty"):
        abo.parse(b"")
