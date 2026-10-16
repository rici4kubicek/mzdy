"""Reader and writer for the Czech ABO (KPC) domestic payment order format.

Structure of a file::

    UHL1ddmmyyNAME________________CLIENTNO__SSSEEEFIXEDCSECRET   file header
    1 1501 NNNNNN BBBB                                         accounting file header
    2 PPPPPP-AAAAAAAAAA TTTTTTTTTTTTTT ddmmyy                    group header
    PPPPPP-AAAAAAAAAA AAAAAAAAAAAA VVVVVVVVVV BBBBKKKK SSSSSSSSSS AV:message   item
    3 +                                                        group end
    5 +                                                        file end

Amounts are in hellers (1/100 CZK). The payer account in the group header is
optional; some exporters instead put the payer account in front of every item.
The reader accepts both variants, the writer always produces the first one.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field, replace
from datetime import date, datetime

from payroll.accounts import AccountNumber, BankAccount

PAYMENT_ORDER = "1501"
CRLF = "\r\n"


class AboParseError(ValueError):
    def __init__(self, line_no: int, message: str) -> None:
        super().__init__(f"Line {line_no}: {message}")
        self.line_no = line_no


@dataclass(frozen=True)
class AboItem:
    payee: BankAccount
    amount: int
    vs: str = ""
    ks: str = ""
    ss: str = ""
    message: str = ""
    payer: AccountNumber | None = None


@dataclass(frozen=True)
class AboGroup:
    due_date: date
    total: int
    items: tuple[AboItem, ...]
    payer: AccountNumber | None = None

    @property
    def items_total(self) -> int:
        return sum(item.amount for item in self.items)


@dataclass(frozen=True)
class AboHeader:
    created: date
    client_name: str = ""
    client_number: str = ""
    interval_start: str = "001"
    interval_end: str = "999"
    fixed_code: str = "000000"
    secret_code: str = "000000"


@dataclass(frozen=True)
class AboFile:
    header: AboHeader
    bank_code: str
    groups: tuple[AboGroup, ...]
    file_number: str = "001000"
    file_type: str = PAYMENT_ORDER

    @property
    def items(self) -> list[AboItem]:
        return [item for group in self.groups for item in group.items]

    @property
    def total(self) -> int:
        return sum(group.items_total for group in self.groups)


@dataclass
class _GroupBuilder:
    due_date: date
    total: int
    payer: AccountNumber | None
    items: list[AboItem] = field(default_factory=list)


def decode(data: bytes) -> str:
    """ABO files are ASCII in theory; Pohoda exports may use windows-1250."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1250")


def to_ascii(text: str) -> str:
    """Strip diacritics; banks reject non-ASCII characters in ABO files."""
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if ord(c) < 128 and not unicodedata.combining(c))


def _parse_date(token: str, line_no: int) -> date:
    try:
        return datetime.strptime(token, "%d%m%y").date()
    except ValueError:
        raise AboParseError(line_no, f"invalid date {token!r}") from None


def _parse_int(token: str, line_no: int, what: str) -> int:
    if not token.isdigit():
        raise AboParseError(line_no, f"invalid {what} {token!r}")
    return int(token)


def _parse_account(token: str, line_no: int) -> AccountNumber:
    try:
        return AccountNumber.parse(token)
    except ValueError:
        raise AboParseError(line_no, f"invalid account number {token!r}") from None


def _symbol(token: str, line_no: int, what: str) -> str:
    """Symbols are stored without leading zeros; empty means not set."""
    if not token.isdigit():
        raise AboParseError(line_no, f"invalid {what} {token!r}")
    return token.lstrip("0")


def _parse_header(line: str, line_no: int) -> AboHeader:
    if len(line) < 10:
        raise AboParseError(line_no, "file header is too short")
    return AboHeader(
        created=_parse_date(line[4:10], line_no),
        client_name=line[10:30].strip(),
        client_number=line[30:40].strip(),
        interval_start=line[40:43].strip() or "001",
        interval_end=line[43:46].strip() or "999",
        fixed_code=line[46:52].strip() or "000000",
        secret_code=line[52:58].strip() or "000000",
    )


def _parse_item(line: str, line_no: int) -> AboItem:
    head, sep, message = line.partition(" AV:")
    tokens = head.split()
    payer = None
    # A dash in the second token means it is an account, i.e. the payer variant.
    if len(tokens) == 6 or (len(tokens) >= 2 and "-" in tokens[1]):
        payer = _parse_account(tokens[0], line_no)
        tokens = tokens[1:]
    if len(tokens) not in (4, 5):
        raise AboParseError(line_no, "unexpected number of fields in payment item")
    bank_ks = tokens[3]
    if len(bank_ks) < 4 or not bank_ks.isdigit():
        raise AboParseError(line_no, f"invalid bank code {bank_ks!r}")
    return AboItem(
        payee=BankAccount(_parse_account(tokens[0], line_no), bank_ks[:4]),
        amount=_parse_int(tokens[1], line_no, "amount"),
        vs=_symbol(tokens[2], line_no, "variable symbol"),
        ks=bank_ks[4:].lstrip("0"),
        ss=_symbol(tokens[4], line_no, "specific symbol") if len(tokens) == 5 else "",
        message=message.strip() if sep else "",
        payer=payer,
    )


def parse(data: bytes | str) -> AboFile:
    text = decode(data) if isinstance(data, bytes) else data
    lines = [(n, line.rstrip()) for n, line in enumerate(text.splitlines(), start=1)]
    lines = [(n, line) for n, line in lines if line.strip()]
    if not lines:
        raise AboParseError(1, "file is empty")

    line_no, first = lines[0]
    if not first.startswith("UHL1"):
        raise AboParseError(line_no, "missing UHL1 file header")
    header = _parse_header(first, line_no)

    file_type = bank_code = file_number = None
    groups: list[AboGroup] = []
    current: _GroupBuilder | None = None
    finished = False

    for line_no, line in lines[1:]:
        if finished:
            raise AboParseError(line_no, "data after end of file")
        tokens = line.split()
        if line.startswith("1 "):
            if file_type is not None:
                raise AboParseError(line_no, "multiple accounting files are not supported")
            if len(tokens) != 4:
                raise AboParseError(line_no, "invalid accounting file header")
            file_type, file_number, bank_code = tokens[1], tokens[2], tokens[3]
            if file_type != PAYMENT_ORDER:
                raise AboParseError(line_no, f"unsupported file type {file_type} (expected 1501)")
        elif line.startswith("2 "):
            if file_type is None or current is not None:
                raise AboParseError(line_no, "unexpected group header")
            if len(tokens) == 4:
                payer = _parse_account(tokens[1], line_no)
                total_token, date_token = tokens[2], tokens[3]
            elif len(tokens) == 3:
                payer = None
                total_token, date_token = tokens[1], tokens[2]
            else:
                raise AboParseError(line_no, "invalid group header")
            current = _GroupBuilder(
                due_date=_parse_date(date_token, line_no),
                total=_parse_int(total_token, line_no, "group total"),
                payer=payer,
            )
        elif line.startswith("3 "):
            if current is None:
                raise AboParseError(line_no, "group end without group header")
            groups.append(
                AboGroup(current.due_date, current.total, tuple(current.items), current.payer)
            )
            current = None
        elif line.startswith("5 "):
            if current is not None:
                raise AboParseError(line_no, "file end inside an open group")
            finished = True
        else:
            if current is None:
                raise AboParseError(line_no, "payment item outside of a group")
            current.items.append(_parse_item(line, line_no))

    if file_type is None:
        raise AboParseError(lines[-1][0], "missing accounting file header")
    if current is not None or not finished:
        raise AboParseError(lines[-1][0], "file is truncated (missing end markers)")

    return AboFile(
        header=header,
        bank_code=bank_code,
        groups=tuple(groups),
        file_number=file_number,
        file_type=file_type,
    )


def _format_item(item: AboItem) -> str:
    fields = [
        item.payee.account.to_abo(),
        f"{item.amount:012d}",
        (item.vs or "0").zfill(10),
        f"{item.payee.bank_code}{(item.ks or '0').zfill(4)}",
        (item.ss or "0").zfill(10),
    ]
    line = " ".join(fields)
    message = to_ascii(item.message).strip()
    return f"{line} AV:{message}" if message else line


def serialize(abo: AboFile) -> bytes:
    h = abo.header
    lines = [
        "UHL1"
        + h.created.strftime("%d%m%y")
        + to_ascii(h.client_name)[:20].ljust(20)
        + h.client_number[:10].rjust(10, "0")
        + h.interval_start.zfill(3)
        + h.interval_end.zfill(3)
        + h.fixed_code.zfill(6)
        + h.secret_code.zfill(6),
        f"1 {abo.file_type} {abo.file_number} {abo.bank_code}",
    ]
    for group in abo.groups:
        payer = f" {group.payer.to_abo()}" if group.payer else ""
        lines.append(f"2{payer} {group.items_total:014d} {group.due_date.strftime('%d%m%y')}")
        lines.extend(_format_item(replace(item, payer=None)) for item in group.items)
        lines.append("3 +")
    lines.append("5 +")
    return (CRLF.join(lines) + CRLF).encode("ascii")
