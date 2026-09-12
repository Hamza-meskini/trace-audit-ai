"""End-to-end audit pipeline service.

Executes document parsing, requirement extraction, evidence retrieval,
hybrid verification (deterministic validators + batched LLM reasoning
with deterministic fallback), and findings generation for a project.
"""

import os
import uuid
import logging
import time
import asyncio
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.project import Project
from app.models.document import Document, EvidenceChunk
from app.models.requirement import Requirement, RequirementEvidence
from app.models.finding import Finding
from app.services.ingestion import (
    INGESTION_SCHEMA_VERSION,
    file_sha256,
    parse_document_with_metadata,
)
from app.services.extraction import extract_requirements_from_text
from app.services.retrieval import (
    build_requirement_search_queries,
    precompute_chunk_embeddings,
    retrieve_candidate_evidence_hybrid,
)
from app.services.classification import batch_assess_requirements
from app.services.taxonomy import finding_type_for
from app.config import settings
from app.services.document_classifier import (
    ROLE_DISPLAY_NAMES,
    discover_specification_documents,
    profile_documents,
)
from app.services.visual_analysis import describe_retrieved_figures
from app.services.requirement_visual_recovery import recover_requirement_text_from_pages

logger = logging.getLogger("traceaudit.pipeline")


def _retrieval_chunk(
    chunk: EvidenceChunk,
    document: Document,
) -> dict:
    metadata = dict(chunk.metadata_json or {})
    return {
        "id": chunk.id,
        "document_id": document.id,
        "document_name": document.original_filename,
        "doc_type": document.doc_type,
        "page_number": chunk.page_number,
        "content": chunk.content,
        "metadata": metadata,
        "document_profile": metadata.get("document_profile"),
    }


def _cache_matches_source(existing_chunks: list[EvidenceChunk], storage_path: str) -> bool:
    if not existing_chunks or not os.path.exists(storage_path):
        return False
    metadata = existing_chunks[0].metadata_json or {}
    if metadata.get("ingestion_schema_version") != INGESTION_SCHEMA_VERSION:
        return False
    cached_sha = metadata.get("source_sha256")
    if not (cached_sha and cached_sha == file_sha256(storage_path)):
        return False
    requested_parser = settings.TRACEAUDIT_DOCUMENT_PARSER.strip().lower()
    cached_backend = str(metadata.get("parser_backend") or "").lower()
    if requested_parser in {"databricks", "databricks-auto"}:
        return cached_backend.startswith("databricks-ai-parse")
    if requested_parser in {"pymupdf", "docling"}:
        return requested_parser in cached_backend
    return True


