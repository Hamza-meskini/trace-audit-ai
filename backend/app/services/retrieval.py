"""Evidence retrieval service.

Finds relevant evidence chunks across all project documents for a given requirement.
"""

from typing import Any
from dataclasses import dataclass
from app.services.embedding import tokenize, compute_bm25_score


@dataclass
class RetrievedChunk:
    chunk_id: str
    document_id: str
    document_name: str
    doc_type: str
    page_number: int | None
    content: str
    score: float
    matched_terms: list[str]


def retrieve_candidate_evidence(
    requirement_text: str,
    chunks: list[dict[str, Any]],
    top_k: int = 5,
    min_score: float = 0.5,
) -> list[RetrievedChunk]:
    """Retrieve top-K most relevant evidence chunks for a requirement using BM25 and semantic matching."""
    query_tokens = tokenize(requirement_text)
    if not query_tokens or not chunks:
        return []

    scored_results: list[RetrievedChunk] = []

    # Calculate average doc length
    total_tokens = sum(len(tokenize(c.get("content", ""))) for c in chunks)
    avg_len = total_tokens / max(len(chunks), 1)

    for c in chunks:
        content = c.get("content", "")
        doc_tokens = tokenize(content)
        score = compute_bm25_score(query_tokens, doc_tokens, avg_doc_len=avg_len)

        # Check for exact numbers / parameters in both
        # Bonus for matching numbers (e.g. 18, 32, -20, 70, IP54)
        for q in query_tokens:
            if any(ch.isdigit() for ch in q) and q in doc_tokens:
                score += 2.0

        if score >= min_score:
            matched = [t for t in query_tokens if t in doc_tokens and len(t) > 2]
            scored_results.append(RetrievedChunk(
                chunk_id=c.get("id", ""),
                document_id=c.get("document_id", ""),
                document_name=c.get("document_name", ""),
                doc_type=c.get("doc_type", "Document"),
                page_number=c.get("page_number"),
                content=content,
                score=score,
                matched_terms=matched,
            ))

    scored_results.sort(key=lambda x: x.score, reverse=True)
    return scored_results[:top_k]
