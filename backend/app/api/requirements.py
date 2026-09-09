"""Requirements API — list, get (with evidence), create."""

import re
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.requirement import Requirement, RequirementEvidence
from app.models.document import EvidenceChunk, Document
from app.schemas.requirement import RequirementResponse, RequirementCreate, EvidenceResponse, RequirementReviewRequest
from app.models.finding import Finding
from app.services import audit_progress

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
        document_id=chunk.document_id if chunk else None,
        chunk_id=chunk.id if chunk else None,
        metadata=public_metadata(chunk.metadata_json) if chunk else {},
    )


def public_metadata(metadata):
    """Allowlist document structure, never leak local storage/cache paths."""
    return {key: value for key, value in (metadata or {}).items() if key in {
        "block_type", "rows", "table", "caption", "section_path", "page_numbers", "bounding_boxes",
        "visual_analysis", "document_profile", "document_diagnostics", "requires_visual_reasoning",
    }}


def block_response(chunk):
    return {"id": chunk.id, "document_id": chunk.document_id, "page_number": chunk.page_number,
            "content": chunk.content, "metadata": public_metadata(chunk.metadata_json)}


def chunk_pages(chunk):
    pages = (chunk.metadata_json or {}).get("page_numbers", [])
    return {page for page in [chunk.page_number, *(pages if isinstance(pages, list) else [])]
            if isinstance(page, int) and page > 0}


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

    response = RequirementResponse(
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
    data = req.extracted_parameters or {}
    response.contract = {key: data[key] for key in (
        "conditions", "semantic_clauses", "logic", "logic_tree", "clause_coverage",
        "unmapped_obligations", "contract_complete", "decomposition_confidence",
        "ambiguities", "validation_issues", "decomposition_method",
    ) if key in data}
    verification = data.get("verification", {})
    response.condition_results = verification.get("condition_results", [])
    response.diagnostics = verification.get("diagnostics", {})
    response.diagnostics = {**response.diagnostics, "evidence_catalog": verification.get("evidence_catalog", []),
                            "model": verification.get("model"), "assessed_at": verification.get("assessed_at")}
    response.review_history = data.get("review_history", [])
    docs = (await db.execute(select(Document).where(
        Document.project_id == project_id,
        Document.original_filename == req.source_document,
    ))).scalars().all()
    stored_doc_id = data.get("source_document_id")
    source_doc = next((doc for doc in docs if doc.id == stored_doc_id), None)
    if source_doc is None and len(docs) == 1:
        source_doc = docs[0]
    if source_doc:
        response.source_document_id = source_doc.id
        chunks = (await db.execute(select(EvidenceChunk).where(
            EvidenceChunk.document_id == source_doc.id,
        ).order_by(EvidenceChunk.chunk_index))).scalars().all()
        pattern = re.compile(r"(?<![\w-])" + re.escape(req.req_code) + r"(?![\w-])", re.I)
        pages = {page for c in chunks if pattern.search(c.content) for page in chunk_pages(c)}
        response.source_blocks = [block_response(c) for c in chunks
                                  if chunk_pages(c) & pages or pattern.search(c.content)]
    return response


@router.post("/{requirement_id}/reviews")
async def save_review(project_id: str, requirement_id: str, body: RequirementReviewRequest,
                      db: AsyncSession = Depends(get_db)):
    job = audit_progress.latest(project_id)
    if job and job["status"] in ("queued", "running"):
        raise HTTPException(409, "Wait for the active audit to finish before saving a review")
    req = (await db.execute(select(Requirement).where(
        Requirement.id == requirement_id, Requirement.project_id == project_id,
    ).with_for_update())).scalar_one_or_none()
    if req is None:
        raise HTTPException(404, "Requirement not found")
    if not body.reviewer.strip() or not body.comment.strip():
        raise HTTPException(422, "Reviewer and rationale cannot be blank")
    event = {"id": str(uuid.uuid4()), "action": body.action, "reviewer": body.reviewer.strip(),
             "comment": body.comment.strip(), "created_at": datetime.now(timezone.utc).isoformat(),
             "ai_verdict": req.coverage_status, "previous_review_state": req.review_state,
             "assessment_at": (req.extracted_parameters or {}).get("verification", {}).get("assessed_at")}
    data = dict(req.extracted_parameters or {})
    data["review_history"] = [*data.get("review_history", []), event]
    req.extracted_parameters = data
    if body.action != "Comment":
        req.review_state = body.action
        findings = (await db.execute(select(Finding).where(
            Finding.project_id == project_id, Finding.requirement_id == requirement_id,
        ))).scalars().all()
        for finding in findings:
            finding.review_state = body.action
    await db.flush()
    return event


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
