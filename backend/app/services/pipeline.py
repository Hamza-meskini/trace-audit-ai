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
from app.services.contract_transport import extraction_contract_payload
from app.services.retrieval import (
    build_requirement_search_queries,
    precompute_chunk_embeddings,
    retrieve_candidate_evidence_hybrid,
)
from app.services.databricks_ai_search import retrieve_with_fallback, sync_project_chunks
from app.services.databricks_custom_reranker import rerank_candidates
from app.services.classification import (
    EvidenceLinkAssessment,
    RequirementAssessment,
    batch_assess_requirements,
)
from app.schemas.verification_result import ConditionVerificationResult
from app.services.pipeline_cache import stable_key
from app.services.taxonomy import finding_type_for, normalize_severity, finding_severity_for
from app.config import settings
from app.services.document_classifier import (
    ROLE_DISPLAY_NAMES,
    discover_specification_documents,
    profile_documents,
)
from app.services.visual_analysis import describe_retrieved_figures
from app.services.requirement_visual_recovery import recover_requirement_text_from_pages
from app.services.observability import (
    new_correlation_id,
    observation_context,
    set_span_outputs,
    trace_span,
)

logger = logging.getLogger("traceaudit.pipeline")


def _verification_fingerprint(
    item: dict,
    *,
    corpus_fingerprint: str,
    model: str,
    thinking_level: str,
) -> str:
    return stable_key({
        "version": settings.AUDIT_CACHE_VERSION,
        "model": model,
        "thinking_level": thinking_level,
        "corpus": corpus_fingerprint,
        "requirement": {
            key: item.get(key)
            for key in (
                "req_code", "title", "description", "category", "conditions",
                "semantic_clauses", "clause_coverage", "unmapped_obligations",
                "contract_complete", "logic", "logic_tree", "decomposition_confidence",
                "ambiguities", "validation_issues", "targeted_extraction",
            )
        },
        "candidates": [
            {
                "id": candidate.get("id"),
                "content": candidate.get("content"),
                "metadata": candidate.get("metadata"),
            }
            for candidate in item.get("candidate_chunks", [])
        ],
    })


def _cached_assessment(requirement: Requirement, fingerprint: str) -> RequirementAssessment | None:
    verification = dict((requirement.extracted_parameters or {}).get("verification") or {})
    if verification.get("input_fingerprint") != fingerprint:
        return None
    cached = verification.get("result_cache")
    if not isinstance(cached, dict):
        return None
    try:
        diagnostics = dict(cached.get("pipeline_diagnostics") or {})
        diagnostics["verification_cache_hit"] = True
        return RequirementAssessment(
            coverage_status=str(cached["coverage_status"]),
            confidence=float(cached["confidence"]),
            review_state=str(cached["review_state"]),
            ai_analysis=str(cached.get("ai_analysis") or ""),
            ai_recommendation=str(cached.get("ai_recommendation") or ""),
            evidence_links=[EvidenceLinkAssessment(**item) for item in cached.get("evidence_links", [])],
            condition_results=[
                ConditionVerificationResult.model_validate(item)
                for item in cached.get("condition_results", [])
            ],
            pipeline_diagnostics=diagnostics,
        )
    except (KeyError, TypeError, ValueError):
        return None


def _assessment_cache_payload(assessment: RequirementAssessment) -> dict:
    return {
        "coverage_status": assessment.coverage_status,
        "confidence": assessment.confidence,
        "review_state": assessment.review_state,
        "ai_analysis": assessment.ai_analysis,
        "ai_recommendation": assessment.ai_recommendation,
        "evidence_links": [
            {
                "chunk_id": item.chunk_id,
                "document_name": item.document_name,
                "page_number": item.page_number,
                "quote": item.quote,
                "status": item.status,
                "label": item.label,
                "highlight": item.highlight,
            }
            for item in assessment.evidence_links
        ],
        "condition_results": [item.model_dump() for item in assessment.condition_results],
        "pipeline_diagnostics": assessment.pipeline_diagnostics,
    }


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
        expects_prep = bool(settings.DATABRICKS_AI_PREP_SEARCH_ENABLED)
        has_prep = cached_backend.startswith("databricks-ai-prep-search")
        return has_prep if expects_prep else cached_backend.startswith("databricks-ai-parse")
    if requested_parser in {"pymupdf", "docling"}:
        return requested_parser in cached_backend
    return True


