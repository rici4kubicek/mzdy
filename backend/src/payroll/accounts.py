"""Czech domestic bank account numbers: parsing, formatting and validation."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Weights for the mod 11 checksum, applied right-aligned (ČNB decree 169/2011).
_NUMBER_WEIGHTS = (6, 3, 7, 9, 10, 5, 8, 4, 2, 1)
_PREFIX_WEIGHTS = (10, 5, 8, 4, 2, 1)

_ACCOUNT_RE = re.compile(r"^\s*(?:(\d{1,6})-)?(\d{2,10})(?:\s*/\s*(\d{4}))?\s*$")

# Bank codes from the ČNB payment system code list. An unknown code is reported
# as a warning only, so the list does not have to be perfectly up to date.
KNOWN_BANK_CODES = frozenset(
    {
        "0100", "0300", "0600", "0710", "0800", "2010", "2060", "2070", "2100",
        "2200", "2220", "2250", "2260", "2275", "2600", "2700", "3030", "3050",
        "3060", "3500", "4000", "4300", "5500", "5800", "6000", "6100", "6200",
        "6210", "6300", "6363", "6700", "7910", "7950", "7960", "7970", "7990",
        "8030", "8040", "8060", "8090", "8150", "8190", "8198", "8220", "8250",
        "8255", "8265", "8500",
    }
)  # fmt: skip


class InvalidAccountError(ValueError):
    pass


def _checksum_ok(digits: str, weights: tuple[int, ...]) -> bool:
    padded = digits.zfill(len(weights))
    return sum(int(d) * w for d, w in zip(padded, weights, strict=True)) % 11 == 0


@dataclass(frozen=True, order=True)
class AccountNumber:
    """Account number without the bank code, as used in ABO payer fields."""

    prefix: str
    number: str

    def __post_init__(self) -> None:
        # Normalize so that "000019-0002000145" equals "19-2000145".
        object.__setattr__(self, "prefix", self.prefix.lstrip("0"))
        object.__setattr__(self, "number", self.number.lstrip("0"))

    @classmethod
    def parse(cls, text: str) -> AccountNumber:
        match = _ACCOUNT_RE.match(text)
        if not match or match.group(3):
            raise InvalidAccountError(f"Invalid account number: {text!r}")
        return cls(match.group(1) or "", match.group(2))

    @property
    def is_valid(self) -> bool:
        return (
            len(self.prefix) <= 6
            and 2 <= len(self.number) <= 10
            and _checksum_ok(self.prefix, _PREFIX_WEIGHTS)
            and _checksum_ok(self.number, _NUMBER_WEIGHTS)
            and int(self.number) != 0
        )

    def to_abo(self) -> str:
        """Fixed-width ABO form: 6-digit prefix, dash, 10-digit number."""
        return f"{self.prefix.zfill(6)}-{self.number.zfill(10)}"

    def __str__(self) -> str:
        return f"{self.prefix}-{self.number}" if self.prefix else self.number


@dataclass(frozen=True, order=True)
class BankAccount:
    """Full domestic account including the bank code, e.g. ``19-2000145399/0800``."""

    account: AccountNumber
    bank_code: str

    @classmethod
    def parse(cls, text: str) -> BankAccount:
        match = _ACCOUNT_RE.match(text)
        if not match or not match.group(3):
            raise InvalidAccountError(f"Invalid account number: {text!r}")
        return cls(AccountNumber(match.group(1) or "", match.group(2)), match.group(3))

    @property
    def is_valid(self) -> bool:
        return self.account.is_valid and len(self.bank_code) == 4 and self.bank_code.isdigit()

    @property
    def is_known_bank(self) -> bool:
        return self.bank_code in KNOWN_BANK_CODES

    def __str__(self) -> str:
        return f"{self.account}/{self.bank_code}"
