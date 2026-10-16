"""DynamoDB single-table storage and S3 file storage.

Every record is stored as ``pk`` / ``sk`` plus a JSON ``data`` attribute; with at
most a few hundred records there is no need for typed attributes or indexes.

    pk                  sk                    data
    USERS               <username>            password hash, role, lockout state
    EMPLOYEES           <employee id>         employee with accounts
    SETTINGS            MAIN                  settings
    BATCHES             <YYYY-MM>             batch state (input file, overrides, outputs)
    PAYSLIPS#<YYYY-MM>  <employee id>         payslip file and delivery status
    AUDIT#<YYYY-MM>     <timestamp>#<uuid>    audit entry
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

from payroll.models import Employee, Settings
from payroll.serde import (
    employee_from_dict,
    employee_to_dict,
    settings_from_dict,
    settings_to_dict,
)


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Repository:
    def __init__(self, table_name: str | None = None, bucket: str | None = None) -> None:
        self.table = boto3.resource("dynamodb").Table(table_name or os.environ["TABLE_NAME"])
        self.bucket = bucket or os.environ["BUCKET_NAME"]
        self.s3 = boto3.client("s3")

    # --- generic -------------------------------------------------------------

    def _get(self, pk: str, sk: str) -> dict[str, Any] | None:
        item = self.table.get_item(Key={"pk": pk, "sk": sk}).get("Item")
        return json.loads(item["data"]) if item else None

    def _put(self, pk: str, sk: str, data: dict[str, Any]) -> None:
        self.table.put_item(Item={"pk": pk, "sk": sk, "data": json.dumps(data)})

    def _delete(self, pk: str, sk: str) -> None:
        self.table.delete_item(Key={"pk": pk, "sk": sk})

    def _query(self, pk: str, newest_first: bool = False) -> list[dict[str, Any]]:
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

    # --- users ---------------------------------------------------------------

    def get_user(self, username: str) -> dict[str, Any] | None:
        return self._get("USERS", username)

    def put_user(self, user: dict[str, Any]) -> None:
        self._put("USERS", user["username"], user)

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

    # --- files ---------------------------------------------------------------

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
            ExpiresIn=300,
        )

    def upload_url(self, key: str, content_type: str) -> str:
        return self.s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=300,
        )
