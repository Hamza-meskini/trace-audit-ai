"""Evidence retrieval and candidate reranking service.

Finds relevant evidence chunks across all project documents for a given requirement
using a hybrid two-stage approach:
  1. Lexical BM25 retrieval with parameter term boosting (always available)
  2. Semantic similarity via Gemini text-embedding-005 (when API key is present)

The final score is a weighted combination: α·BM25_norm + (1-α)·cosine_similarity
with document-diversified reranking to ensure cross-document coverage.

Falls back gracefully to pure BM25 when embeddings are unavailable.
"""

import logging
from typing import Any, Optional
from dataclasses import dataclass
import re
from app.services.embedding import (
    tokenize,
    compute_bm25_score,
    embed_single,
    embed_batch,
    cosine_similarity,
    _has_embedding_key,
)

logger = logging.getLogger("traceaudit.retrieval")


@dataclass
class RetrievedChunk:
    chunk_id: str
    document_id: str
    document_name: str
    doc_type: str
    page_number: Optional[int]
    content: str
    score: float
    matched_terms: list[str]
    document_profile: Optional[dict[str, Any]] = None
    metadata: Optional[dict[str, Any]] = None


REQ_CODE_REGEX = re.compile(r"\b(REQ[-_]?[A-Za-z0-9_-]*\d+)\b", re.IGNORECASE)

# Hybrid scoring weight: α for BM25, (1-α) for semantic embedding
# 0.4 gives slight preference to semantic understanding while preserving
# exact-match strength from BM25 (requirement codes, numeric values)
HYBRID_ALPHA = 0.4


def _bm25_retrieve(
    requirement_text: str,
    chunks: list[dict[str, Any]],
    top_k: int = 5,
    min_score: float = 0.3,
) -> list[RetrievedChunk]:
    """Pure BM25 retrieval with boosting (deterministic, always available)."""
    query_tokens = tokenize(requirement_text)
    if not query_tokens or not chunks:
        return []

    req_code_match = REQ_CODE_REGEX.search(requirement_text)
    req_code_token = req_code_match.group(1).lower() if req_code_match else None
    req_suffix_match = re.search(r"(\d+)$", req_code_token or "")
    req_suffix = req_suffix_match.group(1) if req_suffix_match else None

    core_param_tokens = {
        t for t in query_tokens
        if len(t) > 3 and t not in ("the", "shall", "with", "from", "that", "this", "over", "under", "within", "must", "unit", "system", "requirement")
    }
    query_lower = requirement_text.lower()
    visual_query = any(term in query_lower for term in (
        "figure", "image", "visual", "symbol", "marking", "label", "diagram", "photograph",
    ))
    structured_query = visual_query or any(term in query_lower for term in (
        "table", "formula", "calculate", "measured", "result", "v1", "v2", "resistance",
    ))

    total_tokens = sum(len(tokenize(c.get("content", ""))) for c in chunks)
    avg_len = total_tokens / max(len(chunks), 1)

    candidate_pool: list[RetrievedChunk] = []

    for c in chunks:
        content = c.get("content", "")
        content_codes = {m.group(1).lower() for m in REQ_CODE_REGEX.finditer(content)}
        if req_code_token and content_codes and req_code_token not in content_codes:
            # A chunk explicitly assigned to another requirement is not a
            # candidate merely because it shares generic technical words.
            continue
        doc_tokens = tokenize(content)
        score = compute_bm25_score(query_tokens, doc_tokens, avg_doc_len=avg_len)

        # 1. Exact Requirement Code Boost (+5.0)
        if req_code_token and req_code_token in doc_tokens:
            score += 5.0

        # Test reports commonly use TC-DOMAIN-NNN rather than the SRS code.
        if req_suffix and re.search(rf"\bTC[-_][A-Za-z0-9_-]*[-_]{re.escape(req_suffix)}\b", content, re.IGNORECASE):
            score += 6.0

        # 2. Number & Unit Parameter Match Boost (+2.0)
        for q in query_tokens:
            if any(ch.isdigit() for ch in q) and q in doc_tokens:
                score += 2.0

        # 3. Core Parameter Keyword Match Boost (+1.5 per matched keyword)
        matched_params = [t for t in core_param_tokens if t in doc_tokens]
        score += len(matched_params) * 1.5

        # 4. Conflict / Specification Indicator Boost (+1.0)
        doc_name_lower = c.get("document_name", "").lower()
        if any(k in doc_name_lower for k in ["datasheet", "ds-", "report", "matrix", "compliance"]):
            if matched_params or (req_code_token and req_code_token in doc_tokens):
                score += 1.0

        # 5. Structure-intent boost. Captions and compact table rows contain
        # fewer prose terms than ordinary paragraphs, so pure BM25 otherwise
        # suppresses exactly the blocks needed for visual/formula conditions.
        block_type = str((c.get("metadata") or {}).get("block_type") or "").lower()
        if visual_query and block_type == "figure":
            score += 5.0
        elif structured_query and block_type in {"table", "formula", "checkbox"}:
            score += 2.0

        if score >= min_score:
            matched = [t for t in query_tokens if t in doc_tokens and len(t) > 2]
            candidate_pool.append(RetrievedChunk(
                chunk_id=c.get("id", "") or c.get("chunk_id", ""),
                document_id=c.get("document_id", ""),
                document_name=c.get("document_name", ""),
                doc_type=c.get("doc_type", "Document"),
                page_number=c.get("page_number"),
                content=content,
                score=score,
                matched_terms=matched,
                document_profile=c.get("document_profile"),
                metadata=c.get("metadata"),
            ))

    candidate_pool.sort(key=lambda item: item.score, reverse=True)
    return candidate_pool[:top_k]


