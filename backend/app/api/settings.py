"""Settings API — AI model selection, Thinking level configuration, and workspace settings.

Changes to the active model / thinking level are persisted in the app_settings
table and re-applied to the runtime settings object on startup, so the UI
configuration survives server restarts. Provider inference is owned by
llm_client.resolve_llm_provider — no local copy of the routing rules here.
"""

from typing import Optional
from sqlalchemy import select
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException

from app.config import settings, SUPPORTED_MODELS, SUPPORTED_THINKING_LEVELS
from app.database import get_db
from app.models.setting import AppSetting
from app.services.llm_client import resolve_llm_provider

router = APIRouter(prefix="/settings", tags=["Settings"])

SETTING_KEYS = {
    "model": "LLM_MODEL",
    "provider": "LLM_PROVIDER",
    "thinking_level": "GEMINI_THINKING_LEVEL",
}


class UpdateAiSettingsRequest(BaseModel):
    model: Optional[str] = None
    thinking_level: Optional[str] = None  # "LOW", "MEDIUM", "HIGH", "MINIMAL"


class AiSettingsResponse(BaseModel):
    current_model: str
    provider: str
    thinking_level: str
    supported_thinking_levels: list[str]
    has_gemini_key: bool
    has_openai_key: bool
    available_models: list[dict]


def _apply_runtime(model: str, provider: str, thinking_level: str) -> None:
    """Apply a persisted configuration to the runtime settings object."""
    settings.LLM_MODEL = model
    settings.LLM_PROVIDER = provider
    settings.GEMINI_THINKING_LEVEL = thinking_level


async def load_persisted_settings(db) -> None:
    """Re-apply persisted AI settings at application startup (idempotent)."""
    result = await db.execute(select(AppSetting).where(AppSetting.key.in_(SETTING_KEYS)))
    stored = {row.key: row.value for row in result.scalars().all()}
    if not stored:
        return
    model = stored.get("model") or settings.LLM_MODEL
    provider = stored.get("provider") or resolve_llm_provider(model)
    thinking_level = stored.get("thinking_level") or settings.GEMINI_THINKING_LEVEL
    _apply_runtime(model, provider, thinking_level)


@router.get("/ai", response_model=AiSettingsResponse)
async def get_ai_settings():
    """Return the current AI configuration, thinking level, available models, and API key status."""
    has_gemini = bool(settings.effective_gemini_api_key)
    has_openai = bool(settings.effective_openai_api_key)

    return AiSettingsResponse(
        current_model=settings.LLM_MODEL,
        provider=settings.LLM_PROVIDER,
        thinking_level=settings.GEMINI_THINKING_LEVEL,
        supported_thinking_levels=SUPPORTED_THINKING_LEVELS,
        has_gemini_key=has_gemini,
        has_openai_key=has_openai,
        available_models=SUPPORTED_MODELS,
    )


@router.post("/ai", response_model=AiSettingsResponse)
async def update_ai_settings(body: UpdateAiSettingsRequest, db=Depends(get_db)):
    """Update and persist the active LLM model and/or thinking level."""
    model = settings.LLM_MODEL
    thinking_level = settings.GEMINI_THINKING_LEVEL

    if body.model:
        valid_model_ids = {m["id"] for m in SUPPORTED_MODELS}
        if body.model not in valid_model_ids:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid model '{body.model}'. Supported: {', '.join(valid_model_ids)}",
            )
        model = body.model

    if body.thinking_level:
        upper_level = body.thinking_level.upper()
        if upper_level not in SUPPORTED_THINKING_LEVELS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid thinking level '{body.thinking_level}'. Supported: {', '.join(SUPPORTED_THINKING_LEVELS)}",
            )
        thinking_level = upper_level

    provider = resolve_llm_provider(model)

    for db_key, value in (
        ("model", model),
        ("provider", provider),
        ("thinking_level", thinking_level),
    ):
        existing = await db.execute(select(AppSetting).where(AppSetting.key == db_key))
        row = existing.scalar_one_or_none()
        if row is None:
            db.add(AppSetting(key=db_key, value=value))
        else:
            row.value = value
    await db.commit()

    _apply_runtime(model, provider, thinking_level)
    return await get_ai_settings()
