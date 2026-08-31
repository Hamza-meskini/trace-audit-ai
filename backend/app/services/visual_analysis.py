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
from app.services.llm_client import call_gemini_generate_content


logger = logging.getLogger("traceaudit.visual_analysis")


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


async def describe_retrieved_figures(
    requirement_items: list[dict[str, Any]],
    documents_by_id: dict[str, Document],
    chunk_models: dict[str, EvidenceChunk],
    *,
    model: str,
    max_concurrency: int = 3,
) -> dict[str, int]:
    """Describe unique retrieved figures once and update all candidate views."""

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
    }
    if not candidates_by_id:
        return stats

    semaphore = asyncio.Semaphore(max_concurrency)
    active_model = model if "gemini" in model.lower() else "gemini-2.5-flash"

    async def enrich(chunk_id: str, views: list[dict[str, Any]]) -> None:
        first = views[0]
        metadata = first.get("metadata") or {}
        visual = dict(metadata.get("visual_analysis") or {})
        description = str(visual.get("description") or "").strip()
        if visual.get("status") == "complete" and description:
            stats["vision_cache_hits"] += 1
        elif not settings.effective_gemini_api_key:
            visual["status"] = "unavailable"
            visual["reason"] = "No Gemini vision API key configured"
            stats["vision_unavailable"] += 1
        else:
            document = documents_by_id.get(first.get("document_id", ""))
            page_number = first.get("page_number")
            png = None
            if document is not None and page_number is not None:
                png = await asyncio.to_thread(
                    _render_figure_png,
                    document.storage_path,
                    int(page_number),
                    metadata.get("bounding_boxes") or [],
                )
            if png:
                prompt = (
                    "Describe and transcribe this technical-document figure for evidence retrieval. "
                    "Capture every visible label, number, unit, legend, axis, callout, pass/fail result, "
                    "and relationship. Do not decide regulatory compliance and do not invent obscured text. "
                    f"Caption/context: {visual.get('caption') or first.get('content', '')}"
                )
                async with semaphore:
                    description = (await call_gemini_generate_content(
                        prompt,
                        model=active_model,
                        thinking_level="LOW",
                        max_output_tokens=1400,
                        image_bytes=png,
                        image_mime_type="image/png",
                    ) or "").strip()
            if description:
                visual.update({"status": "complete", "description": description})
                stats["vision_analyzed"] += 1
            else:
                visual.setdefault("status", "unavailable")
                visual.setdefault("reason", "Figure could not be rendered or described")
                stats["vision_unavailable"] += 1

        for view in views:
            view_metadata = dict(view.get("metadata") or {})
            view_metadata["visual_analysis"] = visual
            view["metadata"] = view_metadata
            if description and "VISUAL DESCRIPTION:" not in view.get("content", ""):
                view["content"] = f"{view.get('content', '')}\nVISUAL DESCRIPTION: {description}".strip()

        model_chunk = chunk_models.get(chunk_id)
        if model_chunk is not None:
            persisted = dict(model_chunk.metadata_json or {})
            persisted["visual_analysis"] = visual
            model_chunk.metadata_json = persisted
            if description and "VISUAL DESCRIPTION:" not in model_chunk.content:
                model_chunk.content = f"{model_chunk.content}\nVISUAL DESCRIPTION: {description}".strip()

    await asyncio.gather(*(enrich(chunk_id, views) for chunk_id, views in candidates_by_id.items()))
    return stats