def _chunk_key(item: RetrievedChunk) -> str:
    """Stable identity for merging the same chunk from multiple queries."""
    return item.chunk_id or f"{item.document_name}|{item.page_number}|{item.content[:120]}"


def _merge_candidate_pools(*pools: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """Union query-specific pools while retaining their strongest lexical score."""
    merged: dict[str, RetrievedChunk] = {}
    for pool in pools:
        for item in pool:
            key = _chunk_key(item)
            existing = merged.get(key)
            if existing is None:
                merged[key] = item
                continue
            if item.score > existing.score:
                existing.score = item.score
            existing.matched_terms = sorted(set(existing.matched_terms) | set(item.matched_terms))
    return list(merged.values())


def _condition_aware_rerank(
    candidate_pool: list[RetrievedChunk],
    condition_rankings: list[list[str]],
    top_k: int,
) -> list[RetrievedChunk]:
    """Select a relevance leader plus bounded per-condition coverage.

    A single requirement-level ranking repeatedly missed evidence for later
    atomic conditions. Reserve only high-ranked candidates from condition
    queries, then fill remaining slots by hybrid relevance. Python is choosing
    context coverage here, not deciding whether any passage proves a condition.
    """
    if not condition_rankings:
        return _diversified_rerank(list(candidate_pool), top_k)

    best_rank_signal: dict[str, float] = {}
    condition_hits: dict[str, int] = {}
    for ranking in condition_rankings:
        ranking_size = max(len(ranking), 1)
        for index, key in enumerate(ranking):
            # Top condition hit=1.0, with a smooth decay through the pool.
            rank_signal = (ranking_size - index) / ranking_size
            best_rank_signal[key] = max(best_rank_signal.get(key, 0.0), rank_signal)
            condition_hits[key] = condition_hits.get(key, 0) + 1

    max_base_score = max((abs(item.score) for item in candidate_pool), default=1.0) or 1.0
    for item in candidate_pool:
        key = _chunk_key(item)
        rank_signal = best_rank_signal.get(key, 0.0)
        if not rank_signal:
            continue
        multi_condition_signal = min(condition_hits.get(key, 1) - 1, 2) / 2
        # At most a 10% relevance bonus: useful for close candidates, never a
        # hard reservation that can displace a materially stronger passage.
        item.score += max_base_score * (0.08 * rank_signal + 0.02 * multi_condition_signal)

    ranked = sorted(candidate_pool, key=lambda item: item.score, reverse=True)
    by_key = {_chunk_key(item): item for item in ranked}
    selected: list[RetrievedChunk] = []
    selected_keys: set[str] = set()

    def add(item: RetrievedChunk | None) -> None:
        if item is None or len(selected) >= top_k:
            return
        key = _chunk_key(item)
        if key in selected_keys:
            return
        selected.append(item)
        selected_keys.add(key)

    # Keep the strongest global hit, then cover distinct atomic-condition
    # queries while retaining at least one slot for the global ranking.
    add(ranked[0] if ranked else None)
    coverage_limit = max(1, top_k - 1)
    for ranking in condition_rankings:
        if len(selected) >= coverage_limit:
            break
        add(next((by_key.get(key) for key in ranking if key in by_key), None))

    for item in ranked:
        add(item)
        if len(selected) >= top_k:
            break
    return selected[:top_k]


def _expand_structural_context(
    selected: list[RetrievedChunk],
    chunks: list[dict[str, Any]],
    *,
    max_extra: int = 4,
) -> list[RetrievedChunk]:
    """Attach bounded same-page neighbors for tables, forms, and figures.

    The selected item remains first, so ranking metrics stay meaningful. Extra
    blocks are context only: they restore headers, result rows, captions, and
    footnotes that layout-aware ingestion intentionally stores separately.
    """
    if not selected or max_extra <= 0:
        return selected
    positions = {
        (chunk.get("id") or chunk.get("chunk_id")): index
        for index, chunk in enumerate(chunks)
    }
    seen = {_chunk_key(item) for item in selected}
    extras: list[RetrievedChunk] = []
    for item in selected:
        if len(extras) >= max_extra:
            break
        metadata = item.metadata or {}
        block_type = str(metadata.get("block_type") or "").lower()
        structured = block_type in {"table", "figure", "formula", "checkbox"}
        if not structured:
            continue
        position = positions.get(item.chunk_id)
        if position is None:
            continue
        for offset in (-2, -1, 1, 2):
            neighbor_index = position + offset
            if not 0 <= neighbor_index < len(chunks):
                continue
            neighbor = chunks[neighbor_index]
            if (
                neighbor.get("document_name") != item.document_name
                or neighbor.get("page_number") != item.page_number
            ):
                continue
            neighbor_metadata = dict(neighbor.get("metadata") or {})
            neighbor_metadata["context_only"] = True
            candidate = RetrievedChunk(
                chunk_id=neighbor.get("id", "") or neighbor.get("chunk_id", ""),
                document_id=neighbor.get("document_id", ""),
                document_name=neighbor.get("document_name", ""),
                doc_type=neighbor.get("doc_type", "Document"),
                page_number=neighbor.get("page_number"),
                content=neighbor.get("content", ""),
                score=max(item.score - 1e-6, 0.0),
                matched_terms=["structural_context"],
                document_profile=neighbor.get("document_profile"),
                metadata=neighbor_metadata,
            )
            key = _chunk_key(candidate)
            if key in seen:
                continue
            seen.add(key)
            extras.append(candidate)
            if len(extras) >= max_extra:
                break
    return selected + extras


def _diversified_rerank(candidate_pool: list[RetrievedChunk], top_k: int) -> list[RetrievedChunk]:
    """Rerank with source diversification to ensure cross-document coverage."""
    if not candidate_pool:
        return []

    candidate_pool.sort(key=lambda x: x.score, reverse=True)

    selected: list[RetrievedChunk] = []
    seen_docs: dict[str, int] = {}
    deferred: list[RetrievedChunk] = []

    for item in candidate_pool:
        doc_key = item.document_name
        doc_count = seen_docs.get(doc_key, 0)

        # Allow max 2 chunks per single document in initial selection pass
        per_doc_limit = 1 if ("matrix" in doc_key.lower() or doc_key.lower().endswith(".xlsx")) else 2
        if doc_count < per_doc_limit or len(candidate_pool) < top_k:
            selected.append(item)
            seen_docs[doc_key] = doc_count + 1
            if len(selected) >= top_k:
                break
        else:
            deferred.append(item)

    if len(selected) < top_k and deferred:
        remaining_needed = top_k - len(selected)
        selected.extend(deferred[:remaining_needed])

    return selected[:top_k]


def _filter_excluded(
    chunks: list[dict[str, Any]],
    exclude_doc_names: Optional[set[str]] = None,
) -> list[dict[str, Any]]:
    """Drop chunks belonging to excluded documents (e.g. specification self-reference)."""
    if not exclude_doc_names:
        return chunks
    excluded = {name.lower() for name in exclude_doc_names}
    return [c for c in chunks if c.get("document_name", "").lower() not in excluded]


def retrieve_candidate_evidence(
    requirement_text: str,
    chunks: list[dict[str, Any]],
    top_k: int = 5,
    min_score: float = 0.3,
    exclude_doc_names: Optional[set[str]] = None,
    condition_queries: Optional[list[str]] = None,
) -> list[RetrievedChunk]:
    """Synchronous BM25-only retrieval (backward-compatible API for non-async callers)."""
    chunks = _filter_excluded(chunks, exclude_doc_names)
    overall_pool = _bm25_retrieve(requirement_text, chunks, top_k=top_k * 4, min_score=min_score)
    condition_pools = [
        _bm25_retrieve(query, chunks, top_k=max(top_k, 3), min_score=min_score * 0.5)
        for query in (condition_queries or [])
        if query.strip()
    ]
    candidate_pool = _merge_candidate_pools(overall_pool, *condition_pools)
    rankings = [[_chunk_key(item) for item in pool] for pool in condition_pools]
    selected = _condition_aware_rerank(candidate_pool, rankings, top_k)
    return _expand_structural_context(selected, chunks)


async def retrieve_candidate_evidence_hybrid(
    requirement_text: str,
    chunks: list[dict[str, Any]],
    chunk_embeddings: Optional[list[Optional[list[float]]]] = None,
    top_k: int = 5,
    min_score: float = 0.3,
    exclude_doc_names: Optional[set[str]] = None,
    condition_queries: Optional[list[str]] = None,
) -> list[RetrievedChunk]:
    """Hybrid retrieval: BM25 + Gemini text-embedding-005 semantic similarity.

    If embeddings are unavailable (no API key or API failure), falls back to pure BM25.
    Pre-computed chunk_embeddings can be passed in to avoid redundant embedding calls
    across multiple requirements in the same audit run.

    `exclude_doc_names` removes documents (typically the specification itself) from
    the candidate pool so self-referential chunks don't crowd out true evidence.

    Score formula: α·BM25_normalized + (1-α)·cosine_similarity
    """
    # Step 0: Map precomputed embeddings before document filtering
    precomputed_map: dict[str, list[float]] = {}
    if chunk_embeddings:
        for i, emb in enumerate(chunk_embeddings):
            if emb and i < len(chunks):
                content = chunks[i].get("content", "")
                if content:
                    precomputed_map[content[:2048]] = emb

    chunks = _filter_excluded(chunks, exclude_doc_names)

    # Step 1: BM25 retrieval (get a wider candidate pool)
    overall_pool = _bm25_retrieve(
        requirement_text,
        chunks,
        top_k=top_k * 4,
        min_score=min_score * 0.5,
    )
    condition_pools = [
        _bm25_retrieve(query, chunks, top_k=max(top_k, 3), min_score=min_score * 0.25)
        for query in (condition_queries or [])
        if query.strip()
    ]
    bm25_pool = _merge_candidate_pools(overall_pool, *condition_pools)
    condition_rankings = [[_chunk_key(item) for item in pool] for pool in condition_pools]

    if not bm25_pool:
        return []

    # Step 2: If no embedding capability, fall back to pure BM25
    if not _has_embedding_key():
        selected = _condition_aware_rerank(bm25_pool, condition_rankings, top_k)
        return _expand_structural_context(selected, chunks)

    # Step 3: Get query embedding
    query_embedding = await embed_single(requirement_text, task_type="RETRIEVAL_QUERY")
    if query_embedding is None:
        logger.info("Embedding unavailable for query; using pure BM25 retrieval")
        selected = _condition_aware_rerank(bm25_pool, condition_rankings, top_k)
        return _expand_structural_context(selected, chunks)

    # Step 4: Lookup chunk embeddings for candidates in the pool
    needs_embedding: list[int] = []
    candidate_embeddings: list[Optional[list[float]]] = [None] * len(bm25_pool)

    for i, candidate in enumerate(bm25_pool):
        cached = precomputed_map.get(candidate.content[:2048])
        if cached:
            candidate_embeddings[i] = cached
        else:
            needs_embedding.append(i)

    # Batch embed uncached candidates
    if needs_embedding:
        texts_to_embed = [bm25_pool[i].content for i in needs_embedding]
        batch_results = await embed_batch(texts_to_embed, task_type="RETRIEVAL_DOCUMENT")
        for j, idx in enumerate(needs_embedding):
            candidate_embeddings[idx] = batch_results[j]

    # Step 5: Compute hybrid scores
    # Normalize BM25 scores to [0, 1] range
    max_bm25 = max(c.score for c in bm25_pool) if bm25_pool else 1.0
    if max_bm25 == 0:
        max_bm25 = 1.0

    for i, candidate in enumerate(bm25_pool):
        bm25_norm = candidate.score / max_bm25  # Normalized to [0, 1]

        emb = candidate_embeddings[i]
        if emb:
            semantic_score = cosine_similarity(query_embedding, emb)
            # Hybrid combination
            candidate.score = HYBRID_ALPHA * bm25_norm + (1 - HYBRID_ALPHA) * semantic_score
        else:
            # No embedding available for this chunk — use pure BM25 (scaled down)
            candidate.score = HYBRID_ALPHA * bm25_norm

    # Step 6: Diversified rerank on hybrid scores
    selected = _condition_aware_rerank(bm25_pool, condition_rankings, top_k)
    return _expand_structural_context(selected, chunks)


async def precompute_chunk_embeddings(
    chunks: list[dict[str, Any]],
) -> list[Optional[list[float]]]:
    """Pre-compute embeddings for all evidence chunks in a single batch call.

    Called once at the start of an audit pipeline run so individual requirement
    retrievals can reuse the embeddings without redundant API calls.

    Returns list of embeddings (or None for failed items) aligned with the input chunks list.
    """
    if not _has_embedding_key() or not chunks:
        return [None] * len(chunks)

    texts = [c.get("content", "")[:2048] for c in chunks]
    logger.info(f"Pre-computing semantic embeddings for {len(texts)} evidence chunks via Gemini text-embedding-005...")

    embeddings = await embed_batch(texts, task_type="RETRIEVAL_DOCUMENT")

    success_count = sum(1 for e in embeddings if e is not None)
    logger.info(f"Successfully embedded {success_count}/{len(texts)} chunks")

    return embeddings
