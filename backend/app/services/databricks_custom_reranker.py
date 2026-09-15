"""Databricks Model Serving adapter for the BGE cross-encoder reranker."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from typing import Any

import httpx

from app.config import settings
from app.services.databricks_document_ai import _workspace_hostname
from app.services.observability import trace_span
from app.services.retrieval import RetrievedChunk, _chunk_key, _condition_aware_rerank


logger = logging.getLogger("traceaudit.databricks_custom_reranker")
ENDPOINT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _endpoint_name() -> str:
    value = str(settings.DATABRICKS_CUSTOM_RERANKER_ENDPOINT or "").strip()
    if not ENDPOINT_RE.fullmatch(value):
        raise RuntimeError("DATABRICKS_CUSTOM_RERANKER_ENDPOINT is invalid")
    return value


def _invoke_reranker(queries: list[str], documents: list[str]) -> list[list[float]]:
    endpoint = _endpoint_name()
    token = settings.effective_databricks_token
    if not token:
        raise RuntimeError("DATABRICKS_TOKEN is required for the custom reranker")
    url = f"https://{_workspace_hostname()}/serving-endpoints/{endpoint}/invocations"
    batch_size = max(1, int(settings.DATABRICKS_CUSTOM_RERANKER_BATCH_SIZE))
    budget = max(1.0, float(settings.DATABRICKS_CUSTOM_RERANKER_TIMEOUT_SECONDS))
    deadline = time.monotonic() + budget
    with trace_span(
        "databricks_custom_reranker.score",
        span_type="RERANKER",
        inputs={"queries": len(queries), "documents": len(documents)},
        attributes={"endpoint": endpoint},
    ) as span:
        score_rows: list[list[float]] = []
        # Keep every candidate and query; split only transport work, preserving
        # the exact score matrix used by ranking. Never truncate document text.
        for query in queries:
            scores: list[float] = []
            for offset in range(0, len(documents), batch_size):
                batch = documents[offset:offset + batch_size]
                payload = {"dataframe_split": {
                    "columns": ["query", "documents"],
                    "data": [[query, json.dumps(batch, ensure_ascii=False)]],
                }}
                for attempt in range(2):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError(f"Custom reranker {endpoint} exceeded its {budget:g}s request budget")
                    try:
                        response = httpx.post(
                            url,
                            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                            json=payload,
                            timeout=min(60.0, remaining),
                        )
                        response.raise_for_status()
                        break
                    except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                        retryable = isinstance(exc, httpx.TransportError) or exc.response.status_code in {429, 502, 503, 504}
                        if not retryable:
                            raise
                        if attempt == 1 or deadline - time.monotonic() <= 2:
                            raise RuntimeError(
                                f"Custom reranker {endpoint} unavailable ({type(exc).__name__}) "
                                f"while scoring documents {offset + 1}-{offset + len(batch)}; "
                                "no reranker scores were applied. Check endpoint health before retrying."
                            ) from exc
                        logger.warning("Reranker %s: %s on a %s-document batch; retrying once.", endpoint, type(exc).__name__, len(batch))
                        time.sleep(2)
                body = response.json()
                predictions = body.get("predictions") if isinstance(body, dict) else None
                if not isinstance(predictions, list) or len(predictions) != 1:
                    raise RuntimeError("Custom reranker returned an unexpected prediction count")
                raw_scores = predictions[0].get("scores") if isinstance(predictions[0], dict) else None
                if not isinstance(raw_scores, list) or len(raw_scores) != len(batch):
                    raise RuntimeError("Custom reranker returned scores that do not align with documents")
                values = [float(score) for score in raw_scores]
                if not all(math.isfinite(value) for value in values):
                    raise RuntimeError("Custom reranker returned non-finite scores")
                scores.extend(values)
            score_rows.append(scores)
        if span is not None:
            span.set_outputs({"score_rows": len(score_rows), "scores_per_row": len(documents)})
        return score_rows


async def rerank_candidates(
    requirement_text: str,
    candidates: list[RetrievedChunk],
    *,
    condition_queries: list[str] | None = None,
    top_k: int = 8,
    strict: bool = False,
) -> tuple[list[RetrievedChunk], dict[str, Any]]:
    """Rerank a bounded candidate set while preserving source traceability."""
    enabled = bool(settings.DATABRICKS_CUSTOM_RERANKER_ENABLED)
    if not enabled or not candidates:
        return candidates[:top_k], {
            "enabled": enabled,
            "used": False,
            "endpoint": _endpoint_name() if enabled else "",
            "candidates": len(candidates),
        }

    candidate_limit = max(top_k, int(settings.DATABRICKS_CUSTOM_RERANKER_CANDIDATE_COUNT))
    bounded = candidates[:candidate_limit]
    queries = list(dict.fromkeys(
        query.strip()
        for query in (requirement_text, *(condition_queries or []))
        if query and query.strip()
    ))[:max(1, int(settings.DATABRICKS_CUSTOM_RERANKER_MAX_QUERIES))]
    documents = [item.content for item in bounded]
    started = time.perf_counter()
    try:
        score_rows = await asyncio.to_thread(_invoke_reranker, queries, documents)
    except Exception as exc:
        if strict:
            raise
        logger.warning("Custom Databricks reranker failed; retaining retrieval order: %s", exc)
        return bounded[:top_k], {
            "enabled": True,
            "used": False,
            "endpoint": _endpoint_name(),
            "candidates": len(bounded),
            "seconds": round(time.perf_counter() - started, 3),
            "fallback_reason": str(exc),
        }

    max_retrieval = max((abs(item.score) for item in bounded), default=1.0) or 1.0
    rankings: list[list[str]] = []
    for query_index, raw_scores in enumerate(score_rows):
        rankings.append([
            _chunk_key(bounded[index])
            for index in sorted(range(len(bounded)), key=lambda index: raw_scores[index], reverse=True)
        ])
        for index, raw_score in enumerate(raw_scores):
            metadata = dict(bounded[index].metadata or {})
            by_query = dict(metadata.get("custom_reranker_scores") or {})
            by_query[str(query_index)] = raw_score
            metadata["custom_reranker_scores"] = by_query
            bounded[index].metadata = metadata

    # A passage can be important because it matches the whole clause or one
    # atomic predicate. Use its strongest cross-encoder score, blended with a
    # small amount of the original retrieval signal for stable tie-breaking.
    for index, item in enumerate(bounded):
        raw_score = max(row[index] for row in score_rows)
        probability = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, raw_score))))
        retrieval_signal = max(0.0, item.score / max_retrieval)
        item.score = 0.9 * probability + 0.1 * retrieval_signal
        metadata = dict(item.metadata or {})
        metadata.update({
            "reranker_backend": "databricks-custom-bge",
            "custom_reranker_score": raw_score,
            "custom_reranker_probability": probability,
        })
        item.metadata = metadata

    selected = _condition_aware_rerank(bounded, rankings[1:], top_k)
    return selected, {
        "enabled": True,
        "used": True,
        "endpoint": _endpoint_name(),
        "queries": len(queries),
        "candidates": len(bounded),
        "batch_size": max(1, int(settings.DATABRICKS_CUSTOM_RERANKER_BATCH_SIZE)),
        "seconds": round(time.perf_counter() - started, 3),
    }
