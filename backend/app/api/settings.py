"""Settings API — AI model selection, Thinking level configuration, and workspace settings.

Changes to the active model / thinking level are persisted in the app_settings
table and re-applied to the runtime settings object on startup, so the UI
configuration survives server restarts. Provider inference is owned by
llm_client.resolve_llm_provider — no local copy of the routing rules here.
"""

from typing import Optional
from sqlalchemy import select
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Request

from app.config import settings, SUPPORTED_MODELS, SUPPORTED_THINKING_LEVELS
from app.database import get_db
from app.models.setting import AppSetting
from app.services.llm_client import resolve_llm_provider
from app.services import audit_progress
from app.services.identity import forwarded_identity_trusted, request_identity

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
    if settings.LLM_DATABRICKS_ONLY and resolve_llm_provider(model) != "databricks":
        # A model saved before Databricks-only mode must not re-enable another provider.
        model = settings.LLM_MODEL
        stored.pop("provider", None)
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
        available_models=_available_models(),
    )


def _available_models() -> list[dict]:
    if settings.LLM_DATABRICKS_ONLY:
        return [m for m in SUPPORTED_MODELS if m.get("provider") == "databricks"]
    return SUPPORTED_MODELS


def guard_settings_change(request: Request) -> None:
    """ARC-02: the AI settings are global, so changing them is restricted.

    Behind Databricks Apps SSO only users listed in SETTINGS_ADMINS may change
    them. Locally (no trusted identity) the route stays open for development;
    the cross-site write guard still applies. Never while an audit is active:
    a mid-run switch would mix models inside one run.
    """
    if forwarded_identity_trusted():
        identity = request_identity(request)
        admins = {name.strip().lower() for name in settings.SETTINGS_ADMINS if name and name.strip()}
        names = {value.lower() for value in (identity.user, identity.email) if value}
        if not identity.verified or not (names & admins):
            raise HTTPException(
                status_code=403,
                detail="Only workspace administrators listed in SETTINGS_ADMINS can change the AI settings.",
            )
    active = audit_progress.active_project_ids()
    if active:
        raise HTTPException(
            status_code=409,
            detail=f"{len(active)} audit(s) are running; change the AI settings after they finish.",
        )


@router.post("/ai", response_model=AiSettingsResponse)
async def update_ai_settings(
    body: UpdateAiSettingsRequest,
    request: Request,
    db=Depends(get_db, scope="function"),
):
    """Update and persist the active LLM model and/or thinking level."""
    guard_settings_change(request)
    model = settings.LLM_MODEL
    thinking_level = settings.GEMINI_THINKING_LEVEL

    if body.model:
        valid_model_ids = {m["id"] for m in _available_models()}
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
