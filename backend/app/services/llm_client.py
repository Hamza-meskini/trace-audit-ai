"""Unified LLM Client supporting Google Gemini Thinking capabilities and OpenAI.

Supports Thinking via thinkingConfig (https://ai.google.dev/gemini-api/docs/thinking):
- For Gemini 3 series (e.g. gemini-3.7-flash, gemini-3.1-pro-preview):
    thinkingConfig: {"thinkingLevel": "HIGH" | "MEDIUM" | "LOW" | "MINIMAL"}
- For Gemini 2.5 series:
    thinkingConfig: {"thinkingBudget": -1}
"""

import json
import logging
from typing import Type, TypeVar, Optional, Any
import httpx
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger("traceaudit.llm")

T = TypeVar("T", bound=BaseModel)

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models"


def _clean_json_text(text: str) -> str:
    """Strip markdown code blocks if the model wrapped the JSON output in ```json ... ```."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


def is_gemini_3_series(model_name: str) -> bool:
    """Check if model belongs to the Gemini 3 series which uses thinkingLevel."""
    m = model_name.lower()
    return "3.7" in m or "3.1" in m or "3.5" in m or "3.0" in m or "gemini-3" in m


def is_gemini_2_5_series(model_name: str) -> bool:
    """Check if model belongs to Gemini 2.5 series which uses thinkingBudget."""
    m = model_name.lower()
    return "2.5" in m or "2.0" in m


async def call_gemini_generate_content(
    prompt: str,
    model: str = "gemini-3.7-flash",
    system_instruction: Optional[str] = None,
    json_mode: bool = False,
    response_schema: Optional[dict] = None,
    thinking_level: Optional[str] = None,
    timeout: float = 90.0,
) -> Optional[str]:
    """Call Google Gemini generateContent API via REST with Thinking capabilities enabled."""
    api_key = settings.effective_gemini_api_key
    if not api_key:
        logger.warning("No Gemini API key configured.")
        return None

    clean_model = model.replace("models/", "")
    url = f"{GEMINI_API_URL}/{clean_model}:generateContent?key={api_key}"

    active_thinking_level = (thinking_level or settings.GEMINI_THINKING_LEVEL).upper()

    generation_config: dict[str, Any] = {
        "temperature": 0.1,
        "maxOutputTokens": 8192,
    }

    # Enable Gemini Thinking
    if is_gemini_3_series(clean_model):
        # Gemini 3.7 Flash & 3.1 Pro Preview use thinkingLevel: "HIGH" | "MEDIUM" | "LOW" | "MINIMAL"
        generation_config["thinkingConfig"] = {
            "thinkingLevel": active_thinking_level,
        }
    elif is_gemini_2_5_series(clean_model):
        # Gemini 2.5 uses thinkingBudget: -1 for dynamic thinking
        generation_config["thinkingConfig"] = {
            "thinkingBudget": settings.GEMINI_THINKING_BUDGET,
        }

    if json_mode:
        generation_config["responseMimeType"] = "application/json"
        if response_schema:
            generation_config["responseSchema"] = response_schema

    payload: dict[str, Any] = {
        "contents": [
            {
                "parts": [{"text": prompt}]
            }
        ],
        "generationConfig": generation_config,
    }

    if system_instruction:
        payload["systemInstruction"] = {
            "parts": [{"text": system_instruction}]
        }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload)
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
        logger.error(f"Exception calling Gemini API ({clean_model}): {ex}")
        return None


async def call_openai_chat_completions(
    prompt: str,
    model: str = "gpt-4o-mini",
    system_instruction: Optional[str] = None,
    json_mode: bool = False,
    timeout: float = 60.0,
) -> Optional[str]:
    """Call OpenAI chat completions API via REST."""
    api_key = settings.effective_openai_api_key
    if not api_key:
        return None

    url = "https://api.openai.com/v1/chat/completions"
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

    try:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
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


async def generate_structured(
    prompt: str,
    response_model: Type[T],
    model: Optional[str] = None,
    system_instruction: Optional[str] = None,
    thinking_level: Optional[str] = None,
) -> Optional[T]:
    """Generate structured output validated against a Pydantic schema using the configured LLM with thinking."""
    active_model = model or settings.LLM_MODEL
    is_gemini = "gemini" in active_model.lower() or not active_model.startswith("gpt-")

    raw_response: Optional[str] = None

    if is_gemini and settings.effective_gemini_api_key:
        schema = response_model.model_json_schema()
        raw_response = await call_gemini_generate_content(
            prompt=prompt,
            model=active_model,
            system_instruction=system_instruction,
            json_mode=True,
            response_schema=schema,
            thinking_level=thinking_level,
        )
    elif settings.effective_openai_api_key:
        raw_response = await call_openai_chat_completions(
            prompt=prompt,
            model=active_model,
            system_instruction=system_instruction,
            json_mode=True,
        )

    if not raw_response:
        return None

    try:
        cleaned = _clean_json_text(raw_response)
        parsed_json = json.loads(cleaned)
        return response_model.model_validate(parsed_json)
    except Exception as ex:
        logger.error(f"Failed to validate model schema with {active_model}: {ex}. Raw: {raw_response[:300]}")
        return None


async def generate_text(
    prompt: str,
    model: Optional[str] = None,
    system_instruction: Optional[str] = None,
    thinking_level: Optional[str] = None,
) -> Optional[str]:
    """Generate free-form text response with thinking enabled."""
    active_model = model or settings.LLM_MODEL
    is_gemini = "gemini" in active_model.lower() or not active_model.startswith("gpt-")

    if is_gemini and settings.effective_gemini_api_key:
        return await call_gemini_generate_content(
            prompt=prompt,
            model=active_model,
            system_instruction=system_instruction,
            json_mode=False,
            thinking_level=thinking_level,
        )
    elif settings.effective_openai_api_key:
        return await call_openai_chat_completions(
            prompt=prompt,
            model=active_model,
            system_instruction=system_instruction,
            json_mode=False,
        )
    return None
