import base64
import json
import smtplib
from urllib.parse import parse_qs, parse_qsl, urlparse

import boto3
import pytest
from moto import mock_aws

from payroll import auth, mailer
from payroll.api import app
from payroll.cli import create_user
from payroll.repository import AwsRepository, LocalRepository
from tests.pdfgen import make_pdf

PASSWORD = "correct horse battery"


class FakeSMTP:
    """Stands in for smtplib.SMTP; records sent messages."""

    sent: list = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        pass

    def send_message(self, message):
        FakeSMTP.sent.append(message)


def _aws_repo(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("TABLE_NAME", "payroll")
    monkeypatch.setenv("BUCKET_NAME", "payroll-data")
    boto3.client("dynamodb").create_table(
        TableName="payroll",
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    boto3.client("s3").create_bucket(
        Bucket="payroll-data",
        CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
    )
    queue = boto3.client("sqs").create_queue(QueueName="payslips")
    monkeypatch.setenv("QUEUE_URL", queue["QueueUrl"])
    boto3.client("ses").verify_email_identity(EmailAddress="mzdy@example.com")
    return AwsRepository()


@pytest.fixture(params=["aws", "local"])
def repo(request, monkeypatch, tmp_path):
    """The API runs against both storage backends."""
    monkeypatch.setenv("JWT_SECRET", "test-secret-test-secret-test-secret")
    monkeypatch.setattr(auth, "_secret_cache", None)
    with mock_aws():
        if request.param == "aws":
            monkeypatch.delenv("DATA_DIR", raising=False)
            repository = _aws_repo(monkeypatch)
        else:
            monkeypatch.setenv("DATA_DIR", str(tmp_path))
            monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
            monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
            FakeSMTP.sent = []
            repository = LocalRepository(tmp_path)
        monkeypatch.setattr(app, "_repo", repository)
        monkeypatch.setattr(mailer, "_repo", repository)
        create_user(repository, "Ucetni", "admin", PASSWORD)
        yield repository


def deliver_queued(repo):
    """Run the e-mail worker for everything queued so far."""
    if isinstance(repo, LocalRepository):
        while not repo.mail_queue.empty():
            mailer.send_one(*repo.mail_queue.get())
        return
    sqs = boto3.client("sqs")
    queue_url = sqs.get_queue_url(QueueName="payslips")["QueueUrl"]
    messages = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10)["Messages"]
    records = [{"messageId": m["MessageId"], "body": m["Body"]} for m in messages]
    assert mailer.handler({"Records": records}) == {"batchItemFailures": []}


class Client:
    def __init__(self) -> None:
        self.cookies: dict[str, str] = {}

    def request(self, method, path, body=None, query=None, csrf=True, raw=None):
        headers = {"content-type": "application/json"}
        if csrf:
            headers["x-requested-with"] = "payroll"
        url = urlparse(path)
        if url.query:
            query = dict(parse_qsl(url.query))
        event = {
            "rawPath": url.path,
            "requestContext": {"http": {"method": method}},
            "headers": headers,
            "queryStringParameters": query,
            "cookies": [f"{k}={v}" for k, v in self.cookies.items()],
            "body": base64.b64encode(raw).decode()
            if raw is not None
            else json.dumps(body)
            if body is not None
            else None,
            "isBase64Encoded": raw is not None,
        }
        response = app.handler(event)
        for cookie in response.get("cookies", []):
            name, _, rest = cookie.partition("=")
            self.cookies[name] = rest.split(";")[0]
        if response.get("isBase64Encoded"):
            return response["statusCode"], base64.b64decode(response["body"]), response["headers"]
        return response["statusCode"], json.loads(response["body"])

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body or {}, **kw)

    def put(self, path, body=None, **kw):
        return self.request("PUT", path, body or {}, **kw)

    def delete(self, path, **kw):
        return self.request("DELETE", path, **kw)


@pytest.fixture
def client(repo):
    c = Client()
    status, _ = c.post("/api/auth/login", {"username": "ucetni", "password": PASSWORD})
    assert status == 200
    return c