async def run_audit_pipeline(
    project_id: str,
    db: AsyncSession,
    model: Optional[str] = None,
    thinking_level: Optional[str] = None,
    progress=None,
) -> dict:
    """Execute the full audit pipeline for a project."""
    active_model = model or settings.LLM_MODEL
    active_thinking = thinking_level or settings.GEMINI_THINKING_LEVEL
    pipeline_started = time.perf_counter()
    stage_timings: dict[str, float] = {}
    ingestion_diagnostics: list[dict] = []
    report_progress = progress or (lambda *args: None)
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
    evidence_chunk_models: dict[str, EvidenceChunk] = {}

    ingestion_started = time.perf_counter()
    report_progress("ingestion", 0, len(documents), "Reading source text, tables and figures")
    for doc_index, doc in enumerate(documents):
        # Check if chunks already exist
        chunk_check = await db.execute(
            select(EvidenceChunk).where(EvidenceChunk.document_id == doc.id)
        )
        existing_chunks = chunk_check.scalars().all()

        cache_is_current = _cache_matches_source(existing_chunks, doc.storage_path)

        if (not existing_chunks or not cache_is_current) and os.path.exists(doc.storage_path):
            try:
                parsed_document = await asyncio.to_thread(parse_document_with_metadata, doc.storage_path)
                parsed_chunks = parsed_document.chunks

                # Parse successfully before replacing stale rows, so a parser
                # failure never destroys the last usable index.
                if existing_chunks:
                    stale_ids = [chunk.id for chunk in existing_chunks]
                    await db.execute(
                        delete(RequirementEvidence).where(
                            RequirementEvidence.evidence_chunk_id.in_(stale_ids)
                        )
                    )
                    await db.execute(
                        delete(EvidenceChunk).where(EvidenceChunk.document_id == doc.id)
                    )

                doc.page_count = parsed_document.page_count or None
                ingestion_diagnostics.append({
                    "document_id": doc.id,
                    "document_name": doc.original_filename,
                    "cache": "refreshed" if existing_chunks else "created",
                    **parsed_document.diagnostics,
                })
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
                    evidence_chunk_models[ec.id] = ec
                    all_chunks_for_retrieval.append(_retrieval_chunk(ec, doc))
                doc.processing_status = "Indexed"
            except Exception as ex:
                logger.error(f"Failed parsing document {doc.original_filename}: {ex}")
                doc.processing_status = "Error"
        else:
            for ec in existing_chunks:
                evidence_chunk_models[ec.id] = ec
                all_chunks_for_retrieval.append(_retrieval_chunk(ec, doc))
            cached_diagnostics = (
                (existing_chunks[0].metadata_json or {}).get("document_diagnostics", {})
                if existing_chunks else {}
            )
            ingestion_diagnostics.append({
                "document_id": doc.id,
                "document_name": doc.original_filename,
                "cache": "hit",
                **cached_diagnostics,
            })
            doc.processing_status = "Indexed"

        report_progress("ingestion", doc_index + 1, len(documents), f"Processed {doc.original_filename}: {doc.processing_status}")

    await db.flush()
    stage_timings["ingestion_seconds"] = round(time.perf_counter() - ingestion_started, 3)

    # Profile every document once from content and structure. The profile is
    # cached on chunk metadata and reused by retrieval and evidence
    # qualification; filenames remain only weak hints.
    profiling_started = time.perf_counter()
    report_progress("profiling", 0, len(documents), "Identifying document roles from their content")
    document_profiles = await profile_documents(
        documents=documents,
        all_chunks=all_chunks_for_retrieval,
        model=active_model,
        thinking_level=active_thinking,
    )
    for doc in documents:
        profile = document_profiles.get(doc.id)
        if not profile:
            continue
        profile_data = profile.model_dump()
        doc.doc_type = ROLE_DISPLAY_NAMES.get(profile.primary_role, doc.doc_type)
        for chunk in all_chunks_for_retrieval:
            if chunk.get("document_id") != doc.id:
                continue
            chunk["doc_type"] = doc.doc_type
            chunk["document_profile"] = profile_data
            model_chunk = evidence_chunk_models.get(chunk["id"])
            if model_chunk is not None:
                metadata = dict(model_chunk.metadata_json or {})
                metadata["document_profile"] = profile_data
                model_chunk.metadata_json = metadata

    await db.flush()
    stage_timings["document_profiling_seconds"] = round(
        time.perf_counter() - profiling_started, 3
    )

    # Discover specification documents from the same persisted profiles.
    spec_docs, spec_doc_names = await discover_specification_documents(
        documents=documents,
        all_chunks=all_chunks_for_retrieval,
        model=active_model,
        thinking_level=active_thinking,
        document_profiles=document_profiles,
    )

    # 3. If no requirements exist yet, extract them from specification docs or create baseline
    req_result = await db.execute(
        select(Requirement).where(Requirement.project_id == project_id)
    )
    requirements = req_result.scalars().all()
    requirement_visual_diagnostics: list[dict] = []

    if not requirements:
        report_progress("extraction", 0, len(spec_docs), "Extracting requirement clauses and atomic conditions")
        extraction_started = time.perf_counter()
        extracted_count = 0
        seen_req_codes = set()

        for doc in spec_docs:
            if os.path.exists(doc.storage_path):
                source_chunks = [
                    chunk for chunk in all_chunks_for_retrieval
                    if chunk["document_id"] == doc.id
                ]
                doc_text = "\n\n".join(chunk["content"] for chunk in source_chunks)
                recovery = await recover_requirement_text_from_pages(
                    file_path=doc.storage_path,
                    chunks=source_chunks,
                    model=active_model,
                    chunk_models=evidence_chunk_models,
                )
                requirement_visual_diagnostics.append({
                    "document_id": doc.id,
                    "document_name": doc.original_filename,
                    **{key: value for key, value in recovery.items() if key != "text"},
                })
                if recovery.get("text"):
                    doc_text = f"{doc_text}\n\n{recovery['text']}"
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
                        extracted_parameters={
                            "source_document_id": doc.id,
                            "parameters": [p.model_dump() for p in er.parameters],
                            "conditions": [c.model_dump() for c in er.conditions],
                            "semantic_clauses": [
                                item.model_dump() for item in er.semantic_clauses
                            ],
                            "clause_coverage": [
                                item.model_dump() for item in er.clause_coverage
                            ],
                            "unmapped_obligations": list(er.unmapped_obligations),
                            "contract_complete": er.contract_complete,
                            "logic": er.logic.model_dump(),
                            "logic_tree": er.logic_tree,
                            "decomposition_confidence": er.decomposition_confidence,
                            "ambiguities": list(er.ambiguities),
                            "validation_issues": list(er.validation_issues),
                            "decomposition_method": er.decomposition_method,
                        },
                    )
                    db.add(req)

        stage_timings["requirement_extraction_seconds"] = round(
            time.perf_counter() - extraction_started, 3
        )

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

    # 4a. Pre-compute semantic embeddings for all evidence chunks (single batch call)
    # This avoids redundant API calls when retrieving evidence for each requirement.
    report_progress("retrieval", 0, len(requirements), "Preparing evidence search")
    chunk_embeddings = await precompute_chunk_embeddings(all_chunks_for_retrieval)
    logger.info(f"Pre-computed embeddings for {len(all_chunks_for_retrieval)} chunks")

    # 4b. Retrieve candidate evidence per requirement (hybrid BM25 + semantic)
    retrieval_started = time.perf_counter()
    req_items = []
    for req in requirements:
        # Clear existing evidence links for this requirement
        await db.execute(
            delete(RequirementEvidence).where(RequirementEvidence.requirement_id == req.id)
        )

        # Hybrid retrieval: BM25 + Gemini embedding cosine similarity.
        # Spec docs are excluded from candidates (self-referential, they contain
        # the requirement text itself and always outrank true evidence).
        extraction_data = req.extracted_parameters or {}
        structured_conditions = extraction_data.get("conditions", [])
        search_queries = build_requirement_search_queries(
            req.req_code,
            req.title,
            req.description or "",
            structured_conditions,
            extraction_data.get("semantic_clauses", []),
        )
        primary_query = search_queries[0] if search_queries else f"{req.req_code} {req.title} {req.description or ''}"
        retrieved = await retrieve_candidate_evidence_hybrid(
            primary_query,
            all_chunks_for_retrieval,
            chunk_embeddings=chunk_embeddings,
            top_k=8,
            exclude_doc_names=spec_doc_names,
            condition_queries=search_queries[1:],
        )

        candidate_chunks = [
            {
                "id": r.chunk_id,
                "document_id": r.document_id,
                "document_name": r.document_name,
                "doc_type": r.doc_type,
                "page_number": r.page_number,
                "content": r.content,
                "document_profile": r.document_profile,
                "metadata": r.metadata,
            }
            for r in retrieved
        ]

        req_items.append({
            "req_code": req.req_code,
            "title": req.title,
            "description": req.description,
            "category": req.category,
            "conditions": structured_conditions,
            "semantic_clauses": extraction_data.get("semantic_clauses", []),
            "clause_coverage": extraction_data.get("clause_coverage", []),
            "unmapped_obligations": extraction_data.get("unmapped_obligations", []),
            # None means a legacy database record that pre-dates completeness
            # reporting. It is not silently treated as an explicit failure.
            "contract_complete": extraction_data.get("contract_complete"),
            "logic": extraction_data.get("logic"),
            "logic_tree": extraction_data.get("logic_tree"),
            "decomposition_confidence": extraction_data.get("decomposition_confidence"),
            "ambiguities": extraction_data.get("ambiguities", []),
            "validation_issues": extraction_data.get("validation_issues", []),
            "candidate_chunks": candidate_chunks,
            "search_queries": search_queries,
            "spec_doc_names": spec_doc_names,
        })
        report_progress("retrieval", len(req_items), len(requirements), f"Located evidence for {req.req_code}")

    stage_timings["retrieval_seconds"] = round(time.perf_counter() - retrieval_started, 3)
    retrieval_diagnostics = {
        "requirements": len(req_items),
        "requirements_without_candidates": sum(
            not item.get("candidate_chunks") for item in req_items
        ),
        "candidate_chunks_total": sum(
            len(item.get("candidate_chunks", [])) for item in req_items
        ),
        "structured_table_candidates": sum(
            (candidate.get("metadata") or {}).get("block_type") == "table"
            for item in req_items for candidate in item.get("candidate_chunks", [])
        ),
        "figure_candidates": sum(
            (candidate.get("metadata") or {}).get("block_type") == "figure"
            for item in req_items for candidate in item.get("candidate_chunks", [])
        ),
        "search_queries_total": sum(len(item.get("search_queries", [])) for item in req_items),
    }

    # Render and describe only figures selected by retrieval. Descriptions are
    # persisted on the chunk and reused across requirements and later runs.
    vision_started = time.perf_counter()
    report_progress("vision", 0, 0, "Interpreting retrieved figures; provider calls may take several minutes")
    visual_diagnostics = await describe_retrieved_figures(
        req_items,
        {document.id: document for document in documents},
        evidence_chunk_models,
        model=active_model,
    )
    stage_timings["visual_analysis_seconds"] = round(
        time.perf_counter() - vision_started, 3
    )

    # Optional compact Databricks extraction runs only on the retrieved
    # excerpts, after any selected figures have received visual descriptions.
    # It is advisory context for the whole-clause reasoner and never replaces
    # the raw evidence candidates or their source metadata.
    targeted_started = time.perf_counter()
    from app.services.databricks_document_ai import enrich_targeted_evidence_items

    targeted_stats = await enrich_targeted_evidence_items(req_items)
    stage_timings["targeted_extraction_seconds"] = round(time.perf_counter() - targeted_started, 3)
    retrieval_diagnostics["targeted_extraction"] = targeted_stats

    # 4b. Batched hybrid verification: deterministic validators first,
    # LLM multi-condition reasoning (with Databricks cascade fallback) for
    # inconclusive requirements — same engine used by offline evaluation.
    reasoning_started = time.perf_counter()
    report_progress("reasoning", 0, len(requirements), "Evaluating conditions, citations and review safety")
    assessments = await batch_assess_requirements(
        req_items=req_items,
        model=active_model,
        thinking_level=active_thinking,
        spec_doc_names=spec_doc_names,
        progress=lambda done, total: report_progress("reasoning", done, total, f"{done} of {total} requirements evaluated"),
    )
    stage_timings["reasoning_seconds"] = round(time.perf_counter() - reasoning_started, 3)
    reasoning_diagnostics = {
        "assessments_produced": len(assessments),
        "assessments_missing": max(0, len(requirements) - len(assessments)),
        "coverage_statuses": {
            status: sum(
                assessment.coverage_status == status for assessment in assessments.values()
            )
            for status in ("Supported", "Partial", "Missing", "Conflict", "Unknown")
        },
        "review_required": sum(
            str(assessment.review_state).lower() in {"needs review", "open"}
            for assessment in assessments.values()
        ),
    }

    # 4c. Persist assessment results, evidence links, and findings
    report_progress("saving", 0, len(requirements), "Saving traceable decisions and findings")
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
        data = dict(req.extracted_parameters or {})
        candidates = next((item["candidate_chunks"] for item in req_items if item["req_code"] == req.req_code), [])
        data["verification"] = {
            "assessed_at": datetime.now(timezone.utc).isoformat(),
            "condition_results": [item.model_dump() for item in assessment.condition_results],
            "diagnostics": assessment.pipeline_diagnostics,
            "model": active_model,
            "targeted_extraction": next((
                item.get("targeted_extraction")
                for item in req_items if item["req_code"] == req.req_code
            ), None),
            "evidence_catalog": [
                {"evidence_id": f"E{index}", "chunk_id": item["id"], "document_id": item["document_id"],
                 "document_name": item["document_name"], "page_number": item.get("page_number")}
                for index, item in enumerate(candidates, 1)
            ],
        }
        req.extracted_parameters = data

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

        # Generate a Finding if Partial, Missing, Conflict, or Unknown (inconclusive)
        if assessment.coverage_status in ("Partial", "Missing", "Conflict", "Unknown"):
            finding = Finding(
                id=str(uuid.uuid4()),
                project_id=project_id,
                requirement_id=req.id,
                finding_code=f"F-{finding_idx:03d}",
                finding_type=finding_type_for(assessment.coverage_status),
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

    stage_timings["total_seconds"] = round(time.perf_counter() - pipeline_started, 3)
    logger.info(
        "Audit pipeline diagnostics: timings=%s ingestion=%s visual=%s",
        stage_timings,
        ingestion_diagnostics,
        visual_diagnostics,
    )

    return {
        "status": "success",
        "project_id": project_id,
        "model_used": active_model,
        "thinking_level": active_thinking,
        "requirements_analyzed": len(requirements),
        "documents_indexed": len(documents),
        "findings_generated": finding_idx - 1,
        "diagnostics": {
            "stage_timings": stage_timings,
            "documents": ingestion_diagnostics,
            "retrieval": retrieval_diagnostics,
            "visual_analysis": visual_diagnostics,
            "requirement_visual_recovery": requirement_visual_diagnostics,
            "reasoning": reasoning_diagnostics,
        },
    }
