"""Tests for targeted requirement-stage visual recovery."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.services import requirement_visual_recovery as recovery


def figure_chunk(page=4):
    return {
        "id": "chunk-1",
        "page_number": page,
        "content": "Figure caption only",
        "metadata": {
            "block_type": "figure",
            "document_diagnostics": {
                "ocr_failed_page_numbers": [2],
                "low_text_page_numbers": [2, page],
            },
        },
    }


def test_candidate_pages_combine_figures_and_ocr_failures():
    assert recovery.candidate_requirement_pages([figure_chunk()]) == {
        2: ["low_native_text", "ocr_failed"],
        4: ["figure", "low_native_text"],
    }


def test_visual_recovery_preserves_page_provenance_and_caches(monkeypatch):
    chunks = [figure_chunk()]
    chunks[0]["metadata"]["document_diagnostics"] = {}
    monkeypatch.setattr(recovery, "_render_page_png", lambda *_: b"png")
    call = AsyncMock(return_value={
        "text": "REQ-NVA-TEST-006: The warning indicator shall illuminate red.",
        "provider": "gemini",
        "model": "models/gemini-3.6-flash",
        "attempted": [{"provider": "gemini", "model": "models/gemini-3.6-flash"}],
    })
    monkeypatch.setattr(recovery, "call_vision_with_fallback", call)

    first = asyncio.run(recovery.recover_requirement_text_from_pages(
        file_path="unused.pdf", chunks=chunks, model="system.ai.llama-4-maverick"
    ))
    assert "SOURCE PAGE 4" in first["text"]
    assert "REQ-NVA-TEST-006" in first["text"]
    assert first["recovered_pages"] == 1
    assert chunks[0]["metadata"]["requirement_visual_recovery"]["status"] == "complete"

    call.reset_mock()
    second = asyncio.run(recovery.recover_requirement_text_from_pages(
        file_path="unused.pdf", chunks=chunks, model="system.ai.llama-4-maverick"
    ))
    assert second["cache_hits"] == 1
    assert "REQ-NVA-TEST-006" in second["text"]
    call.assert_not_awaited()


def test_no_missing_requirements_is_cached_without_inventing_text(monkeypatch):
    chunks = [figure_chunk(page=3)]
    monkeypatch.setattr(recovery, "_render_page_png", lambda *_: b"png")
    monkeypatch.setattr(
        recovery,
        "call_vision_with_fallback",
        AsyncMock(return_value={"text": "NO_MISSING_REQUIREMENTS", "provider": "groq", "model": "vision", "attempted": []}),
    )
    result = asyncio.run(recovery.recover_requirement_text_from_pages(
        file_path="unused.pdf", chunks=chunks, model="text-model"
    ))
    assert result["text"] == ""
    assert result["recovered_pages"] == 0
    assert result["analyzed_pages"] == 2


def test_low_text_page_requests_full_transcription(monkeypatch):
    chunks = [figure_chunk(page=2)]
    monkeypatch.setattr(recovery, "_render_page_png", lambda *_: b"png")
    call = AsyncMock(return_value={
        "text": "NO_VISIBLE_REQUIREMENTS",
        "provider": "groq",
        "model": "vision",
        "attempted": [],
    })
    monkeypatch.setattr(recovery, "call_vision_with_fallback", call)

    result = asyncio.run(recovery.recover_requirement_text_from_pages(
        file_path="unused.pdf", chunks=chunks, model="text-model"
    ))

    prompt = call.await_args.args[0]
    assert "Transcribe every visible" in prompt
    assert "Do not use the\nmachine-extracted text to decide that nothing is missing" in prompt
    assert result["text"] == ""
