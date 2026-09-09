"""Unified LLM Client supporting Google Gemini Thinking capabilities and OpenAI.

Supports Thinking via thinkingConfig (https://ai.google.dev/gemini-api/docs/thinking):
- For Gemini 3 series (e.g. gemini-3.8-flash, gemini-3.7-flash):
    thinkingConfig: {"thinkingLevel": "high" | "medium" | "low"}
- For Gemini 2.5 series:
    thinkingConfig: {"thinkingBudget": -1}
"""

import json
import re
import asyncio
import base64
import logging
import sys
from typing import Type, TypeVar, Optional, Any
import httpx
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger("traceaudit.llm")

T = TypeVar("T", bound=BaseModel)

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models"

# Transient failures worth retrying: rate limit + gateway/capacity errors
RETRYABLE_STATUS_CODES = {429, 502, 503, 504}
MAX_ATTEMPTS = 3


def _clean_json_text(text: Any) -> str:
    """Extract and clean JSON text from raw string or Databricks/OpenAI multi-part response."""
    if text is None:
        return ""
    if isinstance(text, list):
        parts = []
        for item in text:
            if isinstance(item, dict):
                if item.get("type") == "text" and "text" in item:
                    parts.append(item["text"])
                elif "content" in item:
                    parts.append(str(item["content"]))
                elif "text" in item:
                    parts.append(str(item["text"]))
            elif isinstance(item, str):
                parts.append(item)
        cleaned = "\n".join(parts).strip() if parts else str(text)
    elif isinstance(text, dict):
        if "text" in text:
            cleaned = str(text["text"]).strip()
        elif "content" in text:
            cleaned = str(text["content"]).strip()
        else:
            cleaned = json.dumps(text)
    else:
        cleaned = str(text).strip()

    if "```json" in cleaned:
        cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
    elif cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


def _gemini_major_version(model_name: str) -> Optional[int]:
    """Extract the Gemini major version (e.g. 'gemini-3.7-flash' -> 3), or None."""
    m = re.search(r"gemini[-_]?(\d+)", model_name.lower())
    return int(m.group(1)) if m else None


def is_gemini_3_series(model_name: str) -> bool:
    """Check if model belongs to the Gemini 3+ series which uses thinkingLevel."""
    version = _gemini_major_version(model_name)
    return version is not None and version >= 3


def is_gemini_2_5_series(model_name: str) -> bool:
    """Check if model belongs to the pre-3 Gemini series which uses thinkingBudget."""
    version = _gemini_major_version(model_name)
    return version is not None and version < 3


_GEMINI_JSON_SCHEMA_KEYS = {
    "$id", "$ref", "$anchor", "type", "format", "title", "description",
    "enum", "items", "prefixItems", "minItems", "maxItems", "minimum",
    "maximum", "anyOf", "oneOf", "additionalProperties", "required",
    "propertyOrdering",
}


def _gemini_json_schema(value: Any) -> Any:
    """Reduce a Pydantic schema to Gemini's supported JSON-Schema subset.

    Pydantic emits validation-only keywords such as ``default`` and ``pattern``
    that the Gemini REST API does not accept in ``responseJsonSchema``.  Local
    Pydantic validation still enforces those constraints after generation.
    """
    if isinstance(value, list):
        return [_gemini_json_schema(item) for item in value]
    if not isinstance(value, dict):
        return value

    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        if key in {"properties", "$defs"} and isinstance(item, dict):
            cleaned[key] = {
                str(name): _gemini_json_schema(child)
                for name, child in item.items()
            }
        elif key == "const":
            cleaned["enum"] = [item]
        elif key in _GEMINI_JSON_SCHEMA_KEYS:
            cleaned[key] = _gemini_json_schema(item)
    return cleaned


def _console_safe_text(value: Any) -> str:
    """Make provider error text printable on legacy Windows code pages."""
    text = str(value or "")
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return text.encode(encoding, errors="replace").decode(encoding, errors="replace")


