"""Settings API — AI model selection, Thinking level configuration, and workspace settings."""

from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException
from app.config import settings, SUPPORTED_MODELS, SUPPORTED_THINKING_LEVELS

router = APIRouter(prefix="/settings", tags=["Settings"])


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
async def update_ai_settings(body: UpdateAiSettingsRequest):
    """Update active LLM model and/or thinking level."""
    if body.model:
        valid_model_ids = {m["id"] for m in SUPPORTED_MODELS}
        if body.model not in valid_model_ids:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid model '{body.model}'. Supported: {', '.join(valid_model_ids)}",
            )
        settings.LLM_MODEL = body.model
        if "gemini" in body.model.lower():
            settings.LLM_PROVIDER = "gemini"
        else:
            settings.LLM_PROVIDER = "openai"

    if body.thinking_level:
        upper_level = body.thinking_level.upper()
        if upper_level not in SUPPORTED_THINKING_LEVELS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid thinking level '{body.thinking_level}'. Supported: {', '.join(SUPPORTED_THINKING_LEVELS)}",
            )
        settings.GEMINI_THINKING_LEVEL = upper_level

    return await get_ai_settings()