def setup_registry(client):
    employees = [
        ("1001", "Jan", "Novák", "jan@example.com", "1000000013/0800"),
        ("1002", "Eva", "Svobodová", "eva@example.com", "19-2000145399/0100"),
    ]
    ids = []
    for number, first, last, email, account in employees:
        status, body = client.post(
            "/api/employees",
            {
                "number": number,
                "first_name": first,
                "last_name": last,
                "email": email,
                "accounts": [{"account": account, "valid_from": "2024-01-01"}],
            },
        )
        assert status == 201, body
        ids.append(body["id"])
    status, body = client.put(
        "/api/settings",
        {
            "payer_account": "1000000005",
            "deduction_accounts": [
                {"account": "1000000021/0710", "label": "VZP"},
                {"account": "1000000048/0710", "label": "ČSSZ"},
                {"account": "1000000056/0710", "label": "Finanční úřad"},
            ],
            "pension_accounts": [
                {"account": "27-1840220297/0100", "label": "Dvořák", "vs": "900011111"},
                {"account": "27-1840220297/0100", "label": "Černý", "vs": "900022222"},
            ],
            "email_sender": "mzdy@example.com",
        },
    )
    assert status == 200, body
    return ids


def upload(client, sample_abo, period="2026-10"):
    content = base64.b64encode(sample_abo.encode()).decode()
    return client.put(
        f"/api/batches/{period}/abo", {"filename": "mzdy.abo", "content_base64": content}
    )


def test_requires_login(repo):
    status, body = Client().get("/api/employees")
    assert status == 401


def test_login_lockout(repo):
    c = Client()
    for _ in range(5):
        status, _ = c.post("/api/auth/login", {"username": "ucetni", "password": "wrong"})
        assert status == 401
    status, body = c.post("/api/auth/login", {"username": "ucetni", "password": PASSWORD})
    assert status == 429


def test_login_unknown_user(repo):
    status, _ = Client().post("/api/auth/login", {"username": "nobody", "password": "x"})
    assert status == 401


def test_csrf_header_required(client):
    status, body = client.post("/api/employees", {}, csrf=False)
    assert status == 403


def test_me_and_logout(client):
    assert client.get("/api/auth/me") == (200, {"username": "ucetni", "role": "admin"})
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me")[0] == 401


def test_employee_validation(client):
    setup_registry(client)
    base = {"number": "1003", "first_name": "A", "last_name": "B"}
    status, body = client.post(
        "/api/employees",
        {**base, "accounts": [{"account": "1000000014/0800", "valid_from": "2024-01-01"}]},
    )
    assert status == 400 and "Invalid account" in body["error"]
    status, body = client.post(
        "/api/employees",
        {**base, "accounts": [{"account": "1000000013/0800", "valid_from": "2024-01-01"}]},
    )
    assert status == 400 and "already belongs" in body["error"]
    status, body = client.post("/api/employees", {**base, "number": "01001"})
    assert status == 400 and "already used" in body["error"]


