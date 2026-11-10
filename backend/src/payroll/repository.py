"""Record and file storage, e-mail queue.

Two backends share the same record layout and domain methods:

* :class:`LocalRepository` (self-hosted, selected by ``DATA_DIR``): SQLite database,
  files on disk, signed ``/api/files`` URLs and an in-process e-mail queue.
* :class:`AwsRepository`: DynamoDB single table, S3 presigned URLs and SQS.

Every record is stored as ``pk`` / ``sk`` plus a JSON ``data`` value; with at most
a few hundred records there is no need for typed attributes or indexes.

    pk                  sk                    data
    USERS               <username>            password hash, role, lockout state
    EMPLOYEES           <employee id>         employee with accounts
    SETTINGS            MAIN                  settings
    BATCHES             <YYYY-MM>             batch state (input file, overrides, outputs)
    PAYSLIPS#<YYYY-MM>  <employee id>         payslip file and delivery status
    AUDIT#<YYYY-MM>     <timestamp>#<uuid>    audit entry
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import queue
import sqlite3
import threading
import time
import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from payroll import auth
from payroll.models import Employee, Settings
from payroll.serde import (
    employee_from_dict,
    employee_to_dict,
    settings_from_dict,
    settings_to_dict,
)

URL_LIFETIME_SECONDS = 300


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Repository(ABC):
    # --- storage primitives --------------------------------------------------

    @abstractmethod
    def _get(self, pk: str, sk: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def _put(self, pk: str, sk: str, data: dict[str, Any]) -> None: ...

    @abstractmethod
    def _delete(self, pk: str, sk: str) -> None: ...

    @abstractmethod
    def _query(self, pk: str, newest_first: bool = False) -> list[dict[str, Any]]: ...

    @abstractmethod
    def put_file(self, key: str, data: bytes, content_type: str) -> None: ...

    @abstractmethod
    def get_file(self, key: str) -> bytes: ...

    @abstractmethod
    def download_url(self, key: str, filename: str, inline: bool = False) -> str: ...

    @abstractmethod
    def upload_url(self, key: str, content_type: str) -> str: ...

    @abstractmethod
    def enqueue_payslip(self, period: str, employee_id: str) -> None: ...

    # --- users ---------------------------------------------------------------

    def get_user(self, username: str) -> dict[str, Any] | None:
        return self._get("USERS", username)

    def put_user(self, user: dict[str, Any]) -> None:
        self._put("USERS", user["username"], user)

    def list_users(self) -> list[dict[str, Any]]:
        return self._query("USERS")

    # --- employees -----------------------------------------------------------

    def list_employees(self) -> list[Employee]:
        employees = [employee_from_dict(d) for d in self._query("EMPLOYEES")]
        return sorted(employees, key=lambda e: (e.last_name, e.first_name))

    def get_employee(self, employee_id: str) -> Employee | None:
        data = self._get("EMPLOYEES", employee_id)
        return employee_from_dict(data) if data else None

    def put_employee(self, employee: Employee) -> None:
        self._put("EMPLOYEES", employee.id, employee_to_dict(employee))

    def delete_employee(self, employee_id: str) -> None:
        self._delete("EMPLOYEES", employee_id)

    # --- settings ------------------------------------------------------------

    def get_settings(self) -> Settings:
        data = self._get("SETTINGS", "MAIN")
        return settings_from_dict(data) if data else Settings()

    def put_settings(self, settings: Settings) -> None:
        self._put("SETTINGS", "MAIN", settings_to_dict(settings))

    # --- batches -------------------------------------------------------------

    def list_batches(self) -> list[dict[str, Any]]:
        return self._query("BATCHES", newest_first=True)

    def get_batch(self, period: str) -> dict[str, Any] | None:
        return self._get("BATCHES", period)

    def put_batch(self, batch: dict[str, Any]) -> None:
        self._put("BATCHES", batch["period"], batch)

    # --- payslips ------------------------------------------------------------

    def list_payslips(self, period: str) -> list[dict[str, Any]]:
        return self._query(f"PAYSLIPS#{period}")

    def get_payslip(self, period: str, employee_id: str) -> dict[str, Any] | None:
        return self._get(f"PAYSLIPS#{period}", employee_id)

    def put_payslip(self, period: str, payslip: dict[str, Any]) -> None:
        self._put(f"PAYSLIPS#{period}", payslip["employee_id"], payslip)

    def delete_payslips(self, period: str) -> None:
        for payslip in self.list_payslips(period):
            self._delete(f"PAYSLIPS#{period}", payslip["employee_id"])

    # --- audit ---------------------------------------------------------------

    def audit(self, user: str, action: str, **detail: Any) -> None:
        now = utcnow()
        entry = {"time": now, "user": user, "action": action, "detail": detail}
        self._put(f"AUDIT#{now[:7]}", f"{now}#{uuid.uuid4().hex[:8]}", entry)

    def list_audit(self, month: str) -> list[dict[str, Any]]:
        return self._query(f"AUDIT#{month}", newest_first=True)


class LocalRepository(Repository):
    """SQLite database and files under ``data_dir``, for a single self-hosted server."""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.files_dir = self.data_dir / "files"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.data_dir / "payroll.db", check_same_thread=False)
        with self._lock, self._db:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS records ("
                " pk TEXT NOT NULL, sk TEXT NOT NULL, data TEXT NOT NULL,"
                " PRIMARY KEY (pk, sk))"
            )
        # Payslip e-mails waiting to be sent; consumed by the server's mail worker.
        self.mail_queue: queue.Queue[tuple[str, str]] = queue.Queue()

    def _get(self, pk: str, sk: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT data FROM records WHERE pk = ? AND sk = ?", (pk, sk)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def _put(self, pk: str, sk: str, data: dict[str, Any]) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO records (pk, sk, data) VALUES (?, ?, ?)",
                (pk, sk, json.dumps(data)),
            )

    def _delete(self, pk: str, sk: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM records WHERE pk = ? AND sk = ?", (pk, sk))

    def _query(self, pk: str, newest_first: bool = False) -> list[dict[str, Any]]:
        order = "DESC" if newest_first else "ASC"
        with self._lock:
            rows = self._db.execute(
                f"SELECT data FROM records WHERE pk = ? ORDER BY sk {order}", (pk,)
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def _path(self, key: str) -> Path:
        path = (self.files_dir / key).resolve()
        if self.files_dir.resolve() not in path.parents:
            raise ValueError(f"Invalid file key {key!r}")
        return path

    def put_file(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    def get_file(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    # Signed URLs play the role of S3 presigned URLs: the browser downloads and
    # uploads files through /api/files without a session cookie or CSRF header.

    @staticmethod
    def _signature(params: dict[str, str]) -> str:
        message = "\n".join(f"{k}={params[k]}" for k in sorted(params))
        secret = auth.signing_secret().encode()
        return hmac.new(secret, message.encode(), hashlib.sha256).hexdigest()

    def _signed_url(self, **params: str) -> str:
        params["expires"] = str(int(time.time()) + URL_LIFETIME_SECONDS)
        params["signature"] = self._signature(params)
        return "/api/files?" + urlencode(params)

    def download_url(self, key: str, filename: str, inline: bool = False) -> str:
        disposition = "inline" if inline else "attachment"
        return self._signed_url(method="GET", key=key, filename=filename, disposition=disposition)

    def upload_url(self, key: str, content_type: str) -> str:
        return self._signed_url(method="PUT", key=key, content_type=content_type)

    def verify_url(self, method: str, query: dict[str, str]) -> dict[str, str] | None:
        """Return the URL's parameters if its signature is valid and unexpired."""
        params = dict(query)
        signature = params.pop("signature", "")
        if params.get("method") != method or not params.get("expires", "").isdigit():
            return None
        if int(params["expires"]) < time.time():
            return None
        if not hmac.compare_digest(signature, self._signature(params)):
            return None
        return params

    def enqueue_payslip(self, period: str, employee_id: str) -> None:
        self.mail_queue.put((period, employee_id))