async def call_gemini_generate_content(
    prompt: str,
    model: str = "gemini-3.7-flash",
    system_instruction: Optional[str] = None,
    json_mode: bool = False,
    response_schema: Optional[dict] = None,
    thinking_level: Optional[str] = None,
    max_output_tokens: int = 8192,
    timeout: float = 90.0,
    image_bytes: Optional[bytes] = None,
    image_mime_type: str = "image/png",
) -> Optional[str]:
    """Call Google Gemini generateContent API via REST with Thinking capabilities enabled."""
    api_key = settings.effective_gemini_api_key
    if not api_key:
        logger.warning("No Gemini API key configured.")
        return None

    clean_model = model.replace("models/", "")
    url = f"{GEMINI_API_URL}/{clean_model}:generateContent?key={api_key}"

    active_thinking_level = (thinking_level or settings.GEMINI_THINKING_LEVEL).strip()

    generation_config: dict[str, Any] = {"maxOutputTokens": max_output_tokens}

    # Gemini 3.7/3.8 reject the legacy sampling knobs during migration. Older
    # models still benefit from a low extraction temperature.
    if not is_gemini_3_series(clean_model):
        generation_config["temperature"] = 0.1

    # Enable Gemini Thinking
    if is_gemini_3_series(clean_model):
        # Gemini 3.x uses thinkingLevel: high | medium | low. MINIMAL is not
        # supported by the current 3.7/3.8 Flash endpoints.
        if active_thinking_level.lower() == "minimal":
            # Gemini 3.7/3.8 Flash intentionally do not expose MINIMAL;
            # preserve the caller's low-latency intent with the supported LOW.
            active_thinking_level = "low"
        generation_config["thinkingConfig"] = {
            # REST expects the enum spelling in lower-case (the UI/config API
            # intentionally continues to expose HIGH/MEDIUM/LOW).
            "thinkingLevel": active_thinking_level.lower(),
        }
    elif is_gemini_2_5_series(clean_model):
        # Gemini 2.5 uses thinkingBudget: -1 for dynamic thinking
        generation_config["thinkingConfig"] = {
            "thinkingBudget": settings.GEMINI_THINKING_BUDGET,
        }

    if json_mode:
        generation_config["responseMimeType"] = "application/json"
        if response_schema:
            generation_config["responseJsonSchema"] = _gemini_json_schema(response_schema)

    parts: list[dict[str, Any]] = [{"text": prompt}]
    if image_bytes:
        parts.append({
            "inline_data": {
                "mime_type": image_mime_type,
                "data": base64.b64encode(image_bytes).decode("ascii"),
            }
        })

    payload: dict[str, Any] = {
        "contents": [
            {
                "parts": parts
            }
        ],
        "generationConfig": generation_config,
    }

    if system_instruction:
        payload["systemInstruction"] = {
            "parts": [{"text": system_instruction}]
        }

    for attempt in range(MAX_ATTEMPTS):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code in RETRYABLE_STATUS_CODES:
                    logger.warning(f"Gemini API rate/capacity [{resp.status_code}]. Retrying (attempt {attempt+1}/{MAX_ATTEMPTS})...")
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                if resp.status_code != 200:
                    logger.error(f"Gemini API error [{resp.status_code}]: {resp.text}")
                    return None

                data = resp.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    logger.warning("No candidates returned from Gemini.")
                    return None

                parts = candidates[0].get("content", {}).get("parts", [])
                if not parts:
                    return None

                return parts[0].get("text", "")
        except Exception as ex:
            if attempt == MAX_ATTEMPTS - 1:
                logger.error(f"Exception calling Gemini API ({clean_model}): {ex}")
                return None
            await asyncio.sleep(1.0)
    return None


def _openai_message_text(data: dict[str, Any]) -> str:
    """Extract text from an OpenAI-compatible chat-completion response."""
    choices = data.get("choices") or []
    if not choices:
        return ""
    content = (choices[0].get("message") or {}).get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text") or item.get("content") or "")
            for item in content
            if isinstance(item, dict)
        ).strip()
    return str(content or "").strip()


def _strip_reasoning_tags(text: str) -> str:
    """Keep provider reasoning traces out of persisted evidence descriptions."""
    cleaned = text.strip()
    closing_tag = cleaned.lower().rfind("</think>")
    if closing_tag >= 0:
        cleaned = cleaned[closing_tag + len("</think>"):]
    return cleaned.replace("<think>", "").replace("</think>", "").strip()


async def _call_openai_compatible_vision(
    *,
    prompt: str,
    image_bytes: bytes,
    image_mime_type: str,
    api_key: str,
    url: str,
    model: str,
    provider: str,
    max_output_tokens: int = 1400,
    timeout: float = 90.0,
    token_parameter: str = "max_tokens",
    request_options: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """Call Groq or Hugging Face through their OpenAI-compatible VLM API."""
    if not api_key:
        return None
    image_url = (
        f"data:{image_mime_type};base64,"
        + base64.b64encode(image_bytes).decode("ascii")
    )
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        }],
        "temperature": 0.1,
        token_parameter: max_output_tokens,
        "stream": False,
    }
    payload.update(request_options or {})
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    for attempt in range(MAX_ATTEMPTS):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, json=payload, headers=headers)
            if response.status_code in RETRYABLE_STATUS_CODES:
                retry_after = response.headers.get("retry-after", "")
                try:
                    provider_delay = min(60.0, max(0.0, float(retry_after)))
                except ValueError:
                    provider_delay = 0.0
                delay = max(1.5 * (attempt + 1), provider_delay)
                logger.warning(
                    "%s vision rate/capacity [%s]. Retrying in %.1fs (attempt %s/%s)...",
                    provider,
                    response.status_code,
                    delay,
                    attempt + 1,
                    MAX_ATTEMPTS,
                )
                await asyncio.sleep(delay)
                continue
            if response.status_code != 200:
                logger.error(
                    "%s vision API error [%s] for %s: %s",
                    provider,
                    response.status_code,
                    model,
                    response.text[:500],
                )
                return None
            text = _strip_reasoning_tags(_openai_message_text(response.json()))
            return text or None
        except Exception as exc:
            if attempt == MAX_ATTEMPTS - 1:
                logger.error("%s vision call failed for %s: %s", provider, model, exc)
                return None
            await asyncio.sleep(1.0 * (attempt + 1))
    return None