def test_full_salary_flow(client, repo, sample_abo):
    ids = setup_registry(client)

    status, body = upload(client, sample_abo)
    assert status == 200, body
    c = body["classification"]
    assert c["has_errors"] is False
    assert c["totals"]["salaries"] == {"count": 2, "amount": 5_700_000}
    assert c["totals"]["deductions"] == {"count": 3, "amount": 1_850_000}
    assert c["totals"]["pension"] == {"count": 2, "amount": 250_000}
    assert c["items"][0]["comment"] == "AV: Mzda 1026 Jan Novák NP: Mzda 1026 Jan Novák"
    assert body["batch"]["due_dates"] == {
        "salaries": "2026-11-13",
        "deductions": "2026-11-20",
        "pension": "2026-11-25",
    }
    assert body["batch"]["status"] == "imported"

    status, body = client.put(
        "/api/batches/2026-10/due-dates", {"due_dates": {"deductions": "2026-11-21"}}
    )
    assert status == 400 and "business day" in body["error"]
    status, body = client.put(
        "/api/batches/2026-10/due-dates", {"due_dates": {"deductions": "2026-11-19"}}
    )
    assert status == 200

    status, batch = client.post("/api/batches/2026-10/outputs")
    assert status == 200, batch
    assert batch["status"] == "generated"
    outputs = batch["outputs"]
    assert outputs["salaries"]["filename"] == "15_mzdy_2026-10.kpc"
    assert outputs["pension"]["filename"] == "25_duchodove-pripojisteni_2026-10.kpc"
    assert outputs["deductions"]["due_date"] == "2026-11-19"
    assert outputs["pension"] == {**outputs["pension"], "count": 2, "total": 250_000}
    assert batch["salaries_by_employee"] == {ids[0]: 3_200_000, ids[1]: 2_500_000}

    deductions = repo.get_file(outputs["deductions"]["key"]).decode()
    assert "2 000000-1000000005 00000001850000 191126" in deductions

    status, body = client.get("/api/batches/2026-10/outputs/salaries/download")
    assert status == 200
    if isinstance(repo, LocalRepository):
        status, data, headers = Client().get(body["url"])
        assert status == 200
        assert data == repo.get_file(outputs["salaries"]["key"])
        assert "15_mzdy_2026-10.kpc" in headers["Content-Disposition"]
    else:
        query = parse_qs(urlparse(body["url"]).query)
        assert "15_mzdy_2026-10" in query["response-content-disposition"][0]

    for group in ("salaries", "deductions", "pension"):
        status, batch = client.post(f"/api/batches/2026-10/outputs/{group}/uploaded")
    assert batch["status"] == "uploaded"

    # Once files are in the bank, the batch cannot be changed.
    status, body = upload(client, sample_abo)
    assert status == 409

    status, batches = client.get("/api/batches")
    assert [b["period"] for b in batches] == ["2026-10"]

    status, entries = client.get("/api/audit")
    actions = {e["action"] for e in entries}
    assert {"batch.import", "batch.generate", "batch.mark_uploaded"} <= actions


def test_unknown_account_blocks_generation(client, sample_abo):
    setup_registry(client)
    text = sample_abo.replace("000000-1000000013 000003200000", "000000-1000000064 000003200000")
    status, body = upload(client, text)
    assert status == 200
    assert body["classification"]["has_errors"]
    status, body = client.post("/api/batches/2026-10/outputs")
    assert status == 409

    status, body = client.put("/api/batches/2026-10/overrides", {"overrides": {"0": "salaries"}})
    assert body["classification"]["has_errors"]
    status, body = client.put("/api/batches/2026-10/overrides", {"overrides": {"0": "deductions"}})
    assert not body["classification"]["has_errors"]


def test_invalid_abo(client):
    content = base64.b64encode(b"garbage").decode()
    status, body = client.put(
        "/api/batches/2026-10/abo", {"filename": "x.abo", "content_base64": content}
    )
    assert status == 400 and "Invalid ABO" in body["error"]


def test_invalid_period(client):
    assert client.get("/api/batches/2026-13")[0] == 400


