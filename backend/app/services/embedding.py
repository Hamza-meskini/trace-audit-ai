"""Vector indexing and semantic embedding service."""

import math
import re
from typing import Optional
from app.config import settings


def tokenize(text: str) -> list[str]:
    """Simple alphanumeric tokenizer."""
    return re.findall(r"\b[a-zA-Z0-9_-]+\b", text.lower())


def compute_bm25_score(query_tokens: list[str], doc_tokens: list[str], avg_doc_len: float = 50.0, k1: float = 1.5, b: float = 0.75) -> float:
    """Lightweight deterministic BM25 / lexical similarity for evidence candidate retrieval."""
    if not query_tokens or not doc_tokens:
        return 0.0

    doc_len = len(doc_tokens)
    doc_freq = {}
    for t in doc_tokens:
        doc_freq[t] = doc_freq.get(t, 0) + 1

    score = 0.0
    for q in query_tokens:
        if q in doc_freq:
            tf = doc_freq[q]
            # IDF proxy
            numerator = tf * (k1 + 1)
            denominator = tf + k1 * (1 - b + b * (doc_len / max(avg_doc_len, 1.0)))
            score += (numerator / denominator)

    return score


async def get_embedding(text: str) -> Optional[list[float]]:
    """Compute dense embedding using OpenAI if configured."""
    if not settings.OPENAI_API_KEY:
        return None

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        response = await client.embeddings.create(
            model=settings.EMBEDDING_MODEL,
            input=text[:4000],
        )
        return response.data[0].embedding
    except Exception:
        return None
