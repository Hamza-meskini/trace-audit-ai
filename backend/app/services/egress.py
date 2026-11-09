"""Outbound-host allow-list for every HTTP call the app makes itself.

Customer text leaves the process only towards hosts the owner has approved.
By default that is the Databricks workspace (``DATABRICKS_HOST`` or the host of
``DATABRICKS_BASE_URL``): Model Serving, the AI Gateway and the reranker
endpoint all live there. ``EGRESS_ALLOWED_HOSTS`` adds hosts (exact names,
``*.suffix`` wildcards, or ``*`` to disable the check).

Enforcement is an httpx request hook installed by :func:`async_client`, so it
applies to the final URL of every request on that client, including
redirects. Synchronous one-off calls use :func:`check_egress` directly.
Databricks SDK calls (SQL warehouse, Files API, ai_parse_document) go to the
workspace host through the SDK and are not routed here.

Note: Gemini embeddings (``generativelanguage.googleapis.com``) are blocked by
the default list; retrieval then uses BM25 only. Add that host to
``EGRESS_ALLOWED_HOSTS`` to keep the embedding path every Nova benchmark used.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Optional
from urllib.parse import urlsplit

import httpx

from app.config import settings

logger = logging.getLogger("traceaudit.egress")


class EgressBlockedError(RuntimeError):
    """An outbound request targeted a host outside the allow-list."""

    def __init__(self, host: str):
        self.host = host
        super().__init__(
            f"Outbound request to {host!r} blocked by the egress allow-list. "
            "Add the host to EGRESS_ALLOWED_HOSTS if this provider is approved."
        )


def _host_of(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    parsed = urlsplit(value if "://" in value else f"https://{value}")
    return (parsed.hostname or "").lower()


def allowed_hosts() -> list[str]:
    """Configured allow-list: the Databricks workspace plus EGRESS_ALLOWED_HOSTS."""
    hosts: list[str] = []
    for candidate in (settings.effective_databricks_host, _host_of(settings.effective_databricks_base_url)):
        host = _host_of(candidate)
        if host and host not in hosts:
            hosts.append(host)
    for entry in settings.EGRESS_ALLOWED_HOSTS or []:
        entry = str(entry).strip().lower()
        if entry and entry not in hosts:
            hosts.append(entry if entry.startswith("*") else _host_of(entry) or entry)
    return hosts


def host_allowed(host: str, hosts: Optional[Iterable[str]] = None) -> bool:
    if not settings.EGRESS_ENFORCED:
        return True
    host = (host or "").lower().rstrip(".")
    if not host:
        return False
    for entry in allowed_hosts() if hosts is None else hosts:
        if entry == "*":
            return True
        if entry.startswith("*."):
            suffix = entry[1:]
            if host.endswith(suffix) and host != suffix.lstrip("."):
                return True
        elif host == entry:
            return True
    return False


def url_allowed(url: str) -> bool:
    return host_allowed(_host_of(url))


_reported: set[str] = set()


def check_egress(url: str) -> None:
    """Raise :class:`EgressBlockedError` unless ``url``'s host is allowed."""
    host = _host_of(str(url))
    if host_allowed(host):
        return
    if host not in _reported:
        _reported.add(host)
        logger.warning("Blocked outbound request to %s (not in the egress allow-list)", host)
    raise EgressBlockedError(host)


async def _request_hook(request: httpx.Request) -> None:
    check_egress(str(request.url))


def async_client(**kwargs: Any) -> httpx.AsyncClient:
    """``httpx.AsyncClient`` that refuses requests to hosts outside the allow-list."""
    hooks = dict(kwargs.pop("event_hooks", None) or {})
    hooks["request"] = [_request_hook, *hooks.get("request", [])]
    return httpx.AsyncClient(event_hooks=hooks, **kwargs)


def model_configuration_problems() -> list[str]:
    """Configured models that would be refused or would leave the allow-list."""
    from app.services.llm_client import ensure_model_allowed, resolve_llm_provider

    problems: list[str] = []
    configured = {
        "LLM_MODEL": settings.LLM_MODEL,
        "DATABRICKS_MODEL": settings.DATABRICKS_MODEL,
        "DATABRICKS_VISION_MODEL": settings.DATABRICKS_VISION_MODEL,
        "ATOMIC_DECOMPOSITION_MODEL": settings.ATOMIC_DECOMPOSITION_MODEL,
        "ATOMIC_DECOMPOSITION_FALLBACK_MODEL": settings.ATOMIC_DECOMPOSITION_FALLBACK_MODEL,
        "SECONDARY_ADJUDICATOR_MODEL": settings.SECONDARY_ADJUDICATOR_MODEL,
        "CITATION_GROUNDING_MODEL": settings.CITATION_GROUNDING_MODEL,
    }
    databricks_reachable = bool(settings.effective_databricks_base_url) and url_allowed(
        settings.effective_databricks_base_url
    )
    for name, model in configured.items():
        if not model:
            continue
        try:
            ensure_model_allowed(model)
        except ValueError as exc:
            problems.append(f"{name}: {exc}")
            continue
        if resolve_llm_provider(model) == "databricks" and settings.effective_databricks_base_url and not databricks_reachable:
            problems.append(f"{name}: Databricks endpoint host is outside the egress allow-list")
    return problems


def log_startup_report() -> None:
    """Log the effective egress policy and any model that would be refused."""
    if not settings.EGRESS_ENFORCED:
        logger.warning("Egress allow-list disabled (EGRESS_ENFORCED=false)")
    else:
        logger.info("Egress allow-list: %s", ", ".join(allowed_hosts()) or "(empty: all outbound HTTP blocked)")
    for problem in model_configuration_problems():
        logger.error("Model configuration refused: %s", problem)
