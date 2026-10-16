"""Run the whole application locally against an in-memory AWS emulation (moto).

    .venv/bin/python devserver.py   # http://localhost:8000, "admin" / "admin-password"

Serves the built frontend from ../frontend/dist and the API under /api. E-mails
are not delivered; the mailer runs against the emulated SES. Data is lost on exit.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

MOTO_PORT = 5055
DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

os.environ.update(
    AWS_DEFAULT_REGION="eu-central-1",
    AWS_ACCESS_KEY_ID="local",
    AWS_SECRET_ACCESS_KEY="local",
    AWS_ENDPOINT_URL=f"http://localhost:{MOTO_PORT}",
    TABLE_NAME="payroll",
    BUCKET_NAME="payroll-data",
    JWT_SECRET="local-development-secret-not-for-production",
)

import boto3  # noqa: E402
from moto.server import ThreadedMotoServer  # noqa: E402


def setup_aws() -> None:
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
    s3 = boto3.client("s3")
    s3.create_bucket(
        Bucket="payroll-data", CreateBucketConfiguration={"LocationConstraint": "eu-central-1"}
    )
    s3.put_bucket_cors(
        Bucket="payroll-data",
        CORSConfiguration={
            "CORSRules": [
                {"AllowedMethods": ["PUT"], "AllowedOrigins": ["*"], "AllowedHeaders": ["*"]}
            ]
        },
    )
    queue = boto3.client("sqs").create_queue(QueueName="payslips")
    os.environ["QUEUE_URL"] = queue["QueueUrl"]


def run_mailer() -> None:
    from payroll import mailer

    sqs = boto3.client("sqs")
    while True:
        response = sqs.receive_message(
            QueueUrl=os.environ["QUEUE_URL"], MaxNumberOfMessages=10, WaitTimeSeconds=1
        )
        messages = response.get("Messages", [])
        if not messages:
            time.sleep(1)
            continue
        records = [{"messageId": m["MessageId"], "body": m["Body"]} for m in messages]
        mailer.handler({"Records": records})
        for m in messages:
            sqs.delete_message(QueueUrl=os.environ["QUEUE_URL"], ReceiptHandle=m["ReceiptHandle"])


class Handler(BaseHTTPRequestHandler):
    def _api(self) -> None:
        from payroll.api import app

        url = urlsplit(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        cookies = [c.strip() for c in (self.headers.get("Cookie") or "").split(";") if c.strip()]
        event = {
            "rawPath": url.path,
            "requestContext": {"http": {"method": self.command}},
            "headers": dict(self.headers.items()),
            "queryStringParameters": dict(parse_qsl(url.query)) or None,
            "cookies": cookies,
            "body": base64.b64encode(body).decode(),
            "isBase64Encoded": True,
        }
        response = app.handler(event)
        self.send_response(response["statusCode"])
        for key, value in response["headers"].items():
            self.send_header(key, value)
        for cookie in response.get("cookies", []):
            self.send_header("Set-Cookie", cookie)
        payload = response["body"].encode()
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _static(self) -> None:
        path = urlsplit(self.path).path
        file = (DIST / path.lstrip("/")).resolve()
        if "." not in path.rsplit("/", 1)[-1] or not file.is_file() or DIST not in file.parents:
            file = DIST / "index.html"
        data = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(file.name)[0] or "text/plain")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _dispatch(self) -> None:
        if self.path.startswith("/api/"):
            self._api()
        else:
            self._static()

    do_GET = do_POST = do_PUT = do_DELETE = _dispatch

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    server = ThreadedMotoServer(port=MOTO_PORT, verbose=False)
    server.start()
    setup_aws()

    from payroll.cli import create_user
    from payroll.repository import Repository

    create_user(Repository(), "admin", "admin", "admin-password")
    boto3.client("ses").verify_email_identity(EmailAddress="mzdy@example.com")
    threading.Thread(target=run_mailer, daemon=True).start()

    print(json.dumps({"url": f"http://localhost:{args.port}", "user": "admin"}))
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
