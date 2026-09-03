"""Vision-provider cascade and response parsing regressions."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.config import settings
from app.services import llm_client


def test_openai_message_text_accepts_string_and_part_lists():
    assert llm_client._openai_message_text({
        "choices": [{"message": {"content": "figure text"}}]
    }) == "figure text"
    assert llm_client._openai_message_text({
        "choices": [{"message": {"content": [{"type": "text", "text": "table 4"}]}}]
    }) == "table 4"


def test_reasoning_tags_are_removed_from_persisted_visual_description():
    assert llm_client._strip_reasoning_tags(
        "<think>internal chain of thought</think>Visible label: 48 V"
    ) == "Visible label: 48 V"


def test_vision_cascade_falls_back_from_gemini_to_groq(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "configured")
    monkeypatch.setattr(settings, "GROQ_API_KEY", "configured")
    monkeypatch.setattr(settings, "HF_TOKEN", "configured")
    gemini = AsyncMock(return_value=None)
    groq = AsyncMock(return_value="Groq transcribed the technical plot")
    huggingface = AsyncMock(return_value="should not run")
    monkeypatch.setattr(llm_client, "call_gemini_generate_content", gemini)
    monkeypatch.setattr(llm_client, "call_groq_vision", groq)
    monkeypatch.setattr(llm_client, "call_huggingface_vision", huggingface)

    result = asyncio.run(llm_client.call_vision_with_fallback(
        "transcribe",
        image_bytes=b"png",
    ))

    assert result["provider"] == "groq"
    assert result["model"] == settings.GROQ_VISION_MODEL
    assert result["text"] == "Groq transcribed the technical plot"
    assert [item["provider"] for item in result["attempted"]] == ["gemini", "groq"]
    huggingface.assert_not_awaited()


def test_vision_cascade_reaches_huggingface(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "configured")
    monkeypatch.setattr(settings, "GROQ_API_KEY", "configured")
    monkeypatch.setattr(settings, "HF_TOKEN", "configured")
    monkeypatch.setattr(llm_client, "call_gemini_generate_content", AsyncMock(return_value=None))
    monkeypatch.setattr(llm_client, "call_groq_vision", AsyncMock(return_value=None))
    monkeypatch.setattr(
        llm_client,
        "call_huggingface_vision",
        AsyncMock(return_value="Hugging Face transcribed the figure"),
    )

    result = asyncio.run(llm_client.call_vision_with_fallback(
        "transcribe",
        image_bytes=b"png",
    ))

    assert result["provider"] == "huggingface"
    assert [item["provider"] for item in result["attempted"]] == [
        "gemini",
        "groq",
        "huggingface",
    ]


def test_vision_cascade_skips_open_circuit_provider(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "configured")
    monkeypatch.setattr(settings, "GROQ_API_KEY", "configured")
    gemini = AsyncMock(return_value="should not run")
    groq = AsyncMock(return_value="Groq description")
    monkeypatch.setattr(llm_client, "call_gemini_generate_content", gemini)
    monkeypatch.setattr(llm_client, "call_groq_vision", groq)

    result = asyncio.run(llm_client.call_vision_with_fallback(
        "transcribe",
        image_bytes=b"png",
        skip_providers={"gemini"},
    ))

    assert result["provider"] == "groq"
    gemini.assert_not_awaited()
