"""ARC-02: snapshot the configuration a run actually used.

There are close to a hundred settings fields; a run report that names only a
few of them cannot be reproduced. Every benchmark run and every audit records
this snapshot (secrets removed) and its hash, so two runs can be checked for
identical settings before they are compared.
"""
from __future__ import annotations

import re
from typing import Any

from app.config import settings
from app.services.pipeline_cache import stable_key

_SECRET_NAME = re.compile(r"(_TOKEN$|^TOKEN$|SECRET|PASSWORD|API_KEY|_KEY$|CREDENTIAL|DSN|CONNECTION_STRING)", re.IGNORECASE)
_SECRET_VALUE = re.compile(r"(dapi[0-9a-f]{16,}|AIza[0-9A-Za-z_-]{20,}|sk-[A-Za-z0-9]{16,})")


def _clean(name: str, value: Any) -> Any:
    if _SECRET_NAME.search(name):
        return "<set>" if value else "<unset>"
    if isinstance(value, str) and _SECRET_VALUE.search(value):
        return "<redacted>"
    if isinstance(value, (list, tuple)):
        return [_clean(name, item) for item in value]
    if isinstance(value, dict):
        return {key: _clean(f"{name}.{key}", item) for key, item in value.items()}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def effective_config() -> dict[str, Any]:
    """Every settings field with secrets replaced by <set>/<unset>, plus its hash."""
    raw = settings.model_dump() if hasattr(settings, "model_dump") else dict(vars(settings))
    values = {name: _clean(name, value) for name, value in sorted(raw.items()) if not name.startswith("_")}
    return {"sha256": stable_key(values), "settings": values}
