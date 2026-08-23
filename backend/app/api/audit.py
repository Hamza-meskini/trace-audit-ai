"""Audit API — trigger pipeline and check status."""

import logging
from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.pipeline import run_audit_pipeline

logger = logging.getLogger("traceaudit.api")

router = APIRouter(prefix="/projects/{project_id}/audit", tags=["Audit"])


class AuditRunRequest(BaseModel):
    model: Optional[str] = None           # e.g. "gemini-3.7-flash", "system.ai.qwen35-122b-a10b"
    thinking_level: Optional[str] = None  # "HIGH", "MEDIUM", "LOW", "MINIMAL"


@router.post("")
async def trigger_audit(
    project_id: str,
    body: Optional[AuditRunRequest] = None,
    db: AsyncSession = Depends(get_db),
):
    """Trigger an immediate audit analysis pipeline on the given project with optional model and thinking level."""
    try:
        model_name = body.model if body and body.model else None
        thinking = body.thinking_level if body and body.thinking_level else None
        result = await run_audit_pipeline(project_id, db, model=model_name, thinking_level=thinking)
        return result
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception:
        logger.exception(f"Audit pipeline failed for project '{project_id}'")
        raise HTTPException(status_code=500, detail="Audit pipeline error. Check server logs for details.")
