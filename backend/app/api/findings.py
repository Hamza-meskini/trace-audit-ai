"""Findings API — list, get, update."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.finding import Finding
from app.models.requirement import Requirement
from app.schemas.finding import FindingResponse, FindingUpdate

router = APIRouter(prefix="/projects/{project_id}/findings", tags=["Findings"])


@router.get("", response_model=list[FindingResponse])
async def list_findings(
    project_id: str,
    severity: str | None = Query(None),
    finding_type: str | None = Query(None),
    review_state: str | None = Query(None),
    category: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(Finding)
        .where(Finding.project_id == project_id)
        .options(selectinload(Finding.requirement))
        .order_by(Finding.created_at.desc())
    )

    if severity:
        query = query.where(Finding.severity == severity)
    if finding_type:
        query = query.where(Finding.finding_type == finding_type)
    if review_state:
        query = query.where(Finding.review_state == review_state)
    if category:
        query = query.where(Finding.category == category)

    result = await db.execute(query)
    findings = result.scalars().all()

    return [
        FindingResponse(
            id=f.id,
            project_id=f.project_id,
            requirement_id=f.requirement_id,
            finding_code=f.finding_code,
            finding_type=f.finding_type,
            severity=f.severity,
            review_state=f.review_state,
            assigned_to=f.assigned_to,
            description=f.description,
            sources_count=f.sources_count,
            category=f.category,
            requirement_title=f.requirement.title if f.requirement else None,
            created_at=f.created_at,
            updated_at=f.updated_at,
        )
        for f in findings
    ]


@router.patch("/{finding_id}", response_model=FindingResponse)
async def update_finding(
    project_id: str,
    finding_id: str,
    body: FindingUpdate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Finding)
        .where(Finding.id == finding_id, Finding.project_id == project_id)
        .options(selectinload(Finding.requirement))
    )
    finding = result.scalar_one_or_none()
    if not finding:
        raise HTTPException(status_code=404, detail="Finding not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(finding, field, value)

    await db.flush()
    await db.refresh(finding)

    return FindingResponse(
        id=finding.id,
        project_id=finding.project_id,
        requirement_id=finding.requirement_id,
        finding_code=finding.finding_code,
        finding_type=finding.finding_type,
        severity=finding.severity,
        review_state=finding.review_state,
        assigned_to=finding.assigned_to,
        description=finding.description,
        sources_count=finding.sources_count,
        category=finding.category,
        requirement_title=finding.requirement.title if finding.requirement else None,
        created_at=finding.created_at,
        updated_at=finding.updated_at,
    )
