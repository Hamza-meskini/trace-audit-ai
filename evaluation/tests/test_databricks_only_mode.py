"""LLM_DATABRICKS_ONLY keeps every LLM stage on Databricks (Maverick by default)."""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.config import Settings, settings
from app.services import llm_client

MAVERICK = "system.ai.llama-4-maverick"


class Example(BaseModel):
    value: str


@pytest.fixture(autouse=True)
def databricks_only(monkeypatch):
    monkeypatch.setattr(settings, "LLM_DATABRICKS_ONLY", True)
    monkeypatch.setattr(settings, "DATABRICKS_TOKEN", "test")
    monkeypatch.setattr(settings, "DATABRICKS_BASE_URL", "https://example.test/v1")
    monkeypatch.setattr(settings, "DATABRICKS_VISION_MODEL", MAVERICK)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "databricks")
    monkeypatch.setattr(settings, "LLM_MODEL", MAVERICK)
    for key in ("OPENROUTER_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "HF_TOKEN", "TOKENROUTER_API_KEY"):
        monkeypatch.setattr(settings, key, "configured")


def test_defaults_put_every_llm_stage_on_maverick():
    defaults = Settings.model_fields
    assert defaults["LLM_DATABRICKS_ONLY"].default is True
    for name in ("LLM_MODEL", "DATABRICKS_MODEL", "DATABRICKS_VISION_MODEL",
                 "ATOMIC_DECOMPOSITION_MODEL", "ATOMIC_DECOMPOSITION_FALLBACK_MODEL"):
        assert defaults[name].default == MAVERICK, name


def test_vision_never_leaves_databricks(monkeypatch):
    databricks = AsyncMock(return_value=None)
    third_party = AsyncMock(return_value={"text": "should not run", "model": "x"})
    monkeypatch.setattr(llm_client, "call_databricks_chat_completions", databricks)
    monkeypatch.setattr(llm_client, "call_openrouter_vision", third_party)
    monkeypatch.setattr(llm_client, "call_gemini_generate_content", third_party)
    monkeypatch.setattr(llm_client, "call_groq_vision", third_party)
    monkeypatch.setattr(llm_client, "call_huggingface_vision", third_party)

    result = asyncio.run(llm_client.call_vision_with_fallback("read", image_bytes=b"png"))

    assert result["text"] == ""
    assert [item["provider"] for item in result["attempted"]] == ["databricks"]
    third_party.assert_not_awaited()
    assert llm_client.vision_model_chain("models/gemini-x") == [MAVERICK]


@pytest.mark.parametrize("model", ["z-ai/glm-5.3-free", "gemini-3.8-flash", "qwen3.8-flash"])
def test_non_databricks_models_are_refused(model, monkeypatch):
    tokenrouter = AsyncMock(return_value='{"value": "x"}')
    monkeypatch.setattr(llm_client, "call_tokenrouter_chat_completions", tokenrouter)
    with pytest.raises(ValueError, match="LLM_DATABRICKS_ONLY"):
        asyncio.run(llm_client.generate_structured("p", Example, model=model))
    with pytest.raises(ValueError, match="LLM_DATABRICKS_ONLY"):
        asyncio.run(llm_client.generate_text("p", model=model))
    tokenrouter.assert_not_awaited()


def test_failed_databricks_call_does_not_cascade(monkeypatch):
    databricks = AsyncMock(return_value=None)
    tokenrouter = AsyncMock(return_value="should not run")
    openai = AsyncMock(return_value="should not run")
    monkeypatch.setattr(llm_client, "call_databricks_chat_completions", databricks)
    monkeypatch.setattr(llm_client, "call_tokenrouter_chat_completions", tokenrouter)
    monkeypatch.setattr(llm_client, "call_openai_chat_completions", openai)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "configured")

    assert asyncio.run(llm_client.generate_structured("p", Example, model="system.ai.gpt-oss-120b")) is None
    assert asyncio.run(llm_client.generate_text("p", model="system.ai.gpt-oss-120b")) is None
    tokenrouter.assert_not_awaited()
    openai.assert_not_awaited()


def test_maverick_structured_call_succeeds(monkeypatch):
    databricks = AsyncMock(return_value='{"value": "ok"}')
    monkeypatch.setattr(llm_client, "call_databricks_chat_completions", databricks)
    result = asyncio.run(llm_client.generate_structured("p", Example))
    assert result.value == "ok"
    assert databricks.await_args.kwargs["model"] == MAVERICK