async def _run_audit_pipeline_impl(
    project_id: str,
    db: AsyncSession,
    model: Optional[str] = None,
    thinking_level: Optional[str] = None,
    progress=None,
    run_id: Optional[str] = None,
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

    # 3. Extract requirements for new or changed specification documents.
    req_result = await db.execute(
        select(Requirement).where(Requirement.project_id == project_id)
    )
    requirements = req_result.scalars().all()
    requirement_visual_diagnostics: list[dict] = []
    specs_to_extract: list[Document] = []
    for doc in spec_docs:
        current_sha = file_sha256(doc.storage_path) if os.path.exists(doc.storage_path) else None
        associated = [
            req for req in requirements
            if (req.extracted_parameters or {}).get("source_document_id") == doc.id
            or req.source_document == doc.original_filename
        ]
        if not associated:
            specs_to_extract.append(doc)
            continue
        recorded = {
            (req.extracted_parameters or {}).get("source_sha256")
            for req in associated
            if (req.extracted_parameters or {}).get("source_sha256")
        }
        if recorded and current_sha not in recorded:
            specs_to_extract.append(doc)
        elif current_sha and not recorded:
            # Establish a migration baseline for requirements created before
            # source versioning was introduced.
            for req in associated:
                data = dict(req.extracted_parameters or {})
                data["source_document_id"] = doc.id
                data["source_sha256"] = current_sha
                req.extracted_parameters = data

    if specs_to_extract:
        report_progress(
            "extraction", 0, len(specs_to_extract),
            "Extracting requirements from new or changed specification documents",
        )
        extraction_started = time.perf_counter()
        extracted_count = 0
        seen_req_codes = set()
        existing_by_code = {req.req_code: req for req in requirements}

        for doc in specs_to_extract:
            if os.path.exists(doc.storage_path):
                current_sha = file_sha256(doc.storage_path)
                previous_for_doc = [
                    req for req in requirements
                    if (req.extracted_parameters or {}).get("source_document_id") == doc.id
                    or req.source_document == doc.original_filename
                ]
                extracted_codes_for_doc: set[str] = set()
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
                    extracted_codes_for_doc.add(er.req_code)
                    if er.req_code in seen_req_codes:
                        continue
                    seen_req_codes.add(er.req_code)
                    extracted_count += 1
                    req = existing_by_code.get(er.req_code)
                    preserved = dict(req.extracted_parameters or {}) if req else {}
                    refreshed = {
                        "source_document_id": doc.id,
                        "source_sha256": current_sha,
                        "parameters": [p.model_dump() for p in er.parameters],
                        **extraction_contract_payload(er),
                    }
                    if preserved.get("review_history"):
                        refreshed["review_history"] = preserved["review_history"]
                    if req is None:
                        req = Requirement(
                            id=str(uuid.uuid4()),
                            project_id=project_id,
                            req_code=er.req_code,
                            title=er.title,
                            description=er.description,
                            category=er.category,
                            severity=normalize_severity(er.severity),
                            source_document=doc.original_filename,
                            extracted_parameters=refreshed,
                        )
                        db.add(req)
                        existing_by_code[er.req_code] = req
                    else:
                        req.title = er.title
                        req.description = er.description
                        req.category = er.category
                        req.severity = normalize_severity(er.severity)
                        req.source_document = doc.original_filename
                        req.extracted_parameters = refreshed
                        req.coverage_status = "Unknown"
                        req.confidence = 0.0
                        req.review_state = "Needs review"
                        req.ai_analysis = "The source requirement changed and was re-extracted for this audit."
                        req.ai_recommendation = "Review the refreshed contract and the new evidence assessment."

                # Keep a removed source clause visible for audit history, but
                # prevent its stale contract from being silently approved or
                # forcing re-extraction on every later run.
                for removed in previous_for_doc:
                    if removed.req_code in extracted_codes_for_doc:
                        continue
                    data = dict(removed.extracted_parameters or {})
                    data.pop("verification", None)
                    data["source_sha256"] = current_sha
                    data["source_sync"] = {
                        "status": "removed_from_source",
                        "detected_at": datetime.now(timezone.utc).isoformat(),
                    }
                    issues = list(data.get("validation_issues") or [])
                    marker = "Requirement identifier was not found in the latest source document."
                    if marker not in issues:
                        issues.append(marker)
                    data["validation_issues"] = issues
                    data["contract_complete"] = False
                    removed.extracted_parameters = data
                    removed.coverage_status = "Unknown"
                    removed.confidence = 0.0
                    removed.review_state = "Needs review"
                    removed.ai_analysis = marker
                    removed.ai_recommendation = "Confirm whether this requirement was retired or moved."

        stage_timings["requirement_extraction_seconds"] = round(
            time.perf_counter() - extraction_started, 3
        )

        await db.flush()
        req_result = await db.execute(
            select(Requirement).where(Requirement.project_id == project_id)
        )
        requirements = req_result.scalars().all()

    requirements = [
        req for req in requirements
        if (req.extracted_parameters or {}).get("source_sync", {}).get("status")
        != "removed_from_source"
    ]

    # 4. Assess each requirement against all evidence chunks
    # 4a. Prepare the selected search backend once. Managed AI Search writes the
    # current project chunks to a Delta Sync source table; local mode reuses one
    # pre-computed embedding batch across every requirement.
    report_progress("retrieval", 0, len(requirements), "Preparing evidence search")
    managed_search_ready = False
    try:
        search_sync = await sync_project_chunks(project_id, all_chunks_for_retrieval)
        managed_search_ready = search_sync.backend_used == "databricks-ai-search"
    except Exception as exc:
        logger.warning("Databricks AI Search sync failed; preparing local retrieval: %s", exc)
        search_sync = None
    chunk_embeddings = (
        [None] * len(all_chunks_for_retrieval)
        if managed_search_ready
        else await precompute_chunk_embeddings(all_chunks_for_retrieval)
    )
    logger.info(
        "Prepared %s retrieval for %d chunks",
        "Databricks AI Search" if managed_search_ready else "local hybrid",
        len(all_chunks_for_retrieval),
    )

    # 4b. Retrieve candidate evidence per requirement (hybrid BM25 + semantic)
    retrieval_started = time.perf_counter()
    req_items: list[dict | None] = [None] * len(requirements)
    per_requirement_search: dict[str, dict] = {}
    retrieval_semaphore = asyncio.Semaphore(max(1, int(settings.AUDIT_RETRIEVAL_CONCURRENCY)))

    async def retrieve_requirement(index: int, req: Requirement):
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
        async with retrieval_semaphore:
            if managed_search_ready:
                retrieved, search_diagnostics = await retrieve_with_fallback(
                    primary_query,
                    all_chunks_for_retrieval,
                    project_id=project_id,
                    chunk_embeddings=chunk_embeddings,
                    top_k=8,
                    exclude_doc_names=spec_doc_names,
                    condition_queries=search_queries[1:],
                    backend="databricks",
                )
                diagnostics = search_diagnostics.as_dict()
            else:
                retrieval_top_k = (
                    max(8, int(settings.DATABRICKS_CUSTOM_RERANKER_CANDIDATE_COUNT))
                    if settings.DATABRICKS_CUSTOM_RERANKER_ENABLED
                    else 8
                )
                retrieved = await retrieve_candidate_evidence_hybrid(
                    primary_query,
                    all_chunks_for_retrieval,
                    chunk_embeddings=chunk_embeddings,
                    top_k=retrieval_top_k,
                    exclude_doc_names=spec_doc_names,
                    condition_queries=search_queries[1:],
                )
                retrieved, custom_reranker = await rerank_candidates(
                    primary_query,
                    retrieved,
                    condition_queries=search_queries[1:],
                    top_k=8,
                )
                diagnostics = {
                    "requested_backend": "local",
                    "backend_used": "local-hybrid",
                    "queries": len(search_queries),
                    "candidates": len(retrieved),
                    "custom_reranker": custom_reranker,
                }

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

        item = {
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
        }
        return index, req.req_code, item, diagnostics

    retrieval_tasks = [
        asyncio.create_task(retrieve_requirement(index, req))
        for index, req in enumerate(requirements)
    ]
    retrieved_count = 0
    try:
        for task in asyncio.as_completed(retrieval_tasks):
            index, req_code, item, diagnostics = await task
            req_items[index] = item
            per_requirement_search[req_code] = diagnostics
            retrieved_count += 1
            report_progress(
                "retrieval", retrieved_count, len(requirements), f"Located evidence for {req_code}"
            )
    finally:
        unfinished = [task for task in retrieval_tasks if not task.done()]
        for task in unfinished:
            task.cancel()
        if unfinished:
            await asyncio.gather(*unfinished, return_exceptions=True)

    req_items = [item for item in req_items if item is not None]

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
        "backend": "databricks-ai-search" if managed_search_ready else "local-hybrid",
        "sync": search_sync.as_dict() if search_sync is not None else {
            "backend_used": "local-hybrid",
            "fallback_reason": "Databricks AI Search sync was unavailable",
        },
        "per_requirement": per_requirement_search,
    }

    # Use ai_extract as a citation-backed discovery stage over the complete
    # independent-evidence corpus. This can recover pages that similarity
    # top-k missed. The helper prepends cited raw chunks, preserves the normal
    # retrieval results, and performs one focused retry for uncovered atoms.
    targeted_started = time.perf_counter()
    from app.services.databricks_document_ai import enrich_targeted_evidence_items

    excluded_spec_names = {name.lower() for name in spec_doc_names}
    evidence_corpus = [
        chunk for chunk in all_chunks_for_retrieval
        if chunk.get("document_name", "").lower() not in excluded_spec_names
    ]
    pre_discovery_candidate_total = retrieval_diagnostics["candidate_chunks_total"]
    targeted_stats = await enrich_targeted_evidence_items(
        req_items,
        evidence_corpus=evidence_corpus,
    )
    stage_timings["targeted_extraction_seconds"] = round(
        time.perf_counter() - targeted_started, 3
    )
    retrieval_diagnostics["targeted_extraction"] = targeted_stats
    retrieval_diagnostics["candidate_chunks_before_discovery"] = pre_discovery_candidate_total
    retrieval_diagnostics["candidate_chunks_total"] = sum(
        len(item.get("candidate_chunks", [])) for item in req_items
    )
    retrieval_diagnostics["requirements_without_candidates"] = sum(
        not item.get("candidate_chunks") for item in req_items
    )
    retrieval_diagnostics["ai_extract_discovered_chunks"] = sum(
        len(((item.get("targeted_extraction") or {}).get("metadata") or {}).get("cited_source_chunk_ids", []))
        for item in req_items
    )
    retrieval_diagnostics["structured_table_candidates"] = sum(
        (candidate.get("metadata") or {}).get("block_type") == "table"
        for item in req_items for candidate in item.get("candidate_chunks", [])
    )
    retrieval_diagnostics["figure_candidates"] = sum(
        (candidate.get("metadata") or {}).get("block_type") == "figure"
        for item in req_items for candidate in item.get("candidate_chunks", [])
    )

    # Render and describe only figures selected by retrieval. Descriptions are
    # persisted on the chunk and reused across requirements and later runs.
    # Citation discovery runs first so figures found outside the original top-k
    # are also eligible for visual interpretation.
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

    # 4b. Batched hybrid verification: deterministic validators first,
    # LLM multi-condition reasoning (with Databricks cascade fallback) for
    # inconclusive requirements — same engine used by offline evaluation.
    reasoning_started = time.perf_counter()
    report_progress("reasoning", 0, len(requirements), "Evaluating conditions, citations and review safety")
    corpus_fingerprint = stable_key({
        "version": settings.AUDIT_CACHE_VERSION,
        "chunks": [
            {
                "id": chunk.get("id"),
                "document_id": chunk.get("document_id"),
                "content": chunk.get("content"),
                "metadata": chunk.get("metadata"),
            }
            for chunk in all_chunks_for_retrieval
        ],
    })
    req_by_code = {req.req_code: req for req in requirements}
    item_by_code = {item["req_code"]: item for item in req_items}
    fingerprints: dict[str, str] = {}
    assessments: dict[str, RequirementAssessment] = {}
    pending_items: list[dict] = []
    for item in req_items:
        code = item["req_code"]
        fingerprint = _verification_fingerprint(
            item,
            corpus_fingerprint=corpus_fingerprint,
            model=active_model,
            thinking_level=active_thinking,
        )
        fingerprints[code] = fingerprint
        cached = _cached_assessment(req_by_code[code], fingerprint)
        if cached is None:
            pending_items.append(item)
        else:
            assessments[code] = cached

    cached_count = len(assessments)
    if cached_count:
        reused_at = datetime.now(timezone.utc).isoformat()
        for code in assessments:
            req = req_by_code[code]
            data = dict(req.extracted_parameters or {})
            verification = dict(data.get("verification") or {})
            diagnostics = dict(verification.get("diagnostics") or {})
            diagnostics["verification_cache_hit"] = True
            verification.update({
                "audit_run_id": run_id,
                "assessed_at": reused_at,
                "diagnostics": diagnostics,
            })
            data["verification"] = verification
            req.extracted_parameters = data
        await db.commit()
        report_progress(
            "reasoning",
            cached_count,
            len(requirements),
            f"Reused {cached_count} unchanged requirement results",
        )

    persisted_codes: set[str] = set()

    async def persist_completed(completed: dict[str, RequirementAssessment]):
        for code, assessment in completed.items():
            req = req_by_code[code]
            item = item_by_code[code]
            await db.execute(
                delete(RequirementEvidence).where(RequirementEvidence.requirement_id == req.id)
            )
            await db.execute(delete(Finding).where(Finding.requirement_id == req.id))

            req.coverage_status = assessment.coverage_status
            req.confidence = assessment.confidence
            req.review_state = assessment.review_state
            req.ai_analysis = assessment.ai_analysis
            req.ai_recommendation = assessment.ai_recommendation
            req.sources_count = len(assessment.evidence_links)
            data = dict(req.extracted_parameters or {})
            candidates = item.get("candidate_chunks", [])
            diagnostics = dict(assessment.pipeline_diagnostics or {})
            diagnostics["verification_cache_hit"] = False
            assessment.pipeline_diagnostics = diagnostics
            data["verification"] = {
                "assessed_at": datetime.now(timezone.utc).isoformat(),
                "audit_run_id": run_id,
                "input_fingerprint": fingerprints[code],
                "condition_results": [result.model_dump() for result in assessment.condition_results],
                "diagnostics": diagnostics,
                "model": active_model,
                "thinking_level": active_thinking,
                "targeted_extraction": item.get("targeted_extraction"),
                "evidence_catalog": [
                    {
                        "evidence_id": f"E{index}",
                        "chunk_id": candidate["id"],
                        "document_id": candidate["document_id"],
                        "document_name": candidate["document_name"],
                        "page_number": candidate.get("page_number"),
                    }
                    for index, candidate in enumerate(candidates, 1)
                ],
                "result_cache": _assessment_cache_payload(assessment),
            }
            req.extracted_parameters = data

            for ev_link in assessment.evidence_links:
                db.add(RequirementEvidence(
                    id=str(uuid.uuid4()),
                    requirement_id=req.id,
                    evidence_chunk_id=ev_link.chunk_id,
                    status=ev_link.status,
                    label=ev_link.label,
                    highlight=ev_link.highlight,
                ))

            if assessment.coverage_status in ("Partial", "Missing", "Conflict", "Unknown"):
                ordinal = next(
                    index for index, candidate in enumerate(requirements, 1)
                    if candidate.req_code == code
                )
                finding_sev = finding_severity_for(req.severity, assessment.coverage_status)
                db.add(Finding(
                    id=str(uuid.uuid4()),
                    project_id=project_id,
                    requirement_id=req.id,
                    finding_code=f"F-{ordinal:03d}",
                    finding_type=finding_type_for(assessment.coverage_status),
                    severity=finding_sev,
                    review_state=assessment.review_state,
                    category=req.category,
                    sources_count=len(assessment.evidence_links),
                    description=assessment.ai_analysis,
                ))
        # Each completed batch is a restart-safe checkpoint and becomes visible
        # to reviewers immediately.
        await db.commit()
        persisted_codes.update(completed)

    if pending_items:
        fresh_assessments = await batch_assess_requirements(
            req_items=pending_items,
            model=active_model,
            thinking_level=active_thinking,
            spec_doc_names=spec_doc_names,
            progress=lambda done, total: report_progress(
                "reasoning",
                cached_count + done,
                len(requirements),
                f"{cached_count + done} of {len(requirements)} requirements evaluated",
            ),
            on_results=persist_completed,
        )
        assessments.update(fresh_assessments)
        unpersisted = {
            code: assessment for code, assessment in fresh_assessments.items()
            if code not in persisted_codes
        }
        if unpersisted:
            await persist_completed(unpersisted)
    stage_timings["reasoning_seconds"] = round(time.perf_counter() - reasoning_started, 3)
    reasoning_diagnostics = {
        "assessments_produced": len(assessments),
        "assessments_missing": max(0, len(requirements) - len(assessments)),
        "verification_cache_hits": cached_count,
        "verification_cache_misses": len(pending_items),
        "coverage_statuses": {
            status: sum(
                assessment.coverage_status == status for assessment in assessments.values()
            )
            for status in ("Supported", "Partial", "Missing", "Conflict", "Unknown", "Not applicable")
        },
        "review_required": sum(
            str(assessment.review_state).lower() in {"needs review", "open"}
            for assessment in assessments.values()
        ),
    }

    # 5. Update document linked counts
    report_progress("saving", len(assessments), len(requirements), "Finalizing document links")
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
        "findings_generated": sum(
            assessment.coverage_status in ("Partial", "Missing", "Conflict", "Unknown")
            for assessment in assessments.values()
        ),
        "diagnostics": {
            "stage_timings": stage_timings,
            "documents": ingestion_diagnostics,
            "retrieval": retrieval_diagnostics,
            "visual_analysis": visual_diagnostics,
            "requirement_visual_recovery": requirement_visual_diagnostics,
            "reasoning": reasoning_diagnostics,
        },
    }


async def run_audit_pipeline(
    project_id: str,
    db: AsyncSession,
    model: Optional[str] = None,
    thinking_level: Optional[str] = None,
    progress=None,
    run_id: Optional[str] = None,
) -> dict:
    """Execute one correlated, end-to-end observable audit run."""
    correlation_id = run_id or new_correlation_id("audit")
    active_model = model or settings.LLM_MODEL
    with observation_context(
        audit_run_id=correlation_id,
        project_id=project_id,
        stage="audit_pipeline",
    ):
        with trace_span(
            "traceaudit.audit_pipeline",
            span_type="CHAIN",
            inputs={
                "audit_run_id": correlation_id,
                "project_id": project_id,
                "model": active_model,
                "thinking_level": thinking_level or settings.GEMINI_THINKING_LEVEL,
            },
        ) as span:
            result = await _run_audit_pipeline_impl(
                project_id=project_id,
                db=db,
                model=model,
                thinking_level=thinking_level,
                progress=progress,
                run_id=correlation_id,
            )
            result["audit_run_id"] = correlation_id
            set_span_outputs(span, {
                "status": result.get("status"),
                "requirements_analyzed": result.get("requirements_analyzed"),
                "documents_indexed": result.get("documents_indexed"),
                "findings_generated": result.get("findings_generated"),
                "stage_timings": result.get("diagnostics", {}).get("stage_timings"),
            })
            return result