def test_payslips_flow(client, repo, sample_abo):
    ids = setup_registry(client)
    upload(client, sample_abo)

    status, body = client.post(
        "/api/batches/2026-10/payslips/upload-url", {"content_type": "application/pdf"}
    )
    assert status == 200
    key = body["key"]
    pdf = make_pdf(["Osobni cislo: 1001", "Osobni cislo: 1002", "strana 2"])
    if isinstance(repo, LocalRepository):
        # Upload through the signed URL, without a session cookie or CSRF header.
        status, _ = Client().request("PUT", body["url"], raw=pdf, csrf=False)
        assert status == 200
        assert repo.get_file(key) == pdf
    else:
        repo.put_file(key, pdf, "application/pdf")

    status, body = client.post("/api/batches/2026-10/payslips/process", {"key": key})
    assert status == 200, body
    assert {p["employee_id"] for p in body["payslips"]} == set(ids)
    assert body["summary"]["unmatched"] == []

    status, body = client.get(f"/api/batches/2026-10/payslips/{ids[0]}/preview")
    assert status == 200 and body["url"]

    status, body = client.post("/api/batches/2026-10/payslips/send")
    assert status == 200
    assert sorted(body["queued"]) == sorted(ids)

    deliver_queued(repo)
    if isinstance(repo, LocalRepository):
        assert sorted(m["To"] for m in FakeSMTP.sent) == [
            "Eva Svobodová <eva@example.com>",
            "Jan Novák <jan@example.com>",
        ]

    status, body = client.get("/api/batches/2026-10/payslips")
    assert {p["status"] for p in body["payslips"]} == {"sent"}

    # A second "send all" does not resend; an explicit selection does.
    assert client.post("/api/batches/2026-10/payslips/send")[1]["queued"] == []
    body = client.post("/api/batches/2026-10/payslips/send", {"employee_ids": [ids[0]]})[1]
    assert body["queued"] == [ids[0]]


def test_payslip_key_must_belong_to_period(client, sample_abo):
    setup_registry(client)
    upload(client, sample_abo)
    status, body = client.post(
        "/api/batches/2026-10/payslips/process", {"key": "batches/2026-09/input/x"}
    )
    assert status == 400


def test_mailer_message(employees, settings):
    from dataclasses import replace

    settings = replace(settings, email_sender="mzdy@example.com")
    message = mailer.build_message(settings, employees[0], "2026-10", b"%PDF-1.4")
    assert message["Subject"] == "Výplatní páska 10/2026"
    assert "Jan Novák" in message.get_body(("plain",)).get_content()
    attachment = next(message.iter_attachments())
    assert attachment.get_filename() == "vyplatni-paska-2026-10.pdf"


def test_settings_account_conflicts(client):
    setup_registry(client)
    status, settings = client.get("/api/settings")
    shared = settings["pension_accounts"]

    body = {**settings, "pension_accounts": [{**shared[0], "vs": ""}, shared[1]]}
    status, response = client.put("/api/settings", body)
    assert status == 400 and "variable symbol" in response["error"]

    body = {
        **settings,
        "deduction_accounts": [*settings["deduction_accounts"], {"account": "1000000013/0800"}],
    }
    status, response = client.put("/api/settings", body)
    assert status == 400 and "already belongs to Jan Novák" in response["error"]

    status, response = client.post(
        "/api/employees",
        {
            "number": "1003",
            "last_name": "X",
            "accounts": [{"account": "27-1840220297/0100", "valid_from": "2024-01-01"}],
        },
    )
    assert status == 400 and "configured in settings" in response["error"]


def test_signed_file_urls(repo, tmp_path):
    if not isinstance(repo, LocalRepository):
        pytest.skip("signed /api/files URLs exist only for local storage")
    repo.put_file("batches/2026-10/output/x.kpc", b"data", "text/plain")
    url = repo.download_url("batches/2026-10/output/x.kpc", "x.kpc")
    assert Client().get(url)[:2] == (200, b"data")
    # Tampering with any parameter invalidates the signature.
    assert Client().get(url.replace("x.kpc", "y.kpc"))[0] == 403
    assert Client().request("PUT", url, raw=b"evil", csrf=False)[0] == 403
    assert Client().get("/api/files?key=batches/2026-10/output/x.kpc")[0] == 403
    with pytest.raises(ValueError):
        repo.put_file("../escape", b"x", "text/plain")


def test_send_requires_smtp(client, repo, sample_abo, monkeypatch):
    if not isinstance(repo, LocalRepository):
        pytest.skip("SES needs no extra configuration")
    setup_registry(client)
    upload(client, sample_abo)
    monkeypatch.delenv("SMTP_HOST")
    status, body = client.post("/api/batches/2026-10/payslips/send")
    assert status == 400 and "SMTP" in body["error"]
