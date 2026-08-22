"""Evidence retrieval and candidate reranking service.

Finds relevant evidence chunks across all project documents for a given requirement
using two-stage lexical BM25 retrieval, parameter term boosting, and document-diversified reranking.
"""

from typing import Any, Optional
from dataclasses import dataclass
import re
from app.services.embedding import tokenize, compute_bm25_score


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


def retrieve_candidate_evidence(
    requirement_text: str,
    chunks: list[dict[str, Any]],
    top_k: int = 5,
    min_score: float = 0.3,
) -> list[RetrievedChunk]:
    """Retrieve top-K most relevant evidence chunks for a requirement with candidate pool expansion and diversified reranking."""
    query_tokens = tokenize(requirement_text)
    if not query_tokens or not chunks:
        return []

    # Extract requirement code if present in query
    req_code_match = REQ_CODE_REGEX.search(requirement_text)
    req_code_token = req_code_match.group(1).lower() if req_code_match else None

    # Key parameter terms for boosting
    core_param_tokens = {
        t for t in query_tokens
        if len(t) > 3 and t not in ("the", "shall", "with", "from", "that", "this", "over", "under", "within", "must", "unit", "system", "requirement")
    }

    # Calculate average doc length
    total_tokens = sum(len(tokenize(c.get("content", ""))) for c in chunks)
    avg_len = total_tokens / max(len(chunks), 1)

    candidate_pool: list[RetrievedChunk] = []

    for c in chunks:
        content = c.get("content", "")
        doc_tokens = tokenize(content)
        content_lower = content.lower()
        score = compute_bm25_score(query_tokens, doc_tokens, avg_doc_len=avg_len)

        # 1. Exact Requirement Code Boost (+5.0)
        if req_code_token and req_code_token in doc_tokens:
            score += 5.0

        # 2. Number & Unit Parameter Match Boost (+2.0)
        for q in query_tokens:
            if any(ch.isdigit() for ch in q) and q in doc_tokens:
                score += 2.0

        # 3. Core Parameter Keyword Match Boost (+1.5 per matched keyword)
        matched_params = [t for t in core_param_tokens if t in doc_tokens]
        score += len(matched_params) * 1.5

        # 4. Conflict / Specification Indicator Boost (+1.0 for datasheets and test verdicts)
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

    if not candidate_pool:
        return []

    # Sort initial candidate pool by score
    candidate_pool.sort(key=lambda x: x.score, reverse=True)

    # Reranking with Source Diversification:
    # Ensure diverse document representation (test reports, datasheets, matrix rows)
    # so conflict datasheets are not crowded out by multiple identical matrix rows.
    selected: list[RetrievedChunk] = []
    seen_docs: dict[str, int] = {}
    deferred: list[RetrievedChunk] = []

    for item in candidate_pool:
        doc_key = item.document_name
        doc_count = seen_docs.get(doc_key, 0)

        # Allow max 2 chunks per single document in initial selection pass
        if doc_count < 2 or len(candidate_pool) < top_k:
            selected.append(item)
            seen_docs[doc_key] = doc_count + 1
            if len(selected) >= top_k:
                break
        else:
            deferred.append(item)

    # If top_k not filled yet, fill with highest remaining deferred chunks
    if len(selected) < top_k and deferred:
        remaining_needed = top_k - len(selected)
        selected.extend(deferred[:remaining_needed])

    return selected[:top_k]