async def call_groq_vision(
    prompt: str,
    *,
    image_bytes: bytes,
    image_mime_type: str = "image/png",
    model: Optional[str] = None,
    max_output_tokens: int = 1400,
) -> Optional[str]:
    """Describe an image with Groq's current vision chat-completion API."""
    return await _call_openai_compatible_vision(
        prompt=prompt,
        image_bytes=image_bytes,
        image_mime_type=image_mime_type,
        api_key=settings.effective_groq_api_key,
        url="https://api.groq.com/openai/v1/chat/completions",
        model=model or settings.GROQ_VISION_MODEL,
        provider="Groq",
        max_output_tokens=max_output_tokens,
        token_parameter="max_completion_tokens",
        request_options={"reasoning_effort": "none", "reasoning_format": "hidden"},
    )


async def call_huggingface_vision(
    prompt: str,
    *,
    image_bytes: bytes,
    image_mime_type: str = "image/png",
    model: Optional[str] = None,
    max_output_tokens: int = 1400,
) -> Optional[str]:
    """Describe an image through Hugging Face Inference Providers."""
    return await _call_openai_compatible_vision(
        prompt=prompt,
        image_bytes=image_bytes,
        image_mime_type=image_mime_type,
        api_key=settings.effective_hf_token,
        url="https://router.huggingface.co/v1/chat/completions",
        model=model or settings.HF_VISION_MODEL,
        provider="Hugging Face",
        max_output_tokens=max_output_tokens,
    )


async def call_vision_with_fallback(
    prompt: str,
    *,
    image_bytes: bytes,
    image_mime_type: str = "image/png",
    gemini_model: str = "models/gemini-3.6-flash",
    max_output_tokens: int = 1400,
    skip_providers: Optional[set[str]] = None,
) -> dict[str, Any]:
    """Try Gemini, Groq, then Hugging Face and return auditable provider data."""
    attempted: list[dict[str, str]] = []
    skipped = {provider.lower() for provider in (skip_providers or set())}

    if "gemini" not in skipped and settings.effective_gemini_api_key:
        attempted.append({"provider": "gemini", "model": gemini_model})
        text = await call_gemini_generate_content(
            prompt,
            model=gemini_model,
            thinking_level="LOW",
            max_output_tokens=max_output_tokens,
            image_bytes=image_bytes,
            image_mime_type=image_mime_type,
        )
        if text and text.strip():
            return {
                "text": text.strip(),
                "provider": "gemini",
                "model": gemini_model,
                "attempted": attempted,
            }

    if "groq" not in skipped and settings.effective_groq_api_key:
        attempted.append({"provider": "groq", "model": settings.GROQ_VISION_MODEL})
        text = await call_groq_vision(
            prompt,
            image_bytes=image_bytes,
            image_mime_type=image_mime_type,
            max_output_tokens=max_output_tokens,
        )
        if text and text.strip():
            return {
                "text": text.strip(),
                "provider": "groq",
                "model": settings.GROQ_VISION_MODEL,
                "attempted": attempted,
            }

    if "huggingface" not in skipped and settings.effective_hf_token:
        attempted.append({"provider": "huggingface", "model": settings.HF_VISION_MODEL})
        text = await call_huggingface_vision(
            prompt,
            image_bytes=image_bytes,
            image_mime_type=image_mime_type,
            max_output_tokens=max_output_tokens,
        )
        if text and text.strip():
            return {
                "text": text.strip(),
                "provider": "huggingface",
                "model": settings.HF_VISION_MODEL,
                "attempted": attempted,
            }

    return {"text": "", "provider": "", "model": "", "attempted": attempted}


