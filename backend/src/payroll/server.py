"""Self-hosted HTTP server: API, frontend and the payslip e-mail worker in one process.

    DATA_DIR=/data python -m payroll.server --host 0.0.0.0 --port 8000 --static ../frontend/dist

Configuration (environment):

* ``DATA_DIR`` (required): SQLite database, uploaded/generated files, signing key.
* ``SMTP_*``: outgoing mail server, see :mod:`payroll.mailer`.
* ``COOKIE_SECURE=false``: allow login over plain HTTP (only inside a trusted LAN).
* ``INITIAL_ADMIN_PASSWORD``: creates user ``admin`` on first start, when no user exists.
"""

from __future__ import annotations

import argparse
import base64
import logging
import mimetypes
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from payroll.repository import LocalRepository

logger = logging.getLogger("payroll.server")

MAX_BODY_BYTES = 50 * 1024 * 1024
MAIL_ATTEMPTS = 3

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
}


def make_handler(static_dir: Path) -> type[BaseHTTPRequestHandler]:
    static_root = static_dir.resolve()

    class Handler(BaseHTTPRequestHandler):
        server_version = "payroll"
        sys_version = ""

        def _send(self, status: int, headers: dict[str, str], body: bytes) -> None:
            self.send_response(status)
            for key, value in {**_SECURITY_HEADERS, **headers}.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _api(self) -> None:
            from payroll.api import app

            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                self._send(413, {"Content-Type": "text/plain"}, b"Request too large")
                return
            body = self.rfile.read(length) if length else b""
            url = urlsplit(self.path)
            cookies = [c.strip() for c in (self.headers.get("Cookie") or "").split(";")]
            event = {
                "rawPath": url.path,
                "requestContext": {"http": {"method": self.command}},
                "headers": dict(self.headers.items()),
                "queryStringParameters": dict(parse_qsl(url.query)) or None,
                "cookies": [c for c in cookies if c],
                "body": base64.b64encode(body).decode(),
                "isBase64Encoded": True,
            }
            response = app.handler(event)
            payload = response["body"]
            data = (
                base64.b64decode(payload) if response.get("isBase64Encoded") else payload.encode()
            )
            self.send_response(response["statusCode"])
            for cookie in response.get("cookies", []):
                self.send_header("Set-Cookie", cookie)
            self._finish(response["headers"], data)

        def _finish(self, headers: dict[str, str], body: bytes) -> None:
            for key, value in {**_SECURITY_HEADERS, **headers}.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _static(self) -> None:
            path = urlsplit(self.path).path
            file = (static_root / path.lstrip("/")).resolve()
            if static_root not in file.parents or not file.is_file():
                # Client-side routes such as /batches/2026-10 are served by index.html.
                file = static_root / "index.html"
            if not file.is_file():
                self._send(404, {"Content-Type": "text/plain"}, b"Frontend is not built")
                return
            content_type = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
            cache = "no-cache" if file.name == "index.html" else "public, max-age=31536000"
            self._send(
                200, {"Content-Type": content_type, "Cache-Control": cache}, file.read_bytes()
            )

        def _dispatch(self) -> None:
            if self.path.startswith("/api/"):
                self._api()
            elif self.command in ("GET", "HEAD"):
                self._static()
            else:
                self._send(405, {"Content-Type": "text/plain"}, b"Method not allowed")

        do_GET = do_HEAD = do_POST = do_PUT = do_DELETE = _dispatch

        def log_message(self, format: str, *args: object) -> None:
            # Log the path without the query string; it may contain signed URL parameters.
            logger.info("%s %s %s", self.command, urlsplit(self.path).path, args[1] if args else "")

    return Handler


def mail_worker(repo: LocalRepository) -> None:
    """Send queued payslips, retrying a failed delivery a few times."""
    from payroll import mailer

    while True:
        period, employee_id = repo.mail_queue.get()
        for attempt in range(1, MAIL_ATTEMPTS + 1):
            try:
                mailer.send_one(period, employee_id)
                break
            except Exception:
                logger.exception(
                    "Sending payslip %s/%s failed (attempt %d)", period, employee_id, attempt
                )
                if attempt < MAIL_ATTEMPTS:
                    time.sleep(10 * attempt)


def ensure_admin(repo: LocalRepository) -> None:
    password = os.environ.get("INITIAL_ADMIN_PASSWORD")
    if password and not repo.list_users():
        from payroll.cli import create_user

        create_user(repo, "admin", "admin", password)
        logger.info("Created user 'admin'")


def main() -> None:
    parser = argparse.ArgumentParser(prog="payroll.server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--static", default=os.environ.get("STATIC_DIR", "../frontend/dist"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if not os.environ.get("DATA_DIR"):
        raise SystemExit("DATA_DIR must be set")
    from payroll import mailer
    from payroll.api import app

    repo = LocalRepository(os.environ["DATA_DIR"])
    # The API and the mailer share one repository (and so one e-mail queue).
    app._repo = repo
    mailer._repo = repo
    ensure_admin(repo)
    threading.Thread(target=mail_worker, args=(repo,), daemon=True, name="mail").start()
    if not os.environ.get("SMTP_HOST"):
        logger.warning("SMTP_HOST is not set; payslips cannot be e-mailed")

    server = ThreadingHTTPServer((args.host, args.port), make_handler(Path(args.static)))
    logger.info("Listening on http://%s:%d", args.host, args.port)
    server.serve_forever()


if __name__ == "__main__":
    main()
