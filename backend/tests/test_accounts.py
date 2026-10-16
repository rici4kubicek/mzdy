import pytest

from payroll.accounts import AccountNumber, BankAccount, InvalidAccountError


def test_parse_full_account():
    acc = BankAccount.parse("19-2000145399/0800")
    assert acc.account == AccountNumber("19", "2000145399")
    assert acc.bank_code == "0800"
    assert acc.is_valid
    assert str(acc) == "19-2000145399/0800"


def test_leading_zeros_are_normalized():
    assert AccountNumber.parse("000019-2000145399") == AccountNumber.parse("19-2000145399")
    assert AccountNumber("000000", "0000000123") == AccountNumber("", "123")


def test_abo_format_is_zero_padded():
    assert AccountNumber("19", "2000145399").to_abo() == "000019-2000145399"
    assert AccountNumber("", "1000000005").to_abo() == "000000-1000000005"


@pytest.mark.parametrize(
    "text",
    ["2000145398/0800", "18-2000145399/0800", "0000000000/0300"],
)
def test_checksum_failures(text):
    assert not BankAccount.parse(text).is_valid


@pytest.mark.parametrize("text", ["abc", "123", "1234567/08", "1234567-12345/0800"])
def test_parse_rejects_garbage(text):
    with pytest.raises(InvalidAccountError):
        BankAccount.parse(text)


def test_account_number_parse_rejects_bank_code():
    with pytest.raises(InvalidAccountError):
        AccountNumber.parse("2000145399/0800")


def test_unknown_bank_code():
    assert BankAccount.parse("1000000005/0300").is_known_bank
    assert not BankAccount.parse("1000000005/9999").is_known_bank