async def call_databricks_chat_completions(
    prompt: str,
    model: str = "system.ai.llama-4-maverick",
    system_instruction: Optional[str] = None,
    json_mode: bool = False,
    max_output_tokens: int = 4096,
    timeout: float = 90.0,
) -> Optional[str]:
    """Call Databricks Model Serving AI Gateway via OpenAI-compatible endpoint."""
    import time
    token = settings.effective_databricks_token
    if not token or not settings.DATABRICKS_BASE_URL:
        return None

    base_url = settings.DATABRICKS_BASE_URL.rstrip("/")
    url = f"{base_url}/chat/completions"

    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": max_output_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    for attempt in range(MAX_ATTEMPTS):
        try:
            t0 = time.time()
            print(f"  [LLM Request -> Databricks] Sending prompt to {model} ({len(prompt)} chars)...", flush=True)
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
                elapsed = time.time() - t0
                if resp.status_code in RETRYABLE_STATUS_CODES:
                    print(f"  [LLM Warning] Databricks rate/capacity [{resp.status_code}]. Retrying (attempt {attempt+1}/{MAX_ATTEMPTS})...", flush=True)
                    logger.warning(
                        f"Databricks API rate/capacity [{resp.status_code}] for model {model}. "
                        f"Retrying (attempt {attempt+1}/{MAX_ATTEMPTS})..."
                    )
                    if attempt < MAX_ATTEMPTS - 1:
                        await asyncio.sleep(20.0)
                    continue
                if resp.status_code != 200:
                    if resp.status_code == 400 and "response_format" in payload:
                        # Some serving endpoints expose OpenAI chat semantics
                        # but not JSON response mode. Keep schema prompting as
                        # the portable fallback instead of failing the call.
                        payload.pop("response_format", None)
                        logger.warning(
                            "Databricks model %s rejected JSON response mode; retrying with schema-only prompting.",
                            model,
                        )
                        if attempt < MAX_ATTEMPTS - 1:
                            await asyncio.sleep(20.0)
                        continue
                    print(f"  [LLM Error] Databricks returned HTTP {resp.status_code}: {resp.text[:200]}", flush=True)
                    logger.error(f"Databricks API error [{resp.status_code}] for model {model}: {resp.text}")
                    return None

                data = resp.json()
                choices = data.get("choices", [])
                if not choices:
                    logger.warning(f"Databricks API returned no choices for model {model}.")
                    if attempt < MAX_ATTEMPTS - 1:
                        await asyncio.sleep(20.0)
                    continue
                content = choices[0].get("message", {}).get("content")
                if isinstance(content, list):
                    parts = []
                    for p in content:
                        if isinstance(p, dict):
                            if p.get("type") == "text" and "text" in p:
                                parts.append(p["text"])
                            elif "text" in p and p.get("type") != "reasoning":
                                parts.append(p["text"])
                            elif "content" in p:
                                parts.append(str(p["content"]))
                        elif isinstance(p, str):
                            parts.append(p)
                    result_text = "\n".join(parts) if parts else str(content)
                else:
                    result_text = content
                print(f"  [LLM Response <- Databricks] Received response from {model} in {elapsed:.2f}s ({len(str(result_text))} chars)", flush=True)
                if not result_text or str(result_text).strip() == "null":
                    if attempt < MAX_ATTEMPTS - 1:
                        await asyncio.sleep(20.0)
                    continue
                if json_mode:
                    try:
                        json.loads(_clean_json_text(result_text))
                    except (TypeError, ValueError):
                        logger.warning("Databricks returned invalid JSON for %s", model)
                        if attempt < MAX_ATTEMPTS - 1:
                            await asyncio.sleep(20.0)
                        continue
                return result_text
        except Exception as ex:
            if attempt == MAX_ATTEMPTS - 1:
                print(f"  [LLM Error] Databricks call failed: {ex}", flush=True)
                logger.error(f"Exception calling Databricks Model Serving ({model}): {ex}")
                return None
            await asyncio.sleep(20.0)
    return None


