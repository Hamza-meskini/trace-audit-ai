"""Databricks reasoning controls must survive routing without affecting Maverick."""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.config import settings
from app.services import llm_client


@pytest.mark.parametrize("model,effort,expected,timeout", [
    ("system.ai.gpt-oss-120b", "HIGH", "high", 300),
    ("system.ai.gpt-oss-120b", None, "medium", 300),
    ("system.ai.gpt-oss-20b", "LOW", "low", 300),
    ("system.ai.llama-4-maverick", "HIGH", None, 90),
])
def test_request_controls(monkeypatch, model, effort, expected, timeout):
    monkeypatch.setattr(settings, "DATABRICKS_TOKEN", "test-key")
    monkeypatch.setattr(settings, "DATABRICKS_BASE_URL", "https://example.test/v1")
    monkeypatch.setattr(settings, "DATABRICKS_REASONING_TIMEOUT_SECONDS", 300)
    response = MagicMock(status_code=200)
    response.json.return_value = {"choices": [{"message": {"content": '{"value":"ok"}'}}]}
    client = AsyncMock()
    client.post.return_value = response
    context = AsyncMock()
    context.__aenter__.return_value = client
    factory = MagicMock(return_value=context)
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", factory)
    result = asyncio.run(llm_client.call_databricks_chat_completions(
        "Extract", model=model, thinking_level=effort, json_mode=True))
    assert result == '{"value":"ok"}'
    payload = client.post.call_args.kwargs["json"]
    assert payload.get("reasoning_effort") == expected
    factory.assert_called_once_with(timeout=timeout)


@pytest.mark.parametrize("structured", [True, False])
def test_routing_forwards_effort(monkeypatch, structured):
    class Result(BaseModel):
        value: str
    monkeypatch.setattr(settings, "LLM_PROVIDER", "databricks")
    monkeypatch.setattr(settings, "DATABRICKS_TOKEN", "test-key")
    adapter = AsyncMock(return_value='{"value":"ok"}')
    monkeypatch.setattr(llm_client, "call_databricks_chat_completions", adapter)
    kwargs = dict(prompt="Extract", model="system.ai.gpt-oss-120b", thinking_level="HIGH")
    if structured:
        asyncio.run(llm_client.generate_structured(**kwargs, response_model=Result, allow_model_fallback=False))
    else:
        asyncio.run(llm_client.generate_text(**kwargs))
    assert adapter.await_args.kwargs["thinking_level"] == "HIGH"
