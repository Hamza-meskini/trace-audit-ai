import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.services import llm_client


def test_databricks_retries_same_model_after_twenty_seconds(monkeypatch):
    monkeypatch.setattr(llm_client.settings, "DATABRICKS_TOKEN", "test")
    monkeypatch.setattr(llm_client.settings, "DATABRICKS_BASE_URL", "https://example.invalid")
    failed = MagicMock(status_code=429)
    success = MagicMock(status_code=200)
    success.json.return_value = {"choices": [{"message": {"content": '{"ok":true}'}}]}
    client = AsyncMock()
    client.post.side_effect = [failed, success]
    context = AsyncMock()
    context.__aenter__.return_value = client
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", lambda **kwargs: context)
    sleep = AsyncMock()
    monkeypatch.setattr(llm_client.asyncio, "sleep", sleep)
    diagnostics = {}
    result = asyncio.run(llm_client.call_databricks_chat_completions(
        "test", json_mode=True, diagnostics=diagnostics,
    ))
    assert result == '{"ok":true}'
    sleep.assert_awaited_once_with(20.0)
    assert client.post.await_count == 2
    assert all(call.kwargs["json"]["model"] == "system.ai.llama-4-maverick"
               for call in client.post.await_args_list)
    request_ids = {
        call.kwargs["json"]["client_request_id"]
        for call in client.post.await_args_list
    }
    assert len(request_ids) == 1
    assert all(call.kwargs["json"]["temperature"] == 0.0
               for call in client.post.await_args_list)
    assert diagnostics["attempt_count"] == 2
    assert diagnostics["rate_limit_retries"] == 1
    assert diagnostics["client_request_id"] in request_ids
