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
            ))

    return candidate_pool


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
) -> list[RetrievedChunk]:
    """Synchronous BM25-only retrieval (backward-compatible API for non-async callers)."""
    chunks = _filter_excluded(chunks, exclude_doc_names)
    candidate_pool = _bm25_retrieve(requirement_text, chunks, top_k=top_k * 3, min_score=min_score)
    return _diversified_rerank(candidate_pool, top_k)


async def retrieve_candidate_evidence_hybrid(
    requirement_text: str,
    chunks: list[dict[str, Any]],
    chunk_embeddings: Optional[list[Optional[list[float]]]] = None,
    top_k: int = 5,
    min_score: float = 0.3,
    exclude_doc_names: Optional[set[str]] = None,
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
    bm25_pool = _bm25_retrieve(requirement_text, chunks, top_k=top_k * 3, min_score=min_score * 0.5)

    if not bm25_pool:
        return []

    # Step 2: If no embedding capability, fall back to pure BM25
    if not _has_embedding_key():
        return _diversified_rerank(bm25_pool, top_k)

    # Step 3: Get query embedding
    query_embedding = await embed_single(requirement_text, task_type="RETRIEVAL_QUERY")
    if query_embedding is None:
        logger.info("Embedding unavailable for query; using pure BM25 retrieval")
        return _diversified_rerank(bm25_pool, top_k)

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
    return _diversified_rerank(bm25_pool, top_k)


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
