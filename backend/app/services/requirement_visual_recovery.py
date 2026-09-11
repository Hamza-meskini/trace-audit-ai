"""Targeted visual recovery before requirement extraction.

Only candidate pages from specification documents are rendered. The vision
model transcribes obligation-bearing content that is absent from the parser's
text; downstream requirement extraction remains the canonical structuring step.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

from app.models.document import EvidenceChunk
from app.config import settings
from app.services.llm_client import call_vision_with_fallback


logger = logging.getLogger("traceaudit.requirement_visual_recovery")
PROMPT_VERSION = "requirement-recovery-v3"


def _valid_transcription(text: str) -> bool:
    if not text or text.startswith(("{", "[")):
        return False
    if text.upper().rstrip(".") in {"NO_MISSING_REQUIREMENTS", "NO_VISIBLE_REQUIREMENTS"}:
        return True
    return len(text.split()) >= 4 and text.lower().strip(" .") not in {"no issues found"}


def _render_page_png(file_path: str, page_number: int) -> bytes | None:
    try:
        import pymupdf
    except ImportError:  # pragma: no cover
        import fitz as pymupdf
    with pymupdf.open(file_path) as document:
        if page_number < 1 or page_number > document.page_count:
            return None
        page = document[page_number - 1]
        return page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False).tobytes("png")


def candidate_requirement_pages(chunks: list[dict[str, Any]]) -> dict[int, list[str]]:
    """Return candidate page numbers and auditable reasons."""
    reasons: dict[int, set[str]] = defaultdict(set)
    diagnostics = {}
    for chunk in chunks:
        metadata = chunk.get("metadata") or {}
        diagnostics = metadata.get("document_diagnostics") or diagnostics
        page = chunk.get("page_number")
        if page and metadata.get("block_type") == "figure":
            reasons[int(page)].add("figure")
    for page in diagnostics.get("ocr_failed_page_numbers") or []:
        reasons[int(page)].add("ocr_failed")
    for page in diagnostics.get("low_text_page_numbers") or []:
        reasons[int(page)].add("low_native_text")
    return {page: sorted(values) for page, values in sorted(reasons.items())}


def _cached_recovery(page_chunks: list[dict[str, Any]]) -> dict[str, Any] | None:
    for chunk in page_chunks:
        cached = (chunk.get("metadata") or {}).get("requirement_visual_recovery")
        if (cached and cached.get("prompt_version") == PROMPT_VERSION
                and cached.get("vision_models") == _vision_models()
                and cached.get("status") == "complete"):
            return dict(cached)
    return None


def _vision_models() -> list[str]:
    return [settings.DATABRICKS_VISION_MODEL, settings.OPENROUTER_VISION_MODEL, settings.GEMINI_VISION_MODEL,
            settings.GROQ_VISION_MODEL, settings.HF_VISION_MODEL]


async def recover_requirement_text_from_pages(
    *,
    file_path: str,
    chunks: list[dict[str, Any]],
    model: str,
    chunk_models: dict[str, EvidenceChunk] | None = None,
    max_pages: int = 12,
    max_concurrency: int = 1,
) -> dict[str, Any]:
    """Recover missing requirement prose and attach cached page-level metadata."""
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for chunk in chunks:
        if chunk.get("page_number") is not None:
            grouped[int(chunk["page_number"])].append(chunk)
    candidates = candidate_requirement_pages(chunks)
    selected = list(candidates.items())[:max_pages]
    semaphore = asyncio.Semaphore(max_concurrency)
    active_vision_model = settings.GEMINI_VISION_MODEL
    disabled_providers: set[str] = set()
    recovered_sections: list[tuple[int, str]] = []
    page_results: list[dict[str, Any]] = []

    async def persist(page_chunks: list[dict[str, Any]], value: dict[str, Any]) -> None:
        for chunk in page_chunks:
            metadata = dict(chunk.get("metadata") or {})
            metadata["requirement_visual_recovery"] = value
            chunk["metadata"] = metadata
            model_chunk = (chunk_models or {}).get(str(chunk.get("id") or ""))
            if model_chunk is not None:
                persisted = dict(model_chunk.metadata_json or {})
                persisted["requirement_visual_recovery"] = value
                model_chunk.metadata_json = persisted

    async def process(page: int, reasons: list[str]) -> None:
        page_chunks = grouped.get(page, [])
        cached = _cached_recovery(page_chunks)
        if cached is not None:
            recovered = str(cached.get("recovered_text") or "").strip()
            if recovered:
                recovered_sections.append((page, recovered))
            page_results.append({**cached, "page": page, "reasons": reasons, "status": "cache_hit"})
            return

        png = await asyncio.to_thread(_render_page_png, file_path, page)
        if not png:
            value = {"prompt_version": PROMPT_VERSION, "status": "unavailable", "reason": "page_render_failed", "recovered_text": ""}
            await persist(page_chunks, value)
            page_results.append({"page": page, "reasons": reasons, **value})
            return
        existing = "\n".join(chunk.get("content", "") for chunk in page_chunks).strip()
        full_transcription_required = bool(
            {"ocr_failed", "low_native_text"}.intersection(reasons)
        )
        if full_transcription_required:
            task = """The machine extraction for this page is missing or unreliable. Transcribe every visible
