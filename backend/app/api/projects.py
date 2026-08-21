"""Projects API — CRUD + stats."""

from datetime import datetime
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.project import Project
from app.models.document import Document, EvidenceChunk
from app.models.requirement import Requirement
from app.models.finding import Finding
from app.schemas.project import (
    ProjectCreate,
    ProjectUpdate,
    ProjectResponse,
    ProjectStatsResponse,
)

router = APIRouter(prefix="/projects", tags=["Projects"])


def _generate_audit_id() -> str:
    year = datetime.now().year
    seq = uuid.uuid4().hex[:4].upper()
    return f"TA-{year}-{seq}"


@router.get("", response_model=list[ProjectResponse])
async def list_projects(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Project).order_by(Project.created_at.desc()))
    return result.scalars().all()


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(body: ProjectCreate, db: AsyncSession = Depends(get_db)):
    project = Project(
        name=body.name,
        audit_id=_generate_audit_id(),
        product_name=body.product_name,
        product_category=body.product_category,
        company=body.company,
        description=body.description,
    )
    db.add(project)
    await db.flush()
    await db.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(project_id: str, body: ProjectUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(project, field, value)

    await db.flush()
    await db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    await db.delete(project)


@router.get("/{project_id}/stats", response_model=ProjectStatsResponse)
async def get_project_stats(project_id: str, db: AsyncSession = Depends(get_db)):
    """Compute live aggregated statistics for a project."""
    # Verify project exists
    result = await db.execute(select(Project).where(Project.id == project_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")

    # Count requirements by coverage status
    reqs = await db.execute(
        select(Requirement.coverage_status, func.count())
        .where(Requirement.project_id == project_id)
        .group_by(Requirement.coverage_status)
    )
    status_counts = dict(reqs.all())
    total_reqs = sum(status_counts.values())
    supported = status_counts.get("Supported", 0)
    partial = status_counts.get("Partial", 0)
    missing = status_counts.get("Missing", 0)
    conflict = status_counts.get("Conflict", 0)

    # Count documents
    doc_count_result = await db.execute(
        select(func.count()).select_from(Document).where(Document.project_id == project_id)
    )
    doc_count = doc_count_result.scalar() or 0

    # Count evidence chunks across all project documents
    chunk_count_result = await db.execute(
        select(func.count())
        .select_from(EvidenceChunk)
        .join(Document)
        .where(Document.project_id == project_id)
    )
    chunk_count = chunk_count_result.scalar() or 0

    # Count findings
    finding_count_result = await db.execute(
        select(func.count()).select_from(Finding).where(Finding.project_id == project_id)
    )
    finding_count = finding_count_result.scalar() or 0

    coverage_pct = round((supported / total_reqs) * 100) if total_reqs > 0 else 0

    return ProjectStatsResponse(
        requirements=total_reqs,
        coverage=coverage_pct,
        supported=supported,
        partial=partial,
        missing=missing,
        conflict=conflict,
        documents=doc_count,
        evidence_segments=chunk_count,
        findings=finding_count,
    )