class AwsRepository(Repository):
    """DynamoDB table, S3 bucket and SQS queue named by environment variables."""

    def __init__(self, table_name: str | None = None, bucket: str | None = None) -> None:
        import boto3

        self.table = boto3.resource("dynamodb").Table(table_name or os.environ["TABLE_NAME"])
        self.bucket = bucket or os.environ["BUCKET_NAME"]
        self.s3 = boto3.client("s3")
        self.sqs = boto3.client("sqs")

    def _get(self, pk: str, sk: str) -> dict[str, Any] | None:
        item = self.table.get_item(Key={"pk": pk, "sk": sk}).get("Item")
        return json.loads(item["data"]) if item else None

    def _put(self, pk: str, sk: str, data: dict[str, Any]) -> None:
        self.table.put_item(Item={"pk": pk, "sk": sk, "data": json.dumps(data)})

    def _delete(self, pk: str, sk: str) -> None:
        self.table.delete_item(Key={"pk": pk, "sk": sk})

    def _query(self, pk: str, newest_first: bool = False) -> list[dict[str, Any]]:
        from boto3.dynamodb.conditions import Key

        kwargs: dict[str, Any] = {
            "KeyConditionExpression": Key("pk").eq(pk),
            "ScanIndexForward": not newest_first,
        }
        items: list[dict[str, Any]] = []
        while True:
            response = self.table.query(**kwargs)
            items.extend(json.loads(i["data"]) for i in response["Items"])
            if "LastEvaluatedKey" not in response:
                return items
            kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    def put_file(self, key: str, data: bytes, content_type: str) -> None:
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def get_file(self, key: str) -> bytes:
        return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def download_url(self, key: str, filename: str, inline: bool = False) -> str:
        disposition = "inline" if inline else "attachment"
        return self.s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ResponseContentDisposition": f'{disposition}; filename="{filename}"',
            },
            ExpiresIn=URL_LIFETIME_SECONDS,
        )

    def upload_url(self, key: str, content_type: str) -> str:
        return self.s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=URL_LIFETIME_SECONDS,
        )

    def enqueue_payslip(self, period: str, employee_id: str) -> None:
        self.sqs.send_message(
            QueueUrl=os.environ["QUEUE_URL"],
            MessageBody=json.dumps({"period": period, "employee_id": employee_id}),
        )


def create_repository() -> Repository:
    """Local storage when ``DATA_DIR`` is set, AWS otherwise."""
    if os.environ.get("DATA_DIR"):
        return LocalRepository(os.environ["DATA_DIR"])
    return AwsRepository()
