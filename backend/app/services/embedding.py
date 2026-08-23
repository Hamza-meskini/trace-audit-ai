"""Lexical (BM25) tokenization and scoring utilities for evidence retrieval."""

import re


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

