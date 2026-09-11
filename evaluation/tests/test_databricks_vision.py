"""Image payload, provider priority, and fallback without network access."""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.config import settings
from app.services import llm_client


def configure(monkeypatch):
    monkeypatch.setattr(settings, "DATABRICKS_TOKEN", "test")
    monkeypatch.setattr(settings, "DATABRICKS_BASE_URL", "https://example.test/v1")
    monkeypatch.setattr(settings, "DATABRICKS_VISION_MODEL", "system.ai.llama-4-maverick")
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "test")


def test_image_payload(monkeypatch):
    configure(monkeypatch)
    response = MagicMock(status_code=200)
    response.json.return_value = {"choices": [{"message": {"content": "visible text"}}]}
    client = AsyncMock()
    client.post.return_value = response
    context = AsyncMock()
    context.__aenter__.return_value = client
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", lambda **kwargs: context)
    assert asyncio.run(llm_client.call_databricks_chat_completions("read", image_bytes=b"png")) == "visible text"
    payload = client.post.await_args.kwargs["json"]
    assert payload["messages"][0]["content"] == [
        {"type": "text", "text": "read"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,cG5n"}},
    ]


def test_databricks_first_then_fallback_and_skip(monkeypatch):
    configure(monkeypatch)
    databricks = AsyncMock(return_value="Maverick transcription")
    fallback = AsyncMock(return_value={"text": "fallback transcription", "model": "vision"})
    monkeypatch.setattr(llm_client, "call_databricks_chat_completions", databricks)
    monkeypatch.setattr(llm_client, "call_openrouter_vision", fallback)
    result = asyncio.run(llm_client.call_vision_with_fallback("read", image_bytes=b"png"))
    assert result["provider"] == "databricks"
    assert databricks.await_args.kwargs["image_bytes"] == b"png"
    fallback.assert_not_awaited()
    databricks.return_value = None
    result = asyncio.run(llm_client.call_vision_with_fallback("read", image_bytes=b"png"))
    assert result["provider"] == "openrouter"
    assert [x["provider"] for x in result["attempted"]] == ["databricks", "openrouter"]
    databricks.reset_mock()
    result = asyncio.run(llm_client.call_vision_with_fallback("read", image_bytes=b"png", skip_providers={"databricks"}))
    databricks.assert_not_awaited()