async def call_groq_chat_completions(
    prompt: str,
    model: Optional[str] = None,
    system_instruction: Optional[str] = None,
    response_schema: Optional[dict[str, Any]] = None,
    schema_name: str = "structured_response",
    reasoning_effort: Optional[str] = None,
    max_output_tokens: int = 8192,
    timeout: float = 90.0,
) -> Optional[str]:
    """Call Groq text generation with Qwen structured-output support."""
    import time

    api_key = settings.effective_groq_api_key
    if not api_key:
        return None

    active_model = model or settings.GROQ_TEXT_MODEL
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})

    payload: dict[str, Any] = {
        "model": active_model,
        "messages": messages,
        "temperature": 0.1,
        "max_completion_tokens": min(max_output_tokens, 16384),
        "stream": False,
    }
    if response_schema:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": re.sub(r"[^A-Za-z0-9_-]", "_", schema_name)[:64],
                # Pydantic's schema contains optional fields, so use Groq's
                # best-effort schema mode and retain local Pydantic validation.
                "strict": False,
                "schema": response_schema,
            },
        }
    else:
        payload["response_format"] = {"type": "json_object"}

    effort = str(reasoning_effort or "none").strip().lower()
    effort_aliases = {"minimal": "low", "xhigh": "high", "max": "high"}
    effort = effort_aliases.get(effort, effort)
    if effort not in {"none", "default", "low", "medium", "high"}:
        effort = "none"
    payload["reasoning_effort"] = effort
    payload["reasoning_format"] = "hidden"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    url = "https://api.groq.com/openai/v1/chat/completions"
    for attempt in range(MAX_ATTEMPTS):
        try:
            started = time.time()
            print(
                f"  [LLM Request -> Groq] Sending prompt to {active_model} ({len(prompt)} chars)...",
                flush=True,
            )
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, json=payload, headers=headers)
            elapsed = time.time() - started
            if response.status_code in RETRYABLE_STATUS_CODES:
                retry_after = response.headers.get("retry-after", "")
                try:
                    provider_delay = min(60.0, max(0.0, float(retry_after)))
                except ValueError:
                    provider_delay = 0.0
                delay = max(1.5 * (attempt + 1), provider_delay)
                logger.warning(
                    "Groq text rate/capacity [%s]. Retrying in %.1fs (attempt %s/%s)...",
                    response.status_code,
                    delay,
                    attempt + 1,
                    MAX_ATTEMPTS,
                )
                await asyncio.sleep(delay)
                continue
            if response.status_code != 200:
                if response.status_code == 400 and payload.get("response_format", {}).get("type") == "json_schema":
                    # Retain JSON syntax guarantees if a deployed model rejects
                    # a particular Pydantic schema feature.
                    payload["response_format"] = {"type": "json_object"}
                    logger.warning(
                        "Groq model %s rejected JSON Schema mode; retrying with JSON Object mode.",
                        active_model,
                    )
                    continue
                logger.error(
                    "Groq text API error [%s] for %s: %s",
                    response.status_code,
                    active_model,
                    response.text[:500],
                )
                return None

            result_text = _openai_message_text(response.json())
            print(
                f"  [LLM Response <- Groq] Received response from {active_model} "
                f"in {elapsed:.2f}s ({len(result_text)} chars)",
                flush=True,
            )
            return result_text or None
        except Exception as exc:
            if attempt == MAX_ATTEMPTS - 1:
                logger.error("Groq text call failed for %s: %s", active_model, exc)
                return None
            await asyncio.sleep(1.0 * (attempt + 1))
    return None


