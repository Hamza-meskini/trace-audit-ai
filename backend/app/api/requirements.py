"""Requirements API — list, get (with evidence), create."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.requirement import Requirement, RequirementEvidence
from app.models.document import EvidenceChunk, Document
from app.schemas.requirement import RequirementResponse, RequirementCreate, EvidenceResponse

router = APIRouter(prefix="/projects/{project_id}/requirements", tags=["Requirements"])


def _build_evidence_response(link: RequirementEvidence) -> EvidenceResponse:
    """Convert a RequirementEvidence join into a flat evidence response."""
    chunk = link.evidence_chunk
    doc_name = ""
    if chunk and chunk.document:
        doc_name = chunk.document.original_filename
    return EvidenceResponse(
        id=link.id,
        document_name=doc_name,
        page_number=chunk.page_number if chunk else None,
        quote=chunk.content if chunk else "",
        status=link.status,
        label=link.label,
        highlight=link.highlight,
    )


@router.get("", response_model=list[RequirementResponse])
async def list_requirements(
    project_id: str,
    category: str | None = Query(None),
    status: str | None = Query(None),
    severity: str | None = Query(None),
    review: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(Requirement)
        .where(Requirement.project_id == project_id)
        .options(
            selectinload(Requirement.evidence_links)
            .selectinload(RequirementEvidence.evidence_chunk)
            .selectinload(EvidenceChunk.document)
        )
        .order_by(Requirement.req_code)
    )

    if category:
        query = query.where(Requirement.category == category)
    if status:
        query = query.where(Requirement.coverage_status == status)
    if severity:
        query = query.where(Requirement.severity == severity)
    if review:
        query = query.where(Requirement.review_state == review)

    result = await db.execute(query)
    reqs = result.scalars().all()

    responses = []
    for req in reqs:
        evidence = [_build_evidence_response(link) for link in req.evidence_links]
        resp = RequirementResponse(
            id=req.id,
            project_id=req.project_id,
            req_code=req.req_code,
            title=req.title,
            description=req.description,
            category=req.category,
            source_document=req.source_document,
            sources_count=req.sources_count,
            coverage_status=req.coverage_status,
            confidence=req.confidence,
            review_state=req.review_state,
            severity=req.severity,
            ai_analysis=req.ai_analysis,
            ai_recommendation=req.ai_recommendation,
            evidence=evidence,
            created_at=req.created_at,
            updated_at=req.updated_at,
        )
        responses.append(resp)

    return responses


@router.get("/{requirement_id}", response_model=RequirementResponse)
async def get_requirement(project_id: str, requirement_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Requirement)
        .where(Requirement.id == requirement_id, Requirement.project_id == project_id)
        .options(
            selectinload(Requirement.evidence_links)
            .selectinload(RequirementEvidence.evidence_chunk)
            .selectinload(EvidenceChunk.document)
        )
    )
    req = result.scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=404, detail="Requirement not found")

    evidence = [_build_evidence_response(link) for link in req.evidence_links]

    return RequirementResponse(
        id=req.id,
        project_id=req.project_id,
        req_code=req.req_code,
        title=req.title,
        description=req.description,
        category=req.category,
        source_document=req.source_document,
        sources_count=req.sources_count,
        coverage_status=req.coverage_status,
        confidence=req.confidence,
        review_state=req.review_state,
        severity=req.severity,
        ai_analysis=req.ai_analysis,
        ai_recommendation=req.ai_recommendation,
        evidence=evidence,
        created_at=req.created_at,
        updated_at=req.updated_at,
    )


@router.post("", response_model=RequirementResponse, status_code=201)
async def create_requirement(
    project_id: str,
    body: RequirementCreate,
    db: AsyncSession = Depends(get_db),
):
    req = Requirement(
        project_id=project_id,
        req_code=body.req_code,
        title=body.title,
        description=body.description,
        category=body.category,
        source_document=body.source_document,
        severity=body.severity,
    )
    db.add(req)
    await db.flush()
    await db.refresh(req)

    return RequirementResponse(
        id=req.id,
        project_id=req.project_id,
        req_code=req.req_code,
        title=req.title,
        description=req.description,
        category=req.category,
        source_document=req.source_document,
        sources_count=req.sources_count,
        coverage_status=req.coverage_status,
        confidence=req.confidence,
        review_state=req.review_state,
        severity=req.severity,
        ai_analysis=req.ai_analysis,
        ai_recommendation=req.ai_recommendation,
        evidence=[],
        created_at=req.created_at,
        updated_at=req.updated_at,
    )
