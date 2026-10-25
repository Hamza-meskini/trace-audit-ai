"""On-demand visual enrichment for retrieved PDF figures.

Figures are not sent to a vision model during bulk ingestion. Only figure
chunks that retrieval actually selects are rendered and described, then the
description is cached on the persisted chunk metadata.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.config import settings
from app.models.document import Document, EvidenceChunk
from app.services.llm_client import call_vision_with_fallback


logger = logging.getLogger("traceaudit.visual_analysis")
PROMPT_VERSION = "figure-evidence-v2"


def _render_figure_png(
    file_path: str,
    page_number: int,
    bounding_boxes: list[dict[str, Any]],
) -> bytes | None:
    try:
        import pymupdf
    except ImportError:  # pragma: no cover
        import fitz as pymupdf

    matching = [
        box for box in bounding_boxes
        if int(box.get("page_number") or page_number) == page_number
    ]
    if not matching:
        return None
    box = matching[0]
    clip = pymupdf.Rect(box["x0"], box["y0"], box["x1"], box["y1"])
    with pymupdf.open(file_path) as document:
        if page_number < 1 or page_number > len(document):
            return None
        page = document[page_number - 1]
        clip = clip & page.rect
        if clip.is_empty or clip.width < 8 or clip.height < 8:
            return None
        return page.get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=clip, alpha=False).tobytes("png")


async def describe_figure_candidates(
    requirement_items: list[dict[str, Any]],
    document_paths_by_id: dict[str, str],
    *,
    model: str,
    chunk_models: dict[str, EvidenceChunk] | None = None,
    max_concurrency: int = 1,
) -> dict[str, Any]:
    """Describe retrieved figures for both production and offline evaluation."""

    candidates_by_id: dict[str, list[dict[str, Any]]] = {}
    for item in requirement_items:
        for candidate in item.get("candidate_chunks", []):
            metadata = candidate.get("metadata") or {}
            if metadata.get("block_type") == "figure":
                candidates_by_id.setdefault(candidate.get("id", ""), []).append(candidate)

    stats = {
        "retrieved_figures": len(candidates_by_id),
        "vision_cache_hits": 0,
        "vision_analyzed": 0,
        "vision_unavailable": 0,
        "vision_provider_counts": {},
    }
    if not candidates_by_id:
        return stats

    semaphore = asyncio.Semaphore(max_concurrency)
    active_model = settings.GEMINI_VISION_MODEL
    disabled_providers: set[str] = set()

    async def enrich(chunk_id: str, views: list[dict[str, Any]]) -> None:
        first = views[0]
        metadata = first.get("metadata") or {}
        visual = dict(metadata.get("visual_analysis") or {})
        description = str(visual.get("description") or "").strip()
        vision_models = [settings.DATABRICKS_VISION_MODEL, settings.OPENROUTER_VISION_MODEL, active_model, settings.GROQ_VISION_MODEL, settings.HF_VISION_MODEL]
        cache_valid = (
            visual.get("status") == "complete" and bool(description)
            and (
                visual.get("source") == "databricks-ai-parse"
                or (
                    visual.get("prompt_version") == PROMPT_VERSION
                    and visual.get("vision_models") == vision_models
                )
            )
        )
        if not cache_valid:
            description = ""
            visual.pop("description", None)
            for view in views:
                view["content"] = view.get("content", "").split("\nVISUAL DESCRIPTION:", 1)[0]
            model_chunk = (chunk_models or {}).get(chunk_id)
            if model_chunk is not None:
                model_chunk.content = model_chunk.content.split("\nVISUAL DESCRIPTION:", 1)[0]
        if cache_valid:
            stats["vision_cache_hits"] += 1
        elif not any((
            settings.DATABRICKS_VISION_MODEL and settings.effective_databricks_token and settings.effective_databricks_base_url,
            settings.effective_openrouter_api_key,
            settings.effective_gemini_api_key,
            settings.effective_groq_api_key,
            settings.effective_hf_token,
        )):
            visual["status"] = "unavailable"
            visual["reason"] = "No vision provider API key configured"
            stats["vision_unavailable"] += 1
        else:
            document_path = document_paths_by_id.get(first.get("document_id", ""))
            page_number = first.get("page_number")
            png = None
            if document_path and page_number is not None:
                png = await asyncio.to_thread(
                    _render_figure_png,
                    document_path,
                    int(page_number),
                    metadata.get("bounding_boxes") or [],
                )
            if png:
                prompt = (
                    "This image is one crop from one page of a technical document. Describe and transcribe "
                    "only what is visibly present for evidence retrieval. Capture every readable label, "
                    "number, unit, legend, axis, callout, pass/fail result, and relationship. Do not claim "
                    "there are multiple panels, views, or photographs unless visible separators clearly show "
                    "them. Do not decide regulatory compliance, infer hidden facts, invent obscured text, or "
                    "include private reasoning or <think> tags. Transcribe any test-article identifier, "
                    "execution statement, measurement scale, and test method explicitly shown. "
                    "A photograph does not itself establish that the verification method was inspection. "
                    "Do not estimate physical dimensions without an explicit readable scale. "
                    "Return concise observable facts only. "
                    f"Caption/context: {visual.get('caption') or first.get('content', '')}"
                )
                async with semaphore:
                    vision_result = await call_vision_with_fallback(
                        prompt,
                        gemini_model=active_model,
                        max_output_tokens=800,
                        image_bytes=png,
                        image_mime_type="image/png",
                        skip_providers=disabled_providers,
                    )
                    description = str(vision_result.get("text") or "").strip()
                    visual["attempted_providers"] = vision_result.get("attempted", [])
                    if description:
                        visual["provider"] = vision_result.get("provider", "")
                        visual["model"] = vision_result.get("model", "")
                        selected_provider = str(vision_result.get("provider") or "")
                        for attempted in vision_result.get("attempted", []):
                            provider = str(attempted.get("provider") or "")
                            if provider and provider != selected_provider:
                                disabled_providers.add(provider)
            if description:
                visual.pop("reason", None)
                visual.update({"status": "complete", "description": description, "prompt_version": PROMPT_VERSION, "vision_models": vision_models})
                stats["vision_analyzed"] += 1
                provider = str(visual.get("provider") or "cache")
                counts = stats["vision_provider_counts"]
                counts[provider] = counts.get(provider, 0) + 1
            else:
                visual["status"] = "unavailable"
                visual.setdefault("reason", "Figure could not be rendered or described")
                stats["vision_unavailable"] += 1

        for view in views:
            view_metadata = dict(view.get("metadata") or {})
            view_metadata["visual_analysis"] = visual
            view["metadata"] = view_metadata
            if description and "VISUAL DESCRIPTION:" not in view.get("content", ""):
                view["content"] = f"{view.get('content', '')}\nVISUAL DESCRIPTION: {description}".strip()

        model_chunk = (chunk_models or {}).get(chunk_id)
        if model_chunk is not None:
            persisted = dict(model_chunk.metadata_json or {})
            persisted["visual_analysis"] = visual
            model_chunk.metadata_json = persisted
            if description and "VISUAL DESCRIPTION:" not in model_chunk.content:
                model_chunk.content = f"{model_chunk.content}\nVISUAL DESCRIPTION: {description}".strip()

    await asyncio.gather(*(enrich(chunk_id, views) for chunk_id, views in candidates_by_id.items()))
    return stats


async def describe_retrieved_figures(
    requirement_items: list[dict[str, Any]],
    documents_by_id: dict[str, Document],
    chunk_models: dict[str, EvidenceChunk],
    *,
    model: str,
    max_concurrency: int = 1,
) -> dict[str, int]:
    """Production adapter that also persists cached visual descriptions."""
    return await describe_figure_candidates(
        requirement_items,
        {
            document_id: document.storage_path
            for document_id, document in documents_by_id.items()
        },
        model=model,
        chunk_models=chunk_models,
        max_concurrency=max_concurrency,
    )
