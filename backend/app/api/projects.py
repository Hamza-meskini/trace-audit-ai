"""Projects API — CRUD + stats."""

from datetime import datetime
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.project import Project
from app.models.document import Document, EvidenceChunk
from app.models.requirement import Requirement
from app.models.finding import Finding
from app.services import audit_progress
from app.services.excel_export import generate_audit_report_excel
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
    job = audit_progress.latest(project_id)
    if job and job["status"] in ("queued", "running", "cancelling"):
        raise HTTPException(409, "A project cannot be deleted while its audit is active")
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
    unknown = status_counts.get("Unknown", 0)
    not_applicable = status_counts.get("Not applicable", 0)

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
        unknown=unknown,
        not_applicable=not_applicable,
        documents=doc_count,
        evidence_segments=chunk_count,
        findings=finding_count,
    )


@router.get("/{project_id}/export/xlsx")
async def export_project_xlsx(project_id: str, db: AsyncSession = Depends(get_db)):
    """Export executive-grade, color-coded Excel audit report."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        xlsx_bytes = await generate_audit_report_excel(db, project_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate Excel report: {e}")

    safe_name = re.sub(r"[^\w\-.]", "_", project.name or "audit")
    filename = f"{project.audit_id or 'TraceAudit'}_{safe_name}_Audit_Report.xlsx"

    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )
