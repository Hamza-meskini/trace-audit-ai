"""End-to-end audit pipeline service.

Executes document parsing, requirement extraction, evidence retrieval,
hybrid verification, and findings generation for a project. After the
shared document preparation, each requirement runs retrieve, figure
description, verdict, and save. Model calls overlap. Saves are committed
one requirement at a time on the audit session.
"""

import os
import uuid
import logging
import time
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
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
    prepare_lexical_corpus,
)
from app.services.databricks_ai_search import retrieve_with_fallback, sync_project_chunks
from app.services.classification import (
    EvidenceLinkAssessment,
    RequirementAssessment,
    assess_requirement_coverage_async,
    refine_deferred_assessment,
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


class _BusySeconds:
    """Wall-clock time during which at least one requirement is in a stage."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._active = 0
        self._started = 0.0
        self.seconds = 0.0

    async def __aenter__(self) -> "_BusySeconds":
        async with self._lock:
            if self._active == 0:
                self._started = time.perf_counter()
            self._active += 1
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        async with self._lock:
            self._active -= 1
            if self._active == 0:
                self.seconds += time.perf_counter() - self._started
        return False


def _merge_count_dicts(parts: list[dict]) -> dict:
    merged: dict = {}
    for part in parts:
        for key, value in (part or {}).items():
            if isinstance(value, bool):
                continue
            if isinstance(value, dict):
                bucket = merged.setdefault(key, {})
                if not isinstance(bucket, dict):
                    continue
                for name, count in value.items():
                    if isinstance(count, (int, float)) and not isinstance(count, bool):
                        bucket[name] = int(bucket.get(name, 0)) + int(count)
            elif isinstance(value, (int, float)):
                merged[key] = merged.get(key, 0) + value
    return merged


async def _persist_cache_reuse(session, requirement_id: str, run_id: Optional[str]) -> None:
    result = await session.execute(select(Requirement).where(Requirement.id == requirement_id))
    req = result.scalar_one()
    data = dict(req.extracted_parameters or {})
    verification = dict(data.get("verification") or {})
    diagnostics = dict(verification.get("diagnostics") or {})
    diagnostics["verification_cache_hit"] = True
    verification.update({
        "audit_run_id": run_id,
        "assessed_at": datetime.now(timezone.utc).isoformat(),
        "diagnostics": diagnostics,
    })
    data["verification"] = verification
    req.extracted_parameters = data


async def _persist_requirement_assessment(
    session,
    *,
    requirement_id: str,
    project_id: str,
    assessment: RequirementAssessment,
    item: dict,
    fingerprint: str,
    ordinal: int,
    run_id: Optional[str],
    model: str,
    thinking_level: str,
) -> None:
    result = await session.execute(select(Requirement).where(Requirement.id == requirement_id))
    req = result.scalar_one()
    await session.execute(
        delete(RequirementEvidence).where(RequirementEvidence.requirement_id == req.id)
    )
    await session.execute(delete(Finding).where(Finding.requirement_id == req.id))

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
        "input_fingerprint": fingerprint,
        "condition_results": [result_item.model_dump() for result_item in assessment.condition_results],
        "diagnostics": diagnostics,
        "model": model,
        "thinking_level": thinking_level,
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
        session.add(RequirementEvidence(
            id=str(uuid.uuid4()),
            requirement_id=req.id,
            evidence_chunk_id=ev_link.chunk_id,
            status=ev_link.status,
            label=ev_link.label,
            highlight=ev_link.highlight,
        ))

    if assessment.coverage_status in ("Partial", "Missing", "Conflict", "Unknown"):
        session.add(Finding(
            id=str(uuid.uuid4()),
            project_id=project_id,
            requirement_id=req.id,
            finding_code=f"F-{ordinal:03d}",
            finding_type=finding_type_for(assessment.coverage_status),
            severity=finding_severity_for(req.severity, assessment.coverage_status),
            review_state=assessment.review_state,
            category=req.category,
            sources_count=len(assessment.evidence_links),
            description=assessment.ai_analysis,
        ))


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
    excluded_spec_names = {name.lower() for name in spec_doc_names}
    evidence_corpus = [
        chunk for chunk in all_chunks_for_retrieval
        if chunk.get("document_name", "").lower() not in excluded_spec_names
    ]
    chunk_embeddings = (
        [None] * len(evidence_corpus)
        if managed_search_ready
        else await precompute_chunk_embeddings(evidence_corpus)
    )
    lexical_corpus = prepare_lexical_corpus(evidence_corpus)
    logger.info(
        "Prepared %s retrieval for %d chunks",
        "Databricks AI Search" if managed_search_ready else "local hybrid",
        len(evidence_corpus),
    )

    # Each requirement retrieves, describes its figures, and is saved as soon
    # as its own evidence is ready. Four workers match the eight-slot model cap,
    # because a requirement in flight can hold a verdict call and a citation call.
    # Database writes take the caller's session one requirement at a time so
    # concurrent model calls never share that session.
    from app.services.databricks_document_ai import enrich_targeted_evidence_items

    snapshots = [
        {
            "id": req.id,
            "req_code": req.req_code,
            "title": req.title,
            "description": req.description,
            "category": req.category,
            "ordinal": ordinal,
            "extracted_parameters": dict(req.extracted_parameters or {}),
        }
        for ordinal, req in enumerate(requirements, 1)
    ]
    await db.commit()

    documents_by_id = {document.id: document for document in documents}
    # Figure descriptions are cached on plain copies. The live ORM rows are
    # updated once, after every worker has finished, so a commit cannot race
    # a vision write on the same session.
    visual_chunk_copies = {
        chunk_id: SimpleNamespace(
            metadata_json=dict(chunk.metadata_json or {}),
            content=chunk.content,
        )
        for chunk_id, chunk in evidence_chunk_models.items()
    }
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
    worker_slots = asyncio.Semaphore(max(1, int(settings.AUDIT_REQUIREMENT_CONCURRENCY)))
    persist_lock = asyncio.Lock()
    progress_lock = asyncio.Lock()
    retrieval_busy = _BusySeconds()
    targeted_busy = _BusySeconds()
    vision_busy = _BusySeconds()
    reasoning_busy = _BusySeconds()
    retrieved_count = 0
    vision_count = 0
    reasoned_count = 0
    requirement_work_started = time.perf_counter()
    report_progress("reasoning", 0, len(requirements), "Evaluating conditions, citations and review safety")

    async def save_requirement(snapshot: dict, item: dict, assessment: RequirementAssessment, *, cache_hit: bool) -> None:
        async with persist_lock:
            if cache_hit:
                await _persist_cache_reuse(db, snapshot["id"], run_id)
            else:
                await _persist_requirement_assessment(
                    db,
                    requirement_id=snapshot["id"],
                    project_id=project_id,
                    assessment=assessment,
                    item=item,
                    fingerprint=item["fingerprint"],
                    ordinal=snapshot["ordinal"],
                    run_id=run_id,
                    model=active_model,
                    thinking_level=active_thinking,
                )
            await db.commit()

    async def run_requirement(snapshot: dict) -> dict:
        nonlocal retrieved_count, vision_count, reasoned_count
        code = snapshot["req_code"]
        try:
            async with worker_slots:
                extraction_data = snapshot["extracted_parameters"]
                structured_conditions = extraction_data.get("conditions", [])
                search_queries = build_requirement_search_queries(
                    code,
                    snapshot["title"],
                    snapshot["description"] or "",
                    structured_conditions,
                    extraction_data.get("semantic_clauses", []),
                )
                primary_query = (
                    search_queries[0]
                    if search_queries
                    else f"{code} {snapshot['title']} {snapshot['description'] or ''}"
                )
                async with retrieval_busy:
                    retrieved, search_diagnostics = await retrieve_with_fallback(
                        primary_query,
                        evidence_corpus,
                        project_id=project_id,
                        chunk_embeddings=chunk_embeddings,
                        top_k=8,
                        exclude_doc_names=spec_doc_names,
                        condition_queries=search_queries[1:],
                        backend="databricks" if managed_search_ready else "local",
                        prepared_corpus=lexical_corpus,
                    )
                candidates_before_discovery = len(retrieved)
                candidate_chunks = [
                    {
                        "id": match.chunk_id,
                        "document_id": match.document_id,
                        "document_name": match.document_name,
                        "doc_type": match.doc_type,
                        "page_number": match.page_number,
                        "content": match.content,
                        "document_profile": match.document_profile,
                        "metadata": match.metadata,
                    }
                    for match in retrieved
                ]
                item = {
                    "req_code": code,
                    "title": snapshot["title"],
                    "description": snapshot["description"],
                    "category": snapshot["category"],
                    "conditions": structured_conditions,
                    "semantic_clauses": extraction_data.get("semantic_clauses", []),
                    "clause_coverage": extraction_data.get("clause_coverage", []),
                    "unmapped_obligations": extraction_data.get("unmapped_obligations", []),
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
                async with progress_lock:
                    retrieved_count += 1
                    report_progress(
                        "retrieval",
                        retrieved_count,
                        len(requirements),
                        f"Located evidence for {code}",
                    )
                async with targeted_busy:
                    targeted = await enrich_targeted_evidence_items(
                        [item],
                        evidence_corpus=evidence_corpus,
                    )
                async with vision_busy:
                    visual = await describe_retrieved_figures(
                        [item],
                        documents_by_id,
                        visual_chunk_copies,
                        model=active_model,
                        max_concurrency=max(1, int(settings.AUDIT_VISION_CONCURRENCY)),
                    )
                async with progress_lock:
                    vision_count += 1
                    report_progress(
                        "vision",
                        vision_count,
                        len(requirements),
                        f"Interpreted figures for {code}",
                    )
                fingerprint = _verification_fingerprint(
                    item,
                    corpus_fingerprint=corpus_fingerprint,
                    model=active_model,
                    thinking_level=active_thinking,
                )
                item["fingerprint"] = fingerprint
                cached = _cached_assessment(
                    SimpleNamespace(extracted_parameters=snapshot["extracted_parameters"]),
                    fingerprint,
                )
                async with reasoning_busy:
                    if cached is None:
                        assessment = await assess_requirement_coverage_async(
                            req_code=code,
                            title=snapshot["title"] or "",
                            description=snapshot["description"],
                            category=snapshot["category"] or "General",
                            candidate_chunks=item.get("candidate_chunks") or [],
                            model=active_model,
                            thinking_level=active_thinking,
                            spec_doc_names=spec_doc_names,
                            conditions=item.get("conditions"),
                            semantic_clauses=item.get("semantic_clauses"),
                            clause_coverage=item.get("clause_coverage"),
                            unmapped_obligations=item.get("unmapped_obligations"),
                            contract_complete=item.get("contract_complete"),
                            logic=item.get("logic"),
                            logic_tree=item.get("logic_tree"),
                            decomposition_confidence=item.get("decomposition_confidence"),
                            ambiguities=item.get("ambiguities"),
                            validation_issues=item.get("validation_issues"),
                            targeted_extraction=item.get("targeted_extraction"),
                            defer_partial=True,
                            defer_secondary_adjudication=True,
                        )
                        cache_hit = False
                    else:
                        assessment = cached
                        cache_hit = True
                    await save_requirement(snapshot, item, assessment, cache_hit=cache_hit)
                async with progress_lock:
                    reasoned_count += 1
                    report_progress(
                        "reasoning",
                        reasoned_count,
                        len(requirements),
                        f"{reasoned_count} of {len(requirements)} requirements evaluated",
                    )
            if (
                not cache_hit
                and assessment.review_state == "Needs review"
                and (assessment.pipeline_diagnostics or {}).get("secondary_adjudication_deferred")
            ):
                async with reasoning_busy:
                    refined = await refine_deferred_assessment(
                        assessment,
                        item.get("candidate_chunks") or [],
                        model=active_model,
                        spec_doc_names=spec_doc_names,
                    )
                if refined is not None:
                    assessment = refined
                    await save_requirement(snapshot, item, assessment, cache_hit=False)
            return {
                "code": code,
                "item": item,
                "search": search_diagnostics.as_dict(),
                "assessment": assessment,
                "cache_hit": cache_hit,
                "visual": visual,
                "targeted": targeted,
                "candidates_before_discovery": candidates_before_discovery,
            }
        except Exception:
            logger.exception("Requirement %s failed during assessment", code)
            return {
                "code": code,
                "item": None,
                "search": {},
                "assessment": None,
                "cache_hit": False,
                "visual": {},
                "targeted": {},
                "candidates_before_discovery": 0,
            }

    worker_tasks = [asyncio.create_task(run_requirement(snapshot)) for snapshot in snapshots]
    try:
        worker_rows = await asyncio.gather(*worker_tasks)
    except BaseException:
        for task in worker_tasks:
            task.cancel()
        await asyncio.gather(*worker_tasks, return_exceptions=True)
        raise

    for chunk_id, copy in visual_chunk_copies.items():
        model_chunk = evidence_chunk_models.get(chunk_id)
        if model_chunk is None:
            continue
        model_chunk.metadata_json = copy.metadata_json
        model_chunk.content = copy.content

    req_items = [row["item"] for row in worker_rows if row.get("item")]
    assessments = {
        row["code"]: row["assessment"]
        for row in worker_rows
        if row.get("assessment") is not None
    }
    cached_count = sum(1 for row in worker_rows if row.get("cache_hit"))
    stage_timings["retrieval_seconds"] = round(retrieval_busy.seconds, 3)
    stage_timings["targeted_extraction_seconds"] = round(targeted_busy.seconds, 3)
    stage_timings["visual_analysis_seconds"] = round(vision_busy.seconds, 3)
    stage_timings["reasoning_seconds"] = round(reasoning_busy.seconds, 3)
    stage_timings["requirement_pipeline_seconds"] = round(
        time.perf_counter() - requirement_work_started, 3
    )
    per_requirement_search = {
        row["code"]: row["search"] for row in worker_rows if row.get("search")
    }
    targeted_stats = _merge_count_dicts([row["targeted"] for row in worker_rows])
    visual_diagnostics = _merge_count_dicts([row["visual"] for row in worker_rows])
    pre_discovery_candidate_total = sum(
        int(row.get("candidates_before_discovery") or 0) for row in worker_rows
    )
    retrieval_diagnostics = {
        "requirements": len(req_items),
        "requirements_without_candidates": sum(
            not item.get("candidate_chunks") for item in req_items
        ),
        "candidate_chunks_before_discovery": pre_discovery_candidate_total,
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
        "targeted_extraction": targeted_stats,
        "ai_extract_discovered_chunks": sum(
            len(((item.get("targeted_extraction") or {}).get("metadata") or {}).get("cited_source_chunk_ids", []))
            for item in req_items
        ),
        "requirement_workers": max(1, int(settings.AUDIT_REQUIREMENT_CONCURRENCY)),
    }
    reasoning_diagnostics = {
        "assessments_produced": len(assessments),
        "assessments_missing": max(0, len(requirements) - len(assessments)),
        "verification_cache_hits": cached_count,
        "verification_cache_misses": sum(
            row.get("item") is not None and not row.get("cache_hit") for row in worker_rows
        ),
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
