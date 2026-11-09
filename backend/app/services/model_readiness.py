"""REL-03: a real readiness check for the models an audit will call.

/api/health is a static liveness probe and must stay that way. Readiness is a
cached one-token query per role model, run only before an audit starts, so an
endpoint that lists as READY but refuses queries (the 2026-11-08 HTTP 400
outage) blocks the audit instead of letting it degrade.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Optional

from app.config import settings
from app.services import llm_client
from app.services.llm_client import LLM_BREAKER, LLMUnavailableError

READY_TTL_SECONDS = 120.0
NOT_READY_TTL_SECONDS = 15.0
PROBE_PROMPT = "Reply with the single word OK."


@dataclass
class Readiness:
    model: str
    ready: bool
    detail: str
    checked_at: float


_cache: dict[str, Readiness] = {}
_lock = asyncio.Lock()


def role_models(selected_model: Optional[str] = None) -> list[str]:
    """Every distinct model an audit run will call."""
    models = [
        selected_model or settings.LLM_MODEL,
        settings.ATOMIC_DECOMPOSITION_MODEL,
    ]
    return [model for model in dict.fromkeys(models) if model]


def reset() -> None:
    _cache.clear()


async def _probe(model: str) -> Readiness:
    now = time.monotonic()
    try:
        reply = await llm_client.generate_text(PROBE_PROMPT, model=model)
    except LLMUnavailableError as exc:
        return Readiness(model, False, exc.user_message, now)
    except Exception as exc:  # a probe must never crash the request
        return Readiness(model, False, f"Readiness probe failed: {type(exc).__name__}", now)
    if not (reply or "").strip():
        return Readiness(model, False, f"The model {model} returned no answer to a one-token readiness query.", now)
    return Readiness(model, True, "ready", now)


async def check_models(models: list[str]) -> list[Readiness]:
    """Return cached or fresh readiness for each model (fresh probes run concurrently)."""
    async with _lock:
        now = time.monotonic()
        results: dict[str, Readiness] = {}
        stale = []
        for model in models:
            # An open breaker is already proof the model is down.
            open_error = LLM_BREAKER.open_error()
            if open_error is not None and open_error.model == model:
                results[model] = Readiness(model, False, open_error.user_message, now)
                continue
            cached = _cache.get(model)
            ttl = READY_TTL_SECONDS if cached and cached.ready else NOT_READY_TTL_SECONDS
            if cached and now - cached.checked_at < ttl:
                results[model] = cached
            else:
                stale.append(model)
        for readiness in await asyncio.gather(*(_probe(model) for model in stale)):
            _cache[readiness.model] = readiness
            results[readiness.model] = readiness
        return [results[model] for model in models]


async def not_ready_reason(selected_model: Optional[str] = None) -> Optional[str]:
    """None when every role model answers; otherwise an honest reason to refuse the audit."""
    if not getattr(settings, "AUDIT_READINESS_CHECK", True) or not llm_credentials_ready():
        return None
    failed = [item for item in await check_models(role_models(selected_model)) if not item.ready]
    if not failed:
        return None
    return " ".join(item.detail for item in failed) + " The audit was not started; try again when the model is serving."


def llm_credentials_ready() -> bool:
    # Without credentials there is nothing to probe; the pipeline's own
    # credential checks report that case.
    return llm_client.llm_credentials_configured()
