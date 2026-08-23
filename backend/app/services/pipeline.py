"""End-to-end audit pipeline service.

Executes document parsing, requirement extraction, evidence retrieval,
hybrid verification (deterministic validators + batched LLM reasoning
with deterministic fallback), and findings generation for a project.
"""

import os
import uuid
import logging
from typing import Optional
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.project import Project
from app.models.document import Document, EvidenceChunk
from app.models.requirement import Requirement, RequirementEvidence
from app.models.finding import Finding
from app.services.ingestion import parse_document
from app.services.extraction import extract_requirements_from_text
from app.services.retrieval import retrieve_candidate_evidence
from app.services.classification import batch_assess_requirements

logger = logging.getLogger("traceaudit.pipeline")

# Filename keywords used to locate specification documents to extract requirements from
SPEC_DOC_KEYWORDS = ("spec", "requirement", "srs", "user_manual")


async def run_audit_pipeline(
    project_id: str,
    db: AsyncSession,
    model: Optional[str] = None,
    thinking_level: Optional[str] = None,
) -> dict:
    """Execute the full audit pipeline for a project."""
    active_model = model or "gemini-3.7-flash"
    active_thinking = thinking_level or "HIGH"
    # 1. Fetch project
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise ValueError(f"Project '{project_id}' not found")

    project.status = "Analyzing evidence"
    await db.flush()

    # 2. Ingest unparsed documents
    doc_result = await db.execute(
        select(Document).where(Document.project_id == project_id)
    )
    documents = doc_result.scalars().all()

    all_chunks_for_retrieval = []

    for doc in documents:
        # Check if chunks already exist
        chunk_check = await db.execute(
            select(EvidenceChunk).where(EvidenceChunk.document_id == doc.id)
        )
        existing_chunks = chunk_check.scalars().all()

        if not existing_chunks and os.path.exists(doc.storage_path):
            try:
                parsed_chunks = parse_document(doc.storage_path)
                doc.page_count = len(parsed_chunks)
                for pc in parsed_chunks:
                    ec = EvidenceChunk(
                        id=str(uuid.uuid4()),
                        document_id=doc.id,
                        page_number=pc.page_number,
                        chunk_index=pc.chunk_index,
                        content=pc.content,
                        metadata_json=pc.metadata,
                    )
                    db.add(ec)
                    all_chunks_for_retrieval.append({
                        "id": ec.id,
                        "document_id": doc.id,
                        "document_name": doc.original_filename,
                        "doc_type": doc.doc_type,
                        "page_number": ec.page_number,
                        "content": ec.content,
                    })
                doc.processing_status = "Indexed"
            except Exception as ex:
                logger.error(f"Failed parsing document {doc.original_filename}: {ex}")
                doc.processing_status = "Error"
        else:
            for ec in existing_chunks:
                all_chunks_for_retrieval.append({
                    "id": ec.id,
                    "document_id": doc.id,
                    "document_name": doc.original_filename,
                    "doc_type": doc.doc_type,
                    "page_number": ec.page_number,
                    "content": ec.content,
                })
            doc.processing_status = "Indexed"

    await db.flush()

    # 3. If no requirements exist yet, extract them from specification docs or create baseline
    req_result = await db.execute(
        select(Requirement).where(Requirement.project_id == project_id)
    )
    requirements = req_result.scalars().all()

    if not requirements:
        # Select the primary requirement specification document(s)
        # Priority 1: Primary SRS / Requirement specification files
        srs_candidates = [
            d for d in documents
            if any(k in d.original_filename.lower() for k in ("srs", "requirement", "prd", "prs", "product_requirements"))
            or ("requirement" in (d.doc_type or "").lower())
        ]

        if srs_candidates:
            spec_docs = srs_candidates
        else:
            # Priority 2: General specification documents, explicitly excluding test reports, datasheets, matrices
            spec_docs = [
                d for d in documents
                if ("spec" in d.original_filename.lower() or "spec" in (d.doc_type or "").lower())
                and not any(k in d.original_filename.lower() for k in ("test", "report", "datasheet", "matrix", "compliance", "safety_report", "lab"))
            ]

        if not spec_docs and documents:
            spec_docs = [documents[0]]

        extracted_count = 0
        seen_req_codes = set()

        for doc in spec_docs:
            if os.path.exists(doc.storage_path):
                doc_text = " ".join([c["content"] for c in all_chunks_for_retrieval if c["document_id"] == doc.id])
                extracted = await extract_requirements_from_text(
                    doc_text,
                    doc.original_filename,
                    model=active_model,
                    thinking_level=active_thinking,
                )
                for er in extracted:
                    if er.req_code in seen_req_codes:
                        continue
                    seen_req_codes.add(er.req_code)
                    extracted_count += 1
                    req = Requirement(
                        id=str(uuid.uuid4()),
                        project_id=project_id,
                        req_code=er.req_code,
                        title=er.title,
                        description=er.description,
                        category=er.category,
                        severity=er.severity,
                        source_document=doc.original_filename,
                    )
                    db.add(req)

        await db.flush()
        req_result = await db.execute(
            select(Requirement).where(Requirement.project_id == project_id)
        )
        requirements = req_result.scalars().all()

    # 4. Assess each requirement against all evidence chunks
    finding_idx = 1
    # Clear existing findings and requirement_evidence links for clean re-analysis
    await db.execute(
        delete(Finding).where(Finding.project_id == project_id)
    )

    # 4a. Retrieve candidate evidence per requirement
    req_items = []
    for req in requirements:
        # Clear existing evidence links for this requirement
        await db.execute(
            delete(RequirementEvidence).where(RequirementEvidence.requirement_id == req.id)
        )

        # Retrieve top candidate evidence chunks
        retrieved = retrieve_candidate_evidence(
            f"{req.title} {req.description or ''}",
            all_chunks_for_retrieval,
            top_k=4,
        )

        candidate_chunks = [
            {
                "id": r.chunk_id,
                "document_id": r.document_id,
                "document_name": r.document_name,
                "doc_type": r.doc_type,
                "page_number": r.page_number,
                "content": r.content,
            }
            for r in retrieved
        ]

        req_items.append({
            "req_code": req.req_code,
            "title": req.title,
            "description": req.description,
            "category": req.category,
            "candidate_chunks": candidate_chunks,
        })

    # 4b. Batched hybrid verification: deterministic validators first,
    # LLM multi-condition reasoning (with Databricks cascade fallback) for
    # inconclusive requirements — same engine as the evaluation benchmark.
    assessments = await batch_assess_requirements(
        req_items=req_items,
        model=active_model,
        thinking_level=active_thinking,
    )

    # 4c. Persist assessment results, evidence links, and findings
    for req in requirements:
        assessment = assessments.get(req.req_code)
        if assessment is None:
            logger.warning(f"No assessment produced for requirement {req.req_code}; skipping.")
            continue

        # Update requirement fields
        req.coverage_status = assessment.coverage_status
        req.confidence = assessment.confidence
        req.review_state = assessment.review_state
        req.ai_analysis = assessment.ai_analysis
        req.ai_recommendation = assessment.ai_recommendation
        req.sources_count = len(assessment.evidence_links)

        # Insert fresh RequirementEvidence links
        for ev_link in assessment.evidence_links:
            db.add(RequirementEvidence(
                id=str(uuid.uuid4()),
                requirement_id=req.id,
                evidence_chunk_id=ev_link.chunk_id,
                status=ev_link.status,
                label=ev_link.label,
                highlight=ev_link.highlight,
            ))

        # Generate a Finding if Partial, Missing, or Conflict
        if assessment.coverage_status in ("Partial", "Missing", "Conflict"):
            finding_type_map = {
                "Missing": "Missing evidence",
                "Partial": "Partial evidence",
                "Conflict": "Potential conflict",
            }
            finding = Finding(
                id=str(uuid.uuid4()),
                project_id=project_id,
                requirement_id=req.id,
                finding_code=f"F-{finding_idx:03d}",
                finding_type=finding_type_map.get(assessment.coverage_status, "Partial evidence"),
                severity=req.severity,
                review_state=assessment.review_state,
                category=req.category,
                sources_count=len(assessment.evidence_links),
                description=assessment.ai_analysis,
            )
            finding_idx += 1
            db.add(finding)

    # 5. Update document linked counts
    for doc in documents:
        # Count how many requirement evidence links point to this doc's chunks
        count_res = await db.execute(
            select(RequirementEvidence)
            .join(EvidenceChunk, RequirementEvidence.evidence_chunk_id == EvidenceChunk.id)
            .where(EvidenceChunk.document_id == doc.id)
        )
        doc.requirements_linked = len(count_res.scalars().all())

    project.status = "Analysis complete"
    await db.commit()

    return {
        "status": "success",
        "project_id": project_id,
        "model_used": active_model,
        "thinking_level": active_thinking,
        "requirements_analyzed": len(requirements),
        "documents_indexed": len(documents),
        "findings_generated": finding_idx - 1,
    }
