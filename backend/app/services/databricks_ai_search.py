"""Optional Databricks AI Search ingestion and retrieval adapter.

The adapter namespaces index primary keys by project while preserving each
local EvidenceChunk identifier in metadata. That avoids collisions between
projects without weakening citations, database links, or local fallback.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from app.config import settings
from app.services.databricks_document_ai import _sql_config, _workspace_hostname
from app.services.retrieval import (
    RetrievedChunk,
    _chunk_key,
    _condition_aware_rerank,
    _expand_structural_context,
    retrieve_candidate_evidence_hybrid,
)
from app.services.observability import trace_span
from app.services.databricks_custom_reranker import rerank_candidates


logger = logging.getLogger("traceaudit.databricks_ai_search")
UC_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*\.[A-Za-z_][A-Za-z0-9_-]*\.[A-Za-z_][A-Za-z0-9_-]*$")
_RERANKER_AVAILABLE: bool | None = None


@dataclass
class SearchRunDiagnostics:
    requested_backend: str
    backend_used: str
    index_name: str = ""
    embedding_model: str = ""
    reranker_enabled: bool = False
    queries: int = 0
    candidates: int = 0
    fallback_reason: str = ""
    sync_seconds: float = 0.0
    query_seconds: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested_backend": self.requested_backend,
            "backend_used": self.backend_used,
            "index_name": self.index_name,
            "embedding_model": self.embedding_model,
            "reranker_enabled": self.reranker_enabled,
            "queries": self.queries,
            "candidates": self.candidates,
            "fallback_reason": self.fallback_reason,
            "sync_seconds": self.sync_seconds,
            "query_seconds": self.query_seconds,
            **self.details,
        }


def _validate_uc_name(value: str, setting_name: str) -> str:
    value = str(value or "").strip()
    if not UC_NAME_RE.fullmatch(value):
        raise RuntimeError(f"{setting_name} must be a three-part Unity Catalog name")
    return value


def _search_config() -> tuple[str, str, str]:
    endpoint = str(settings.DATABRICKS_AI_SEARCH_ENDPOINT or "").strip()
    if not endpoint:
        raise RuntimeError("DATABRICKS_AI_SEARCH_ENDPOINT is required")
    index = _validate_uc_name(settings.DATABRICKS_AI_SEARCH_INDEX, "DATABRICKS_AI_SEARCH_INDEX")
    table = _validate_uc_name(
        settings.DATABRICKS_AI_SEARCH_SOURCE_TABLE,
        "DATABRICKS_AI_SEARCH_SOURCE_TABLE",
    )
    return endpoint, index, table


def _workspace_client() -> Any:
    try:
        from databricks.sdk import WorkspaceClient
    except ImportError as exc:
        raise RuntimeError("Databricks SDK is required for AI Search") from exc
    token = settings.effective_databricks_token
    if not token:
        raise RuntimeError("DATABRICKS_TOKEN is required for AI Search")
    return WorkspaceClient(host=f"https://{_workspace_hostname()}", token=token)


def _source_rows(project_id: str, chunks: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for chunk in chunks:
        chunk_id = str(chunk.get("id") or chunk.get("chunk_id") or "").strip()
        content = str(chunk.get("content") or "").strip()
        if not chunk_id or not content:
            continue
        metadata = dict(chunk.get("metadata") or {})
        metadata["traceaudit_chunk_id"] = chunk_id
        project_namespace = hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:16]
        indexed_chunk_id = f"{project_namespace}:{chunk_id}"
        rows.append((
            indexed_chunk_id,
            project_id,
            str(chunk.get("document_id") or ""),
            str(chunk.get("document_name") or ""),
            str(chunk.get("doc_type") or "Document"),
            int(chunk["page_number"]) if chunk.get("page_number") is not None else None,
            content,
            str(metadata.get("chunk_to_embed") or content),
            json.dumps(metadata, ensure_ascii=False, default=str),
            hashlib.sha256(
                (content + "\0" + str(metadata.get("chunk_to_embed") or content)).encode("utf-8")
            ).hexdigest(),
        ))
    return rows


def _write_source_table(project_id: str, chunks: list[dict[str, Any]]) -> tuple[int, int, bool]:
    """Upsert one project's current chunks into the Delta Sync source table."""
    _, _, table = _search_config()
    sql_module = __import__("databricks.sql", fromlist=["connect"])
    host, token, warehouse_id = _sql_config()
    rows = _source_rows(project_id, chunks)
    create_sql = f"""
    CREATE TABLE IF NOT EXISTS {table} (
      chunk_id STRING NOT NULL,
      project_id STRING NOT NULL,
      document_id STRING,
      document_name STRING,
      doc_type STRING,
      page_number INT,
      chunk_to_retrieve STRING,
      chunk_to_embed STRING,
      metadata_json STRING,
      content_sha256 STRING
    ) TBLPROPERTIES (delta.enableChangeDataFeed = true)
    """
    insert_sql = f"""
    INSERT INTO {table} (
      chunk_id, project_id, document_id, document_name, doc_type, page_number,
      chunk_to_retrieve, chunk_to_embed, metadata_json, content_sha256
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    with sql_module.connect(
        server_hostname=host,
        http_path=f"/sql/1.0/warehouses/{warehouse_id}",
        access_token=token,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(create_sql)
            cursor.execute(f"DESCRIBE TABLE {table}")
            columns = {str(row[0]).lower() for row in cursor.fetchall() if row and row[0]}
            if "content_sha256" not in columns:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMNS (content_sha256 STRING)")
            cursor.execute(
                f"SELECT chunk_id, content_sha256 FROM {table} WHERE project_id = ?",
                [project_id],
            )
            existing = {str(row[0]): str(row[1] or "") for row in cursor.fetchall()}
            desired = {str(row[0]): str(row[9]) for row in rows}
            changed = existing != desired
            if not changed:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                total_row = cursor.fetchone()
                total_rows = int(total_row[0]) if total_row and total_row[0] is not None else len(rows)
                return len(rows), total_rows, False
            cursor.execute(f"DELETE FROM {table} WHERE project_id = ?", [project_id])
            if rows:
                cursor.executemany(insert_sql, rows)
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            total_row = cursor.fetchone()
    total_rows = int(total_row[0]) if total_row and total_row[0] is not None else len(rows)
    return len(rows), total_rows, True


def _sync_index(project_id: str, chunks: list[dict[str, Any]]) -> SearchRunDiagnostics:
    _, index_name, _ = _search_config()
    started = time.perf_counter()
    with trace_span(
        "databricks_ai_search.sync",
        span_type="RETRIEVER",
        inputs={"project_id": project_id, "chunk_count": len(chunks)},
        attributes={"index_name": index_name},
    ):
        project_row_count, total_row_count, source_changed = _write_source_table(project_id, chunks)
        workspace = _workspace_client()
        if source_changed:
            workspace.vector_search_indexes.sync_index(index_name=index_name)

    timeout = max(0.0, float(settings.DATABRICKS_AI_SEARCH_SYNC_TIMEOUT_SECONDS))
    deadline = time.monotonic() + timeout
    last_status: dict[str, Any] = {}
    while source_changed and timeout and time.monotonic() < deadline:
        info = workspace.vector_search_indexes.get_index(index_name=index_name)
        status = getattr(info, "status", None)
        last_status = {
            "ready": getattr(status, "ready", None),
            "indexed_row_count": getattr(status, "indexed_row_count", None),
            "message": getattr(status, "message", None),
        }
        indexed_count = last_status["indexed_row_count"]
        if last_status["ready"] is True and (
            total_row_count == 0
            or (isinstance(indexed_count, int) and indexed_count >= total_row_count)
        ):
            break
        time.sleep(min(2.0, max(0.0, deadline - time.monotonic())))
    if source_changed:
        indexed_count = last_status.get("indexed_row_count")
        fully_synced = last_status.get("ready") is True and (
            total_row_count == 0
            or (isinstance(indexed_count, int) and indexed_count >= total_row_count)
        )
        if not fully_synced:
            raise RuntimeError(
                "Databricks AI Search sync did not reach the current Delta table state "
                f"within {timeout:.0f}s (indexed {indexed_count or 0}/{total_row_count} rows)"
            )
    return SearchRunDiagnostics(
        requested_backend="databricks",
        backend_used="databricks-ai-search",
        index_name=index_name,
        embedding_model=settings.DATABRICKS_AI_SEARCH_EMBEDDING_MODEL,
        reranker_enabled=bool(settings.DATABRICKS_AI_SEARCH_RERANK_ENABLED),
        sync_seconds=round(time.perf_counter() - started, 3),
        details={
            "source_rows": project_row_count,
            "total_source_rows": total_row_count,
            "source_changed": source_changed,
            "index_status": last_status,
        },
    )


async def sync_project_chunks(project_id: str, chunks: list[dict[str, Any]]) -> SearchRunDiagnostics:
    if not settings.DATABRICKS_AI_SEARCH_ENABLED:
        return SearchRunDiagnostics(requested_backend="local", backend_used="local-hybrid")
    return await asyncio.to_thread(_sync_index, project_id, chunks)


def _response_rows(response: Any) -> list[dict[str, Any]]:
    manifest = getattr(response, "manifest", None)
    columns = [str(getattr(column, "name", "") or "") for column in (getattr(manifest, "columns", None) or [])]
    data = getattr(getattr(response, "result", None), "data_array", None) or []
    return [dict(zip(columns, row)) for row in data]


def _query_once(query: str, project_id: str, num_results: int) -> dict[str, Any]:
    from databricks.sdk.service.vectorsearch import (
        RerankerConfig,
        RerankerConfigRerankerParameters,
    )

    _, index_name, _ = _search_config()
    kwargs: dict[str, Any] = {
        "index_name": index_name,
        "columns": [
            "chunk_id", "document_id", "document_name", "doc_type",
            "page_number", "chunk_to_retrieve", "metadata_json",
        ],
        "query_text": query,
        "query_type": "HYBRID",
        "filters_json": json.dumps({"project_id": project_id}),
        "num_results": num_results,
    }
    global _RERANKER_AVAILABLE
    reranker_requested = bool(settings.DATABRICKS_AI_SEARCH_RERANK_ENABLED)
    use_reranker = reranker_requested and _RERANKER_AVAILABLE is not False
    if use_reranker:
        kwargs["reranker"] = RerankerConfig(
            model="databricks_reranker",
            parameters=RerankerConfigRerankerParameters(
                columns_to_rerank=["chunk_to_retrieve"],
            ),
        )
    with trace_span(
        "databricks_ai_search.query",
        span_type="RETRIEVER",
        inputs={"query": query, "project_id": project_id, "num_results": num_results},
        attributes={"index_name": index_name, "reranker": bool(settings.DATABRICKS_AI_SEARCH_RERANK_ENABLED)},
    ) as span:
        workspace = _workspace_client()
        reranker_used = use_reranker
        reranker_fallback = ""
        try:
            response = workspace.vector_search_indexes.query_index(**kwargs)
        except Exception as exc:
            if not use_reranker or "reranker" not in str(exc).lower():
                raise
            _RERANKER_AVAILABLE = False
            kwargs.pop("reranker", None)
            response = workspace.vector_search_indexes.query_index(**kwargs)
            reranker_used = False
            reranker_fallback = str(exc)
        rows = _response_rows(response)
        if span is not None:
            span.set_outputs({"result_count": len(rows), "reranker_used": reranker_used})
        return {
            "rows": rows,
            "reranker_used": reranker_used,
            "reranker_fallback": reranker_fallback,
        }


def _managed_item(row: dict[str, Any], rank: int, pool_size: int) -> RetrievedChunk | None:
    indexed_chunk_id = str(row.get("chunk_id") or "")
    content = str(row.get("chunk_to_retrieve") or "")
    if not indexed_chunk_id or not content:
        return None
    raw_metadata = row.get("metadata_json")
    try:
        metadata = json.loads(raw_metadata) if isinstance(raw_metadata, str) else dict(raw_metadata or {})
    except (json.JSONDecodeError, TypeError, ValueError):
        metadata = {}
    chunk_id = str(metadata.get("traceaudit_chunk_id") or indexed_chunk_id)
    raw_score = row.get("score", row.get("_score"))
    try:
        score = float(raw_score)
    except (TypeError, ValueError):
        score = (pool_size - rank) / max(pool_size, 1)
    page = row.get("page_number")
    try:
        page_number = int(page) if page is not None else None
    except (TypeError, ValueError):
        page_number = None
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id=str(row.get("document_id") or ""),
        document_name=str(row.get("document_name") or ""),
        doc_type=str(row.get("doc_type") or "Document"),
        page_number=page_number,
        content=content,
        score=score,
        matched_terms=["databricks_ai_search"],
        metadata={**metadata, "retrieval_backend": "databricks-ai-search"},
    )


async def _retrieve_managed(
    requirement_text: str,
    chunks: list[dict[str, Any]],
    *,
    project_id: str,
    top_k: int,
    exclude_doc_names: Optional[set[str]],
    condition_queries: Optional[list[str]],
    strict_reranker: bool = False,
) -> tuple[list[RetrievedChunk], SearchRunDiagnostics]:
    started = time.perf_counter()
    all_queries = list(dict.fromkeys(
        query.strip() for query in (requirement_text, *(condition_queries or []))
        if query and query.strip()
    ))
    query_limit = max(1, int(settings.DATABRICKS_AI_SEARCH_MAX_QUERIES_PER_REQUIREMENT))
    queries = all_queries[:query_limit]
    candidate_count = max(top_k, int(settings.DATABRICKS_AI_SEARCH_CANDIDATE_COUNT))
    semaphore = asyncio.Semaphore(3)

    async def run(query: str) -> dict[str, Any]:
        async with semaphore:
            return await asyncio.to_thread(_query_once, query, project_id, candidate_count)

    query_results = await asyncio.gather(*(run(query) for query in queries))
    excluded = {name.lower() for name in (exclude_doc_names or set())}
    local_by_id = {
        str(chunk.get("id") or chunk.get("chunk_id") or ""): chunk
        for chunk in chunks
    }
    merged: dict[str, RetrievedChunk] = {}
    rankings: list[list[str]] = []
    reranker_used = True
    reranker_fallbacks: list[str] = []
    for pool_index, query_result in enumerate(query_results):
        # Accept list results from lightweight test doubles written for the
        # pre-diagnostics adapter.
        if isinstance(query_result, list):
            rows = query_result
            query_reranker_used = bool(settings.DATABRICKS_AI_SEARCH_RERANK_ENABLED)
            query_fallback = ""
        else:
            rows = query_result.get("rows") or []
            query_reranker_used = bool(query_result.get("reranker_used"))
            query_fallback = str(query_result.get("reranker_fallback") or "")
        reranker_used = reranker_used and query_reranker_used
        if query_fallback:
            reranker_fallbacks.append(query_fallback)
        ranking: list[str] = []
        for rank, row in enumerate(rows):
            item = _managed_item(row, rank, len(rows))
            local = local_by_id.get(item.chunk_id) if item is not None else None
            if item is None or local is None or item.document_name.lower() in excluded:
                continue
            item.document_profile = local.get("document_profile")
            item.metadata = {
                **dict(local.get("metadata") or {}),
                **dict(item.metadata or {}),
            }
            key = _chunk_key(item)
            ranking.append(key)
            existing = merged.get(key)
            if existing is None or item.score > existing.score:
                merged[key] = item
        if pool_index > 0:
            rankings.append(ranking)
    custom_reranker: dict[str, Any]
    if settings.DATABRICKS_CUSTOM_RERANKER_ENABLED:
        selected, custom_reranker = await rerank_candidates(
            requirement_text,
            list(merged.values()),
            condition_queries=condition_queries,
            top_k=top_k,
            strict=strict_reranker,
        )
    else:
        selected = _condition_aware_rerank(list(merged.values()), rankings, top_k)
        custom_reranker = {"enabled": False, "used": False}
    selected = _expand_structural_context(selected, chunks)
    _, index_name, _ = _search_config()
    return selected, SearchRunDiagnostics(
        requested_backend="databricks",
        backend_used="databricks-ai-search",
        index_name=index_name,
        embedding_model=settings.DATABRICKS_AI_SEARCH_EMBEDDING_MODEL,
        reranker_enabled=bool(settings.DATABRICKS_AI_SEARCH_RERANK_ENABLED),
        queries=len(queries),
        candidates=len(merged),
        query_seconds=round(time.perf_counter() - started, 3),
        details={
            "query_limit": query_limit,
            "queries_omitted": max(0, len(all_queries) - len(queries)),
            "reranker_used": bool(settings.DATABRICKS_AI_SEARCH_RERANK_ENABLED) and reranker_used,
            "reranker_fallback": reranker_fallbacks[0] if reranker_fallbacks else "",
            "custom_reranker": custom_reranker,
        },
    )


async def retrieve_with_fallback(
    requirement_text: str,
    chunks: list[dict[str, Any]],
    *,
    project_id: str,
    chunk_embeddings: Optional[list[Optional[list[float]]]] = None,
    top_k: int = 5,
    min_score: float = 0.3,
    exclude_doc_names: Optional[set[str]] = None,
    condition_queries: Optional[list[str]] = None,
    backend: str = "auto",
    strict: bool = False,
    strict_reranker: bool = False,
) -> tuple[list[RetrievedChunk], SearchRunDiagnostics]:
    """Use managed hybrid search when requested, then fall back atomically."""
    use_managed = backend == "databricks" or (
        backend == "auto" and settings.DATABRICKS_AI_SEARCH_ENABLED
    )
    if use_managed:
        try:
            return await _retrieve_managed(
                requirement_text,
                chunks,
                project_id=project_id,
                top_k=top_k,
                exclude_doc_names=exclude_doc_names,
                condition_queries=condition_queries,
                strict_reranker=strict_reranker,
            )
        except Exception as exc:
            if strict:
                raise
            logger.warning("Databricks AI Search failed; using local hybrid retrieval: %s", exc)
            fallback_reason = str(exc)
    else:
        fallback_reason = ""

    retrieval_top_k = (
        max(top_k, int(settings.DATABRICKS_CUSTOM_RERANKER_CANDIDATE_COUNT))
        if settings.DATABRICKS_CUSTOM_RERANKER_ENABLED
        else top_k
    )
    values = await retrieve_candidate_evidence_hybrid(
        requirement_text,
        chunks,
        chunk_embeddings=chunk_embeddings,
        top_k=retrieval_top_k,
        min_score=min_score,
        exclude_doc_names=exclude_doc_names,
        condition_queries=condition_queries,
    )
    values, custom_reranker = await rerank_candidates(
        requirement_text,
        values,
        condition_queries=condition_queries,
        top_k=top_k,
        strict=strict_reranker,
    )
    values = _expand_structural_context(values, chunks)
    return values, SearchRunDiagnostics(
        requested_backend="databricks" if use_managed else "local",
        backend_used="local-hybrid",
        fallback_reason=fallback_reason,
        queries=1 + len(condition_queries or []),
        candidates=len(values),
        details={"custom_reranker": custom_reranker},
    )
