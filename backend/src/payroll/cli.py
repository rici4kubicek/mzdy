"""Administration commands.

TABLE_NAME=... BUCKET_NAME=... python -m payroll.cli create-user alice --role admin
"""

from __future__ import annotations

import argparse
import getpass
import sys

from payroll import auth
from payroll.repository import Repository, utcnow


def create_user(repo: Repository, username: str, role: str, password: str) -> None:
    if len(password) < 12:
        raise SystemExit("Password must be at least 12 characters long")
    repo.put_user(
        {
            "username": username.strip().lower(),
            "role": role,
            "password_hash": auth.hash_password(password),
            "failed_attempts": 0,
            "locked_until": None,
            "created_at": utcnow(),
        }
    )
    repo.audit("cli", "user.create", username=username, role=role)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="payroll")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-user", help="create a user or reset their password")
    create.add_argument("username")
    create.add_argument("--role", choices=["admin", "user"], default="user")
    args = parser.parse_args(argv)

    if args.command == "create-user":
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Repeat password: "):
            sys.exit("Passwords do not match")
        create_user(Repository(), args.username, args.role, password)
        print(f"User {args.username} saved.")


if __name__ == "__main__":
    main()