async def call_tokenrouter_chat_completions(
    prompt: str,
    model: str = "z-ai/glm-5.3-free",
    system_instruction: Optional[str] = None,
    json_mode: bool = False,
    max_output_tokens: int = 4096,
    timeout: float = 60.0,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    stream: bool = True,
) -> Optional[str]:
    """Call TokenRouter's OpenAI-compatible endpoint, streaming by default.

    GLM endpoints are schema-prompted instead of receiving ``response_format``
    or a client-side output-token cap; some GLM routes spend output tokens on
    hidden reasoning before emitting final content.
    """
    import time
    token = api_key or settings.effective_tokenrouter_api_key
    target_base = (base_url or settings.effective_tokenrouter_base_url).rstrip("/")
    if not token or not target_base:
        return None

    url = f"{target_base}/chat/completions"
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})

    is_glm = "glm-" in model.lower() or model.lower().startswith("z-ai/glm")
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
    }
    if not is_glm:
        payload["temperature"] = 0.1
        payload["max_tokens"] = max_output_tokens
    if json_mode and not is_glm:
        payload["response_format"] = {"type": "json_object"}
    if stream:
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    for attempt in range(MAX_ATTEMPTS):
        try:
            t0 = time.time()
            print(
                f"  [LLM Request -> TokenRouter] Sending prompt to {model} "
                f"({len(prompt)} chars, {'streaming' if stream else 'non-streaming'}, "
                f"attempt {attempt + 1}/{MAX_ATTEMPTS})...",
                flush=True,
            )
            request_timeout = httpx.Timeout(timeout, connect=min(timeout, 20.0))
            reasoning_characters = 0
            finish_reasons: list[str] = []
            usage: dict[str, Any] = {}
            async with httpx.AsyncClient(timeout=request_timeout) as client:
                if stream:
                    async with client.stream("POST", url, json=payload, headers=headers) as resp:
                        elapsed = time.time() - t0
                        if resp.status_code in RETRYABLE_STATUS_CODES:
                            error_body = _console_safe_text(
                                (await resp.aread()).decode("utf-8", errors="replace")
                            )
                            print(
                                f"  [LLM Warning] TokenRouter rate/capacity [{resp.status_code}] "
                                f"for {model}: {error_body[:160]}. Retrying...",
                                flush=True,
                            )
                            await asyncio.sleep(2.0 * (attempt + 1))
                            continue
                        if resp.status_code != 200:
                            error_body = _console_safe_text(
                                (await resp.aread()).decode("utf-8", errors="replace")
                            )
                            print(
                                f"  [LLM Error] TokenRouter returned HTTP {resp.status_code}: "
                                f"{error_body[:300]}",
                                flush=True,
                            )
                            logger.error(
                                "TokenRouter API error [%s] for model %s: %s",
                                resp.status_code,
                                model,
                                error_body,
                            )
                            return None

                        content_parts: list[str] = []
                        raw_lines: list[str] = []
                        first_token_logged = False
                        async for line in resp.aiter_lines():
                            stripped = line.strip()
                            if not stripped:
                                continue
                            raw_lines.append(stripped)
                            if not stripped.startswith("data:"):
                                continue
                            data_text = stripped[5:].strip()
                            if not data_text or data_text == "[DONE]":
                                continue
                            try:
                                event = json.loads(data_text)
                            except json.JSONDecodeError:
                                continue
                            if isinstance(event.get("usage"), dict):
                                usage = event["usage"]
                            choices = event.get("choices") or []
                            if not choices:
                                continue
                            choice = choices[0]
                            finish_reason = choice.get("finish_reason")
                            if finish_reason:
                                finish_reasons.append(str(finish_reason))
                            delta = choice.get("delta") or {}
                            message = choice.get("message") or {}
                            reasoning = delta.get("reasoning_content") or message.get("reasoning_content")
                            if isinstance(reasoning, str):
                                reasoning_characters += len(reasoning)
                            content = delta.get("content")
                            if content is None:
                                content = message.get("content")
                            if isinstance(content, str):
                                piece = content
                            elif isinstance(content, list):
                                piece = "".join(
                                    str(item.get("text") or "")
                                    for item in content
                                    if isinstance(item, dict)
                                )
                            else:
                                piece = ""
                            if piece:
                                content_parts.append(piece)
                                if not first_token_logged:
                                    print(
                                        f"  [LLM Stream <- TokenRouter] First output received from {model} "
                                        f"after {time.time() - t0:.2f}s.",
                                        flush=True,
                                    )
                                    first_token_logged = True

                        result_text = "".join(content_parts)
                        # Be tolerant when a compatible gateway ignores
                        # stream=true and returns one ordinary JSON response.
                        if not result_text and raw_lines:
                            try:
                                result_text = _openai_message_text(json.loads("\n".join(raw_lines)))
                            except json.JSONDecodeError:
                                result_text = ""
                else:
                    resp = await client.post(url, json=payload, headers=headers)
                    elapsed = time.time() - t0
                    if resp.status_code in RETRYABLE_STATUS_CODES:
                        print(
                            f"  [LLM Warning] TokenRouter rate/capacity [{resp.status_code}]. Retrying...",
                            flush=True,
                        )
                        await asyncio.sleep(2.0 * (attempt + 1))
                        continue
                    if resp.status_code != 200:
                        error_body = _console_safe_text(resp.text)
                        print(
                            f"  [LLM Error] TokenRouter returned HTTP {resp.status_code}: {error_body[:300]}",
                            flush=True,
                        )
                        return None
                    result_text = _openai_message_text(resp.json())

                elapsed = time.time() - t0
                if result_text:
                    cleaned = _clean_json_text(result_text) if json_mode else result_text
                    print(
                        f"  [LLM Response <- TokenRouter] Received response from {model} "
                        f"in {elapsed:.2f}s ({len(str(cleaned))} chars)",
                        flush=True,
                    )
                    return str(cleaned)
                print(
                    f"  [LLM Warning] TokenRouter returned no text from {model} "
                    f"(finish={','.join(dict.fromkeys(finish_reasons)) or 'unknown'}, "
                    f"reasoning_chars={reasoning_characters}, usage={usage or 'unavailable'}).",
                    flush=True,
                )
                # An HTTP-200 stream with no final content is a completed but
                # unusable model response, not a transient transport failure.
                # Return immediately so the explicit atomic-stage fallback can
                # continue instead of issuing another long GLM request.
                return None
        except Exception as exc:
            if attempt == MAX_ATTEMPTS - 1:
                logger.error(f"TokenRouter API call failed for {model}: {exc}")
                print(
                    f"  [LLM Error] TokenRouter call failed for {model}: {exc}",
                    flush=True,
                )
                return None
            print(
                f"  [LLM Warning] TokenRouter call failed for {model}: {exc}. Retrying...",
                flush=True,
            )
            await asyncio.sleep(2.0 * (attempt + 1))
    return None


