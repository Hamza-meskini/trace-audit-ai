"""ARC-01 layer 1: keep verification-stage model answers across re-audits.

The assessment cache (layer 2, pipeline._verification_fingerprint) is keyed by
VERDICT_LOGIC_VERSION, so any change to the deterministic verdict logic misses
it and re-runs verification. Without this layer that re-run repays every model
call. With it, a call whose model, schema, prompt and parameters are unchanged
returns the stored answer, and only the deterministic logic runs again: a
logic fix reaches every project with zero paid calls.

Only successful, schema-valid answers are stored. A failed call (None or an
LLMUnavailableError) is never cached, so an outage cannot be frozen in.
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Type, TypeVar

from pydantic import BaseModel

from app.config import settings
from app.services import call_ledger, llm_client, llm_recording, pipeline_cache

logger = logging.getLogger("traceaudit.llm_response_cache")
T = TypeVar("T", bound=BaseModel)
NAMESPACE = "llm-response-v1"


def response_key(
    *,
    prompt: str,
    response_model: Type[BaseModel],
    model: Optional[str],
    system_instruction: Optional[str],
    thinking_level: Optional[str],
    max_output_tokens: Optional[int],
) -> str:
    # Same full-sha256 key as the EVA-03 recordings, so a recording can seed this cache.
    return llm_recording.request_key(
        prompt=prompt, response_model=response_model, model=model,
        system_instruction=system_instruction, thinking_level=thinking_level,
        max_output_tokens=max_output_tokens,
        temperature=llm_client.DEFAULT_DATABRICKS_TEMPERATURE,
    )


async def generate_structured(
    prompt: str,
    response_model: Type[T],
    model: Optional[str] = None,
    system_instruction: Optional[str] = None,
    thinking_level: Optional[str] = None,
    max_output_tokens: Optional[int] = None,
    allow_model_fallback: bool = True,
    diagnostics: Optional[dict[str, Any]] = None,
) -> Optional[T]:
    """llm_client.generate_structured with a durable answer cache in front."""
    enabled = bool(getattr(settings, "LLM_RESPONSE_CACHE_ENABLED", True))
    key = None
    if enabled:
        key = response_key(
            prompt=prompt, response_model=response_model, model=model,
            system_instruction=system_instruction, thinking_level=thinking_level,
            max_output_tokens=max_output_tokens,
        )
        try:
            stored = pipeline_cache.get_json(NAMESPACE, key)
        except Exception:  # a cache fault must never stop an audit
            logger.warning("LLM response cache read failed", exc_info=True)
            stored = None
        if stored is not None:
            try:
                result = response_model.model_validate(stored)
            except Exception:
                result = None
            if result is not None:
                if diagnostics is not None:
                    diagnostics["response_cache"] = "hit"
                call_ledger.record({
                    "kind": "cache_hit", "schema": response_model.__name__,
                    "model": model or settings.LLM_MODEL,
                })
                llm_recording.record(
                    prompt=prompt, response_model=response_model, model=model,
                    system_instruction=system_instruction, thinking_level=thinking_level,
                    max_output_tokens=max_output_tokens,
                    temperature=llm_client.DEFAULT_DATABRICKS_TEMPERATURE,
                    result=result, source="cache",
                )
                return result
    result = await llm_client.generate_structured(
        prompt=prompt,
        response_model=response_model,
        model=model,
        system_instruction=system_instruction,
        thinking_level=thinking_level,
        max_output_tokens=max_output_tokens,
        allow_model_fallback=allow_model_fallback,
        diagnostics=diagnostics,
    )
    if enabled and key is not None and result is not None:
        try:
            pipeline_cache.set_json(NAMESPACE, key, result.model_dump(mode="json"))
        except Exception:
            logger.warning("LLM response cache write failed", exc_info=True)
        if diagnostics is not None:
            diagnostics["response_cache"] = "miss"
    return result
