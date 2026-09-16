"""Audit API — trigger pipeline and check status."""

import logging
from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.project import Project
from app.models.document import Document
from app.services import audit_progress
from app.services import audit_worker

logger = logging.getLogger("traceaudit.api")

router = APIRouter(prefix="/projects/{project_id}/audit", tags=["Audit"])


class AuditRunRequest(BaseModel):
    model: Optional[str] = None           # e.g. "gemini-3.7-flash", "system.ai.qwen35-122b-a10b"
    thinking_level: Optional[str] = None  # "HIGH", "MEDIUM", "LOW", "MINIMAL"


@router.get("")
async def audit_status(project_id: str, db: AsyncSession = Depends(get_db)):
    if not await db.get(Project, project_id):
        raise HTTPException(404, "Project not found")
    return audit_progress.latest(project_id)


@router.post("", status_code=202)
async def trigger_audit(
    project_id: str,
    body: Optional[AuditRunRequest] = None,
    db: AsyncSession = Depends(get_db),
):
    """Queue a local job and return immediately; GET exposes durable progress."""
    if not await db.get(Project, project_id):
        raise HTTPException(404, "Project not found")
    documents = (await db.execute(select(Document.id).where(Document.project_id == project_id))).first()
    if not documents:
        raise HTTPException(422, "Upload documents before starting an audit")
    try:
        model_name = body.model if body and body.model else None
        thinking = body.thinking_level if body and body.thinking_level else None
        job = audit_progress.start(project_id, model_name, thinking)
        audit_worker.wake()
        return job
    except ValueError as ve:
        raise HTTPException(status_code=409, detail=str(ve))
    except Exception:
        logger.exception(f"Audit pipeline failed for project '{project_id}'")
        raise HTTPException(status_code=500, detail="Audit pipeline error. Check server logs for details.")


@router.post("/cancel")
async def cancel_audit(project_id: str, db: AsyncSession = Depends(get_db)):
    if not await db.get(Project, project_id):
        raise HTTPException(404, "Project not found")
    job = audit_progress.request_cancel(project_id)
    if job is None:
        raise HTTPException(404, "No audit run found")
    return job
