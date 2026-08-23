"""Lexical (BM25) and semantic (Gemini text-embedding-005) utilities for evidence retrieval.

Provides:
- BM25 tokenization and scoring (deterministic, always available)
- Gemini text-embedding-005 semantic embeddings (API-based, graceful fallback)
- Cosine similarity for embedding comparison
- Batch embedding with in-memory cache to avoid redundant API calls
"""

import re
import math
import logging
import hashlib
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger("traceaudit.embedding")

# ── BM25 Lexical Utilities ──────────────────────────────────────────────────

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


# ── Gemini text-embedding-005 Semantic Embeddings ────────────────────────────

GEMINI_EMBEDDING_MODEL = "text-embedding-005"
GEMINI_EMBED_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_EMBEDDING_MODEL}:embedContent"
GEMINI_BATCH_EMBED_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_EMBEDDING_MODEL}:batchEmbedContents"

# In-memory embedding cache (keyed by content hash) to avoid redundant API calls
# within the same audit run. Cleared between server restarts.
_embedding_cache: dict[str, list[float]] = {}

# Maximum texts per batch call (Gemini allows up to 100)
BATCH_SIZE = 64


def _content_hash(text: str) -> str:
    """Short hash for cache key."""
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:16]


def _has_embedding_key() -> bool:
    """Check if a Gemini API key is available for embedding calls."""
    return bool(settings.effective_gemini_api_key)


async def embed_single(text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> Optional[list[float]]:
    """Get embedding for a single text using Gemini text-embedding-005.

    Args:
        text: The text to embed (truncated to 2048 chars for efficiency).
        task_type: One of RETRIEVAL_QUERY, RETRIEVAL_DOCUMENT, SEMANTIC_SIMILARITY, CLASSIFICATION.

    Returns:
        768-dimensional embedding vector, or None if API is unavailable.
    """
    if not _has_embedding_key():
        return None

    cache_key = _content_hash(text)
    if cache_key in _embedding_cache:
        return _embedding_cache[cache_key]

    api_key = settings.effective_gemini_api_key
    url = f"{GEMINI_EMBED_API_URL}?key={api_key}"

    payload = {
        "model": f"models/{GEMINI_EMBEDDING_MODEL}",
        "content": {"parts": [{"text": text[:2048]}]},
        "taskType": task_type,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            values = data.get("embedding", {}).get("values", [])
            if values:
                _embedding_cache[cache_key] = values
                return values
    except Exception as ex:
        logger.warning(f"Gemini embedding call failed: {ex}")

    return None


async def embed_batch(texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT") -> list[Optional[list[float]]]:
    """Get embeddings for a batch of texts using Gemini batchEmbedContents.

    Checks cache first, only sends uncached texts to the API.
    Returns list of embedding vectors (or None for failed items) in same order as input.
    """
    if not _has_embedding_key():
        return [None] * len(texts)

    results: list[Optional[list[float]]] = [None] * len(texts)
    uncached_indices: list[int] = []
    uncached_texts: list[str] = []

    # Check cache first
    for i, text in enumerate(texts):
        cache_key = _content_hash(text)
        if cache_key in _embedding_cache:
            results[i] = _embedding_cache[cache_key]
        else:
            uncached_indices.append(i)
            uncached_texts.append(text)

    if not uncached_texts:
        return results

    api_key = settings.effective_gemini_api_key
    url = f"{GEMINI_BATCH_EMBED_API_URL}?key={api_key}"

    # Process in sub-batches of BATCH_SIZE
    for batch_start in range(0, len(uncached_texts), BATCH_SIZE):
        batch_texts = uncached_texts[batch_start:batch_start + BATCH_SIZE]
        batch_indices = uncached_indices[batch_start:batch_start + BATCH_SIZE]

        payload = {
            "requests": [
                {
                    "model": f"models/{GEMINI_EMBEDDING_MODEL}",
                    "content": {"parts": [{"text": t[:2048]}]},
                    "taskType": task_type,
                }
                for t in batch_texts
            ]
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                embeddings = data.get("embeddings", [])

                for j, emb_data in enumerate(embeddings):
                    values = emb_data.get("values", [])
                    if values and j < len(batch_indices):
                        idx = batch_indices[j]
                        results[idx] = values
                        _embedding_cache[_content_hash(texts[idx])] = values

            logger.info(f"Embedded batch of {len(batch_texts)} texts via Gemini text-embedding-005")
        except Exception as ex:
            logger.warning(f"Gemini batch embedding call failed for batch starting at {batch_start}: {ex}")

    return results


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0

    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    return dot / (norm_a * norm_b)


def clear_embedding_cache():
    """Clear the in-memory embedding cache (useful between audit runs)."""
    _embedding_cache.clear()