normative requirement on the complete page, including its identifier and complete obligation. Do not use the
machine-extracted text to decide that nothing is missing."""
            empty_response = "If no normative requirement is visibly present, return exactly NO_VISIBLE_REQUIREMENTS."
        else:
            task = """Recover only normative requirements, obligations, thresholds, conditions, exceptions,
sequence rules, or requirement identifiers that are visible in the image but missing or materially incomplete
in the machine-extracted text."""
            empty_response = "If nothing is missing, return exactly NO_MISSING_REQUIREMENTS."
        prompt = f"""You are checking one page of a controlled engineering specification before requirement extraction.

Machine-extracted text already available from page {page}:
---
{existing[:6000] or '[no usable text]'}
---

Inspect the complete page image. {task} This includes text inside diagrams, callouts, scanned regions, labels,
and table images. Preserve identifiers, numbers, units, negation, and words such as shall/must exactly enough for
later contract extraction. Do not decide compliance, infer test results, create atomic condition labels, or add
requirements that are not visibly present.

{empty_response} Otherwise return only the recovered source prose, one requirement per paragraph, prefixed by
its visible source identifier when one exists."""
        async with semaphore:
            response = await call_vision_with_fallback(
                prompt,
                gemini_model=active_vision_model,
                max_output_tokens=1400,
                image_bytes=png,
                image_mime_type="image/png",
                skip_providers=disabled_providers,
            )
        text = str(response.get("text") or "").strip()
        # Moderation labels/JSON are not source prose. Retry another provider
        # rather than caching a nonempty but unusable transcription.
        invalid = bool(text) and not _valid_transcription(text)
        if invalid:
            rejected_provider = str(response.get("provider") or "")
            rejected_attempts = list(response.get("attempted", []))
            if rejected_provider:
                async with semaphore:
                    response = await call_vision_with_fallback(
                        prompt, gemini_model=active_vision_model, max_output_tokens=1400,
                        image_bytes=png, image_mime_type="image/png",
                        skip_providers=disabled_providers | {rejected_provider},
                    )
                response["attempted"] = rejected_attempts + list(response.get("attempted", []))
                text = str(response.get("text") or "").strip()
            else:
                text = ""
        if not _valid_transcription(text):
            text = ""
        attempted = response.get("attempted", [])
        selected_provider = str(response.get("provider") or "")
        for attempt in attempted:
            provider = str(attempt.get("provider") or "")
            if provider and provider != selected_provider:
                disabled_providers.add(provider)
        no_content_markers = {"NO_MISSING_REQUIREMENTS", "NO_VISIBLE_REQUIREMENTS"}
        recovered = "" if text.upper().replace(".", "") in no_content_markers else text
        status = "complete" if text else "unavailable"
        value = {
            "prompt_version": PROMPT_VERSION,
            "status": status,
            "provider": selected_provider,
            "vision_models": _vision_models(),
            "model": response.get("model", ""),
            "attempted_providers": attempted,
            "recovered_text": recovered,
        }
        if not text:
            value["reason"] = "vision_provider_returned_no_text"
        await persist(page_chunks, value)
        if recovered:
            recovered_sections.append((page, recovered))
        page_results.append({"page": page, "reasons": reasons, **value})

    await asyncio.gather(*(process(page, reasons) for page, reasons in selected))
    recovered_sections.sort(key=lambda item: item[0])
    page_results.sort(key=lambda item: item["page"])
    augmented = "\n\n".join(
        f"SECTION: VISUAL REQUIREMENT RECOVERY - SOURCE PAGE {page}\n{text}"
        for page, text in recovered_sections
    )
    return {
        "text": augmented,
        "candidate_pages": len(selected),
        "analyzed_pages": sum(item.get("status") == "complete" for item in page_results),
        "cache_hits": sum(item.get("status") == "cache_hit" for item in page_results),
        "unavailable_pages": sum(item.get("status") == "unavailable" for item in page_results),
        "recovered_pages": sum(bool(item.get("recovered_text")) for item in page_results),
        "pages": page_results,
    }
