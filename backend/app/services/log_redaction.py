"""Keep credentials and customer document text out of logs.

Two mechanisms:

* ``redact_secrets`` masks credential-shaped substrings (API keys, bearer
  tokens, ``key=`` query parameters, user:token URLs, client secrets). A
  logging filter applies it to every record, whichever logger emitted it.
* ``body_summary`` replaces provider response bodies and raw model output --
  which can quote customer documents -- with their length and a short hash.
  Set ``LOG_PROVIDER_BODIES=true`` (local debugging only) to log a
  secret-redacted excerpt instead.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

REDACTED = "[REDACTED]"

# Ordered: specific token shapes first, then generic key/value forms.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Google API keys (Gemini / Vertex).
    (re.compile(r"AIza[0-9A-Za-z_\-]{20,}"), REDACTED),
    # Databricks personal access tokens.
    (re.compile(r"\bdapi[0-9a-fA-F]{16,}(?:-\d+)?"), REDACTED),
    # OpenAI / OpenRouter / generic "sk-" secrets.
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}"), REDACTED),
    # Groq and Hugging Face tokens.
    (re.compile(r"\bgsk_[A-Za-z0-9]{12,}"), REDACTED),
    (re.compile(r"\bhf_[A-Za-z0-9]{12,}"), REDACTED),
    # Authorization headers.
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]{8,}"), r"\1 " + REDACTED),
    # user:token@host credentials inside URLs (e.g. databricks://token:...@host).
    (re.compile(r"(?i)(://[^:/@\s]*:)[^@\s]+@"), r"\1" + REDACTED + "@"),
    # Secret-bearing query parameters or key=value pairs.
    (
        re.compile(
            r"(?i)\b(key|api[_-]?key|access[_-]?token|token|client[_-]?secret|secret|password|x-goog-api-key)"
            r"(\s*[=:]\s*[\"']?)([^\s&\"',;}]+)"
        ),
        r"\1\2" + REDACTED,
    ),
)


def redact_secrets(value: Any) -> str:
    """Return ``value`` as text with credential-shaped substrings masked."""
    text = value if isinstance(value, str) else str(value)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def body_summary(value: Any, limit: int = 300) -> str:
    """Describe a provider body or model output without logging its content."""
    text = "" if value is None else (value if isinstance(value, str) else str(value))
    try:
        from app.config import settings

        verbose = bool(getattr(settings, "LOG_PROVIDER_BODIES", False))
    except Exception:  # pragma: no cover - config import failure
        verbose = False
    if verbose:
        return redact_secrets(text[:limit])
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"<{len(text)} chars, sha256:{digest}>"


def exception_summary(exc: BaseException) -> str:
    """Exception type plus a secret-redacted message (httpx errors embed URLs)."""
    return f"{type(exc).__name__}: {redact_secrets(exc)}"


class RedactingFilter(logging.Filter):
    """Rewrite each record's final message with secrets masked."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = redact_secrets(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        if record.exc_info and not record.exc_text:
            formatter = logging.Formatter()
            record.exc_text = redact_secrets(formatter.formatException(record.exc_info))
        elif record.exc_text:
            record.exc_text = redact_secrets(record.exc_text)
        return True


_FILTER = RedactingFilter()
_installed = False


def install_log_redaction() -> None:
    """Attach the redaction filter to every record created in this process.

    A record factory reaches records from all loggers, including handlers that
    uvicorn or MLflow add after startup; logger-level filters would not.
    """
    global _installed
    if _installed:
        return
    _installed = True
    previous_factory = logging.getLogRecordFactory()

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = previous_factory(*args, **kwargs)
        _FILTER.filter(record)
        return record

    logging.setLogRecordFactory(factory)
