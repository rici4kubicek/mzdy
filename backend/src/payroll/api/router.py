"""Minimal router for API Gateway HTTP API (payload format 2.0) events."""

from __future__ import annotations

import base64
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from payroll import auth

logger = logging.getLogger(__name__)

SESSION_COOKIE = "session"
# Required on state-changing requests; a cross-site form cannot set custom headers.
CSRF_HEADER = "x-requested-with"


class HttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class Request:
    method: str
    path: str
    params: dict[str, str]
    query: dict[str, str]
    headers: dict[str, str]
    cookies: dict[str, str]
    body: bytes
    user: dict[str, Any] | None = None

    def json(self) -> dict[str, Any]:
        if not self.body:
            return {}
        try:
            data = json.loads(self.body)
        except ValueError:
            raise HttpError(400, "Invalid JSON body") from None
        if not isinstance(data, dict):
            raise HttpError(400, "JSON body must be an object")
        return data

    @property
    def username(self) -> str:
        return self.user["sub"] if self.user else "anonymous"


@dataclass
class Response:
    body: Any = None
    status: int = 200
    cookies: list[str] = field(default_factory=list)


Handler = Callable[[Request], Any]


@dataclass
class _Route:
    method: str
    regex: re.Pattern[str]
    handler: Handler
    public: bool


def _parse_cookies(event: dict[str, Any]) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for raw in event.get("cookies") or []:
        name, _, value = raw.partition("=")
        cookies[name.strip()] = value.strip()
    return cookies


def session_cookie(token: str, max_age: int) -> str:
    return (
        f"{SESSION_COOKIE}={token}; Path=/api; Max-Age={max_age}; HttpOnly; Secure; SameSite=Strict"
    )


class Router:
    def __init__(self) -> None:
        self.routes: list[_Route] = []

    def route(
        self, method: str, pattern: str, public: bool = False
    ) -> Callable[[Handler], Handler]:
        regex = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern) + "$")

        def decorator(handler: Handler) -> Handler:
            self.routes.append(_Route(method, regex, handler, public))
            return handler

        return decorator

    def _build_request(self, event: dict[str, Any]) -> Request:
        body = event.get("body") or ""
        raw = base64.b64decode(body) if event.get("isBase64Encoded") else body.encode()
        return Request(
            method=event["requestContext"]["http"]["method"],
            path=event.get("rawPath", "/"),
            params={},
            query=event.get("queryStringParameters") or {},
            headers={k.lower(): v for k, v in (event.get("headers") or {}).items()},
            cookies=_parse_cookies(event),
            body=raw,
        )

    def _dispatch(self, request: Request) -> Response:
        path_matched = False
        for route in self.routes:
            match = route.regex.match(request.path)
            if not match:
                continue
            path_matched = True
            if route.method != request.method:
                continue
            request.params = match.groupdict()
            if not route.public:
                request.user = auth.verify_token(request.cookies.get(SESSION_COOKIE, ""))
                if request.user is None:
                    raise HttpError(401, "Not logged in")
            if request.method != "GET" and request.headers.get(CSRF_HEADER) != "payroll":
                raise HttpError(403, "Missing X-Requested-With header")
            result = route.handler(request)
            return result if isinstance(result, Response) else Response(result)
        raise HttpError(405 if path_matched else 404, "Not found")

    def handle(self, event: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._dispatch(self._build_request(event))
        except HttpError as exc:
            response = Response({"error": exc.message}, exc.status)
        except Exception:
            logger.exception("Unhandled error")
            response = Response({"error": "Internal server error"}, 500)
        return {
            "statusCode": response.status,
            "headers": {
                "Content-Type": "application/json",
                "Cache-Control": "no-store",
            },
            "cookies": response.cookies,
            "body": json.dumps(response.body, ensure_ascii=False),
        }
