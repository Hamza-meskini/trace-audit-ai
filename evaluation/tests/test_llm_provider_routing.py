"""Provider-routing regressions for structured LLM extraction."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.config import settings
from app.services import llm_client


class ExampleResult(BaseModel):
    value: str


def test_explicit_groq_provider_routes_qwen_to_groq(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test-key")
    groq = AsyncMock(return_value='{"value":"ok"}')
    databricks = AsyncMock(return_value=None)
    monkeypatch.setattr(llm_client, "call_groq_chat_completions", groq)
    monkeypatch.setattr(llm_client, "call_databricks_chat_completions", databricks)

    result = asyncio.run(llm_client.generate_structured(
        prompt="Extract a value.",
        response_model=ExampleResult,
        model="qwen/qwen3.8-27b",
        thinking_level="medium",
        allow_model_fallback=False,
    ))

    assert result == ExampleResult(value="ok")
    groq.assert_awaited_once()
    assert groq.await_args.kwargs["model"] == "qwen/qwen3.8-27b"
    assert groq.await_args.kwargs["reasoning_effort"] == "medium"
    assert groq.await_args.kwargs["response_schema"]["type"] == "object"
    databricks.assert_not_awaited()


def test_explicit_tokenrouter_provider_routes_to_tokenrouter(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "tokenrouter")
    monkeypatch.setattr(settings, "TOKENROUTER_API_KEY", "test-tokenrouter-key")
    tokenrouter = AsyncMock(return_value='{"value":"glm_ok"}')
    databricks = AsyncMock(return_value=None)
    gemini = AsyncMock(return_value=None)

    monkeypatch.setattr(llm_client, "call_tokenrouter_chat_completions", tokenrouter)
    monkeypatch.setattr(llm_client, "call_databricks_chat_completions", databricks)
    monkeypatch.setattr(llm_client, "call_gemini_generate_content", gemini)

    result = asyncio.run(llm_client.generate_structured(
        prompt="Extract a value.",
        response_model=ExampleResult,
        model="z-ai/glm-5.3-free",
        allow_model_fallback=False,
    ))

    assert result == ExampleResult(value="glm_ok")
    tokenrouter.assert_awaited_once()
    assert tokenrouter.await_args.kwargs["model"] == "z-ai/glm-5.3-free"
    databricks.assert_not_awaited()
    gemini.assert_not_awaited()


def test_glm_model_auto_infers_tokenrouter(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "")
    monkeypatch.setattr(settings, "TOKENROUTER_API_KEY", "test-tokenrouter-key")
    tokenrouter = AsyncMock(return_value='{"value":"inferred_ok"}')
    monkeypatch.setattr(llm_client, "call_tokenrouter_chat_completions", tokenrouter)

    result = asyncio.run(llm_client.generate_structured(
        prompt="Extract a value.",
        response_model=ExampleResult,
        model="z-ai/glm-5.3-free",
        allow_model_fallback=False,
    ))

    assert result == ExampleResult(value="inferred_ok")
    tokenrouter.assert_awaited_once()


def test_dashscope_qwen_model_routes_to_responses_adapter(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "databricks")
    monkeypatch.setattr(settings, "DASHSCOPE_API_KEY", "test-dashscope-key")
    dashscope = AsyncMock(return_value='{"value":"qwen_ok"}')
    databricks = AsyncMock(return_value=None)
    monkeypatch.setattr(llm_client, "call_dashscope_responses", dashscope)
    monkeypatch.setattr(llm_client, "call_databricks_chat_completions", databricks)

    result = asyncio.run(llm_client.generate_structured(
        prompt="Extract a value.",
        response_model=ExampleResult,
        model="qwen3.8-flash",
        thinking_level="medium",
        allow_model_fallback=False,
    ))

    assert result == ExampleResult(value="qwen_ok")
    dashscope.assert_awaited_once()
    assert dashscope.await_args.kwargs["model"] == "qwen3.8-flash"
    assert dashscope.await_args.kwargs["thinking_level"] == "medium"
    databricks.assert_not_awaited()


def test_dashscope_qwen_never_routes_to_databricks_when_key_is_missing(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "databricks")
    monkeypatch.setattr(settings, "DASHSCOPE_API_KEY", "")
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    databricks = AsyncMock(return_value='{"value":"wrong-provider"}')
    monkeypatch.setattr(llm_client, "call_databricks_chat_completions", databricks)

    try:
        asyncio.run(llm_client.generate_structured(
            prompt="Extract a value.",
            response_model=ExampleResult,
            model="qwen3.8-flash",
            allow_model_fallback=True,
        ))
    except RuntimeError as exc:
        assert "DASHSCOPE_API_KEY" in str(exc)
    else:
        raise AssertionError("Expected missing DashScope credentials to fail explicitly")

    databricks.assert_not_awaited()


def test_dashscope_qwen_text_failure_does_not_fallback_to_databricks(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "databricks")
    monkeypatch.setattr(settings, "DASHSCOPE_API_KEY", "test-dashscope-key")
    dashscope = AsyncMock(return_value=None)
    databricks = AsyncMock(return_value="wrong-provider")
    monkeypatch.setattr(llm_client, "call_dashscope_responses", dashscope)
    monkeypatch.setattr(llm_client, "call_databricks_chat_completions", databricks)

    result = asyncio.run(llm_client.generate_text(
        prompt="Answer.",
        model="qwen3.8-flash",
        thinking_level="medium",
    ))

    assert result is None
    dashscope.assert_awaited_once()
    databricks.assert_not_awaited()


def test_dashscope_response_parser_ignores_reasoning_and_reads_final_message():
    payload = {
        "output": [
            {"type": "reasoning", "summary": [{"text": "hidden reasoning"}]},
            {
                "type": "message",
                "content": [{"type": "output_text", "text": '{"value":"ok"}'}],
            },
        ],
    }

    assert llm_client._dashscope_response_text(payload) == '{"value":"ok"}'


def test_gemini_three_uses_native_schema_and_no_temperature(monkeypatch):
    monkeypatch.setattr(llm_client.settings, "GEMINI_API_KEY", "test-gemini-key")
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": '{"value":"ok"}'}]}}]
    }
    client = AsyncMock()
    client.post.return_value = response
    context = AsyncMock()
    context.__aenter__.return_value = client
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", lambda **kwargs: context)

    raw = asyncio.run(llm_client.call_gemini_generate_content(
        prompt="Extract a value.",
        model="gemini-3.8-flash",
        json_mode=True,
        response_schema=ExampleResult.model_json_schema(),
        thinking_level="MEDIUM",
    ))

    assert raw == '{"value":"ok"}'
    generation_config = client.post.await_args.kwargs["json"]["generationConfig"]
    assert "temperature" not in generation_config
    assert generation_config["thinkingConfig"] == {"thinkingLevel": "medium"}
    assert generation_config["responseMimeType"] == "application/json"
    assert "responseJsonSchema" in generation_config
    assert "pattern" not in str(generation_config["responseJsonSchema"])


def test_glm_uses_streaming_without_unsupported_json_or_thinking_fields(monkeypatch):
    monkeypatch.setattr(settings, "TOKENROUTER_API_KEY", "test-tokenrouter-key")
    captured = {}

    class StreamResponse:
        status_code = 200

        async def aiter_lines(self):
            yield 'data: {"choices":[{"delta":{"content":"{\\"value\\":"}}]}'
            yield 'data: {"choices":[{"delta":{"content":"\\"ok\\"}"}}]}'
            yield "data: [DONE]"

        async def aread(self):
            return b""

    class StreamContext:
        async def __aenter__(self):
            return StreamResponse()

        async def __aexit__(self, *_):
            return False

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        def stream(self, method, url, **kwargs):
            captured.update({"method": method, "url": url, **kwargs})
            return StreamContext()

    monkeypatch.setattr(llm_client.httpx, "AsyncClient", lambda **kwargs: Client())

    result = asyncio.run(llm_client.call_tokenrouter_chat_completions(
        prompt="Extract a value using the supplied schema.",
        model="z-ai/glm-5.3-free",
        json_mode=True,
        max_output_tokens=100,
    ))

    assert result == '{"value":"ok"}'
    assert captured["method"] == "POST"
    payload = captured["json"]
    assert payload["stream"] is True
    assert payload["stream_options"] == {"include_usage": True}
    assert "response_format" not in payload
    assert "reasoning_effort" not in payload
    assert "thinking_level" not in payload
    assert "temperature" not in payload
    assert "max_tokens" not in payload


def test_empty_successful_glm_stream_returns_for_stage_fallback_without_retry(monkeypatch):
    monkeypatch.setattr(settings, "TOKENROUTER_API_KEY", "test-tokenrouter-key")
    calls = {"stream": 0}

    class StreamResponse:
        status_code = 200

        async def aiter_lines(self):
            yield 'data: {"choices":[{"delta":{"reasoning_content":"internal"},"finish_reason":"length"}],"usage":{"completion_tokens":100}}'
            yield "data: [DONE]"

        async def aread(self):
            return b""

    class StreamContext:
        async def __aenter__(self):
            return StreamResponse()

        async def __aexit__(self, *_):
            return False

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        def stream(self, *_args, **_kwargs):
            calls["stream"] += 1
            return StreamContext()

    monkeypatch.setattr(llm_client.httpx, "AsyncClient", lambda **kwargs: Client())

    result = asyncio.run(llm_client.call_tokenrouter_chat_completions(
        prompt="Return JSON.",
        model="z-ai/glm-5.3-free",
        json_mode=True,
    ))

    assert result is None
    assert calls["stream"] == 1
