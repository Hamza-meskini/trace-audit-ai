"""Who is making a request, and whether that identity is verified.

On Databricks Apps the platform proxy authenticates every request with SSO and
sets ``X-Forwarded-User`` / ``X-Forwarded-Email`` itself; a client cannot reach
the app without passing through it, so those headers are trustworthy there.
Anywhere else (local development, tests, a bare uvicorn) any client can send
them, so they are ignored and the identity is the self-reported display name,
marked unverified.

``TRUST_FORWARDED_IDENTITY``: ``auto`` (default) trusts the headers only when
the Databricks Apps runtime variables are present; ``true``/``false`` force it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from starlette.requests import Request

from app.config import settings

SSO_SOURCE = "databricks_sso"
CLIENT_SOURCE = "client_asserted"
APPS_RUNTIME_VARS = ("DATABRICKS_APP_PORT", "DATABRICKS_APP_NAME", "DATABRICKS_APP_URL")


@dataclass(frozen=True)
class Identity:
    user: Optional[str]  # stable proxy identity (X-Forwarded-User)
    email: Optional[str]
    display: Optional[str]  # preferred username, else email, else user
    source: str
    verified: bool


def forwarded_identity_trusted() -> bool:
    mode = str(getattr(settings, "TRUST_FORWARDED_IDENTITY", "auto") or "auto").strip().lower()
    if mode in ("true", "1", "yes"):
        return True
    if mode in ("false", "0", "no"):
        return False
    return any(os.environ.get(name) for name in APPS_RUNTIME_VARS)


def request_identity(request: Request) -> Identity:
    """The SSO identity when it can be trusted; otherwise an unverified one."""
    if forwarded_identity_trusted():
        user = (
            request.headers.get("X-Forwarded-User")
            or request.headers.get("X-Databricks-User")
            or request.headers.get("X-Forwarded-Email")
            or ""
        ).strip()
        if user:
            email = (request.headers.get("X-Forwarded-Email") or "").strip() or None
            preferred = (request.headers.get("X-Forwarded-Preferred-Username") or "").strip()
            return Identity(user=user, email=email, display=preferred or email or user,
                            source=SSO_SOURCE, verified=True)
    return Identity(user=None, email=None, display=None, source=CLIENT_SOURCE, verified=False)
