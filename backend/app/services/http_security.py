"""HTTP surface hardening: security headers, cross-site write guard, body cap.

The deployed app is same-origin (the SPA and API share the Databricks Apps
host) and the frontend never sends credentials cross-origin. The Databricks
Apps proxy authenticates with its own cookies, so a page on another site could
still submit a simple form POST that rides on them. ``SecurityMiddleware``
rejects state-changing requests whose ``Origin`` belongs to neither this host
nor an allowed development origin. Requests without ``Origin`` (curl, server
to server, tests) are unaffected.
"""

from __future__ import annotations

import re
from typing import Iterable
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Message, Receive, Scope, Send

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"same-origin"),
    (b"x-frame-options", b"SAMEORIGIN"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
)


def _header(scope: Scope, name: bytes) -> str:
    for key, value in scope.get("headers") or []:
        if key.lower() == name:
            return value.decode("latin-1")
    return ""


def _origin_key(value: str) -> str:
    parts = urlsplit(value.strip())
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


def origin_allowed(
    origin: str,
    *,
    host: str,
    forwarded_host: str,
    allowed_origins: Iterable[str],
    allowed_origin_regex: str | None = None,
) -> bool:
    """True when ``origin`` is this server's own host or an allowed origin."""
    key = _origin_key(origin)
    if not key:
        return False
    if key in {_origin_key(item) for item in allowed_origins}:
        return True
    if allowed_origin_regex and re.fullmatch(allowed_origin_regex, key):
        return True
    origin_host = urlsplit(key).netloc
    own_hosts = {h.strip().lower() for h in (host, *forwarded_host.split(",")) if h.strip()}
    return origin_host in own_hosts


class SecurityMiddleware:
    """Pure ASGI middleware (streams responses untouched)."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        allowed_origins: Iterable[str],
        allowed_origin_regex: str | None = None,
        max_body_bytes: int,
    ) -> None:
        self.app = app
        self.allowed_origins = list(allowed_origins)
        self.allowed_origin_regex = allowed_origin_regex
        self.max_body_bytes = int(max_body_bytes)

    async def _reject(self, send: Send, status: int, detail: str) -> None:
        body = ('{"detail":"%s"}' % detail).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                        *SECURITY_HEADERS],
        })
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET").upper()
        if method not in SAFE_METHODS:
            origin = _header(scope, b"origin")
            fetch_site = _header(scope, b"sec-fetch-site").lower()
            if origin and fetch_site not in ("same-origin", "none") and not origin_allowed(
                origin,
                host=_header(scope, b"host"),
                forwarded_host=_header(scope, b"x-forwarded-host"),
                allowed_origins=self.allowed_origins,
                allowed_origin_regex=self.allowed_origin_regex,
            ):
                await self._reject(send, 403, "Cross-site request blocked")
                return

            length = _header(scope, b"content-length")
            if length.isdigit() and int(length) > self.max_body_bytes:
                await self._reject(send, 413, "Request body too large")
                return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                present = {key.lower() for key, _ in headers}
                headers.extend((k, v) for k, v in SECURITY_HEADERS if k not in present)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