async def call_openai_chat_completions(
    prompt: str,
    model: str = "gpt-4o-mini",
    system_instruction: Optional[str] = None,
    json_mode: bool = False,
    max_output_tokens: Optional[int] = None,
    timeout: float = 60.0,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Optional[str]:
    """Call OpenAI chat completions API via REST."""
    key = api_key or settings.effective_openai_api_key
    target_base = (base_url or settings.effective_openai_base_url).rstrip("/")
    if not key or not target_base:
        return None

    url = f"{target_base}/chat/completions"
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if max_output_tokens is not None:
        payload["max_tokens"] = max_output_tokens

    try:
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                logger.error(f"OpenAI API error [{resp.status_code}]: {resp.text}")
                return None
            data = resp.json()
            return data["choices"][0]["message"]["content"]
    except Exception as ex:
        logger.error(f"Exception calling OpenAI API: {ex}")
        return None


def resolve_llm_provider(model: Optional[str] = None) -> str:
    """Resolve the effective provider: explicit CLI/env provider wins, with model-family compatibility guards."""
    active_model = (model or settings.LLM_MODEL or "").strip().lower()
    explicit_provider = str(settings.LLM_PROVIDER or "").strip().lower()

    # Invariant guards: models with namespaces or prefixes that only belong to one provider
    if ("z-ai/" in active_model or "glm-" in active_model or "tokenrouter" in active_model) and explicit_provider != "openai":
        return "tokenrouter"
    if active_model.startswith("system.ai."):
        return "databricks"
    if active_model.startswith("gemini-"):
        return "gemini"

    if explicit_provider in {"tokenrouter", "groq", "databricks", "gemini", "openai"}:
        return explicit_provider

    # Heuristic inference if no explicit provider set
    if "z-ai/" in active_model or "glm-" in active_model or "tokenrouter" in active_model:
        return "tokenrouter"
    if "system.ai." in active_model or "databricks" in active_model:
        return "databricks"
    if "gemini" in active_model:
        return "gemini"
    if "qwen" in active_model:
        return "groq" if settings.effective_groq_api_key else "databricks"
    if "llama" in active_model:
        return "databricks" if settings.effective_databricks_token else "groq"
    if active_model.startswith("gpt-"):
        return "openai"

    if settings.effective_tokenrouter_api_key and ("z-ai" in active_model or "glm" in active_model):
        return "tokenrouter"
    if settings.effective_gemini_api_key:
        return "gemini"
    if settings.effective_databricks_token:
        return "databricks"
    if settings.effective_tokenrouter_api_key:
        return "tokenrouter"
    return "gemini"


async def generate_structured(
    prompt: str,
    response_model: Type[T],
    model: Optional[str] = None,
    system_instruction: Optional[str] = None,
    thinking_level: Optional[str] = None,
    max_output_tokens: Optional[int] = None,
    allow_model_fallback: bool = True,
) -> Optional[T]:
    """Generate structured output validated against a Pydantic schema using TokenRouter, Groq, Databricks, Gemini, or OpenAI."""
    active_model = model or settings.LLM_MODEL
    provider = resolve_llm_provider(active_model)

    raw_response: Optional[str] = None

    schema = response_model.model_json_schema()
    prompt_with_schema = f"{prompt}\n\nRespond ONLY with valid JSON strictly conforming to this schema:\n{json.dumps(schema)}"

    # 1. TokenRouter (when selected or inferred)
    if provider == "tokenrouter" and settings.effective_tokenrouter_api_key:
        raw_response = await call_tokenrouter_chat_completions(
            prompt=prompt_with_schema,
            model=active_model,
            system_instruction=system_instruction,
            json_mode=True,
            max_output_tokens=max_output_tokens or 4096,
        )

    # 2. Groq (when explicitly selected or inferred)
    elif provider == "groq" and settings.effective_groq_api_key:
        raw_response = await call_groq_chat_completions(
            prompt=prompt_with_schema,
            model=active_model,
            system_instruction=system_instruction,
            response_schema=schema,
            schema_name=response_model.__name__,
            reasoning_effort=thinking_level,
            max_output_tokens=max_output_tokens or 8192,
        )

    # 3. Databricks AI Gateway (when configured or requested)
    elif provider == "databricks" and settings.effective_databricks_token:
        models_to_try = [active_model]
        if allow_model_fallback and active_model != "system.ai.llama-4-maverick":
            models_to_try.extend(
                m for m in settings.DATABRICKS_FALLBACK_MODELS if m != active_model
            )
        for db_model in models_to_try:
            raw_response = await call_databricks_chat_completions(
                prompt=prompt_with_schema,
                model=db_model,
                system_instruction=system_instruction,
                json_mode=True,
                max_output_tokens=max_output_tokens or 4096,
            )
            if raw_response:
                break

    # 4. Google Gemini (when configured and selected)
    elif provider == "gemini" and settings.effective_gemini_api_key:
        raw_response = await call_gemini_generate_content(
            prompt=f"{prompt}\n\nReturn only the JSON object required by the response schema.",
            model=active_model,
            system_instruction=system_instruction,
            json_mode=True,
            response_schema=schema,
            thinking_level=thinking_level,
            max_output_tokens=max_output_tokens or 8192,
        )

    # 5. OpenAI
    elif provider == "openai" and settings.effective_openai_api_key:
        raw_response = await call_openai_chat_completions(
            prompt=prompt_with_schema,
            model=active_model if active_model.startswith("gpt-") else "gpt-4o-mini",
            system_instruction=system_instruction,
            json_mode=True,
            max_output_tokens=max_output_tokens,
        )

    # Fallbacks across configured providers if primary returned nothing
    if not raw_response and allow_model_fallback and active_model != "system.ai.llama-4-maverick":
        if provider != "tokenrouter" and settings.effective_tokenrouter_api_key and ("z-ai" in active_model or "glm" in active_model):
            raw_response = await call_tokenrouter_chat_completions(
                prompt=prompt_with_schema,
                model=active_model,
                system_instruction=system_instruction,
                json_mode=True,
                max_output_tokens=max_output_tokens or 4096,
            )
        elif provider != "openai" and settings.effective_openai_api_key:
            raw_response = await call_openai_chat_completions(
                prompt=prompt_with_schema,
                model=active_model if active_model.startswith("gpt-") else "gpt-4o-mini",
                system_instruction=system_instruction,
                json_mode=True,
                max_output_tokens=max_output_tokens,
            )

    if not raw_response:
        return None

    try:
        cleaned = _clean_json_text(raw_response)
        parsed_json = json.loads(cleaned)
        # If the model returned a bare list and the schema expects a wrapper
        # object with a `requirements` key, wrap it automatically.
        if isinstance(parsed_json, list) and hasattr(response_model, "model_fields") and "requirements" in response_model.model_fields:
            parsed_json = {"requirements": parsed_json}
        return response_model.model_validate(parsed_json)
    except Exception as ex:
        print(f"  [LLM Schema Error] Failed to parse JSON response: {ex}", flush=True)
        logger.error(f"Failed to validate model schema: {ex}. Raw: {raw_response[:300]}")
        return None


async def generate_text(
    prompt: str,
    model: Optional[str] = None,
    system_instruction: Optional[str] = None,
    thinking_level: Optional[str] = None,
) -> Optional[str]:
    """Generate free-form text response with dynamic provider routing and fallbacks."""
    active_model = model or settings.LLM_MODEL
    provider = resolve_llm_provider(active_model)

    # 1. TokenRouter
    if provider == "tokenrouter" and settings.effective_tokenrouter_api_key:
        res = await call_tokenrouter_chat_completions(
            prompt=prompt,
            model=active_model,
            system_instruction=system_instruction,
            json_mode=False,
        )
        if res:
            return res

    # 2. Gemini
    if provider == "gemini" and settings.effective_gemini_api_key:
        res = await call_gemini_generate_content(
            prompt=prompt,
            model=active_model,
            system_instruction=system_instruction,
            json_mode=False,
            thinking_level=thinking_level,
        )
        if res:
            return res

    # 3. Databricks Model Serving
    if (provider == "databricks" or not res) and settings.effective_databricks_token:
        models_to_try = [active_model] if "system.ai." in active_model else [settings.DATABRICKS_MODEL] + [m for m in settings.DATABRICKS_FALLBACK_MODELS if m != settings.DATABRICKS_MODEL]
        for db_model in models_to_try:
            logger.info(f"Cascading to Databricks AI Gateway model: {db_model}")
            res = await call_databricks_chat_completions(
                prompt=prompt,
                model=db_model,
                system_instruction=system_instruction,
                json_mode=False,
            )
            if res:
                return res

    if active_model == "system.ai.llama-4-maverick":
        return None

    # 4. TokenRouter fallback
    if settings.effective_tokenrouter_api_key:
        res = await call_tokenrouter_chat_completions(
            prompt=prompt,
            model=active_model if "z-ai" in active_model else settings.TOKENROUTER_MODEL,
            system_instruction=system_instruction,
            json_mode=False,
        )
        if res:
            return res

    # 5. OpenAI fallback
    if settings.effective_openai_api_key:
        return await call_openai_chat_completions(
            prompt=prompt,
            model=active_model if active_model.startswith("gpt-") else "gpt-4o-mini",
            system_instruction=system_instruction,
            json_mode=False,
        )
    return None
