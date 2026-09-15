from __future__ import annotations

import asyncio
import json
import httpx
import pytest
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services import databricks_custom_reranker
from app.services.databricks_custom_reranker import _invoke_reranker, rerank_candidates
from app.services.retrieval import RetrievedChunk


def test_endpoint_adapter_sends_json_encoded_document_lists(monkeypatch) -> None:
    monkeypatch.setattr(databricks_custom_reranker.settings, "DATABRICKS_TOKEN", "test")
    monkeypatch.setattr(databricks_custom_reranker.settings, "DATABRICKS_CUSTOM_RERANKER_ENDPOINT", "bge-reranker-v2-m3")
    monkeypatch.setattr(databricks_custom_reranker, "_workspace_hostname", lambda: "workspace.example")
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"predictions": [{"scores": [4.0, -3.0]}]}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["payload"] = kwargs["json"]
        return Response()

    monkeypatch.setattr(databricks_custom_reranker.httpx, "post", fake_post)
    scores = _invoke_reranker(["requirement"], ["relevant", "irrelevant"])

    assert scores == [[4.0, -3.0]]
    assert captured["url"].endswith("/serving-endpoints/bge-reranker-v2-m3/invocations")
    row = captured["payload"]["dataframe_split"]["data"][0]
    assert row[0] == "requirement"
    assert json.loads(row[1]) == ["relevant", "irrelevant"]


def test_cross_encoder_scores_replace_retrieval_order(monkeypatch) -> None:
    monkeypatch.setattr(databricks_custom_reranker.settings, "DATABRICKS_CUSTOM_RERANKER_ENABLED", True)
    monkeypatch.setattr(databricks_custom_reranker.settings, "DATABRICKS_CUSTOM_RERANKER_ENDPOINT", "bge-reranker-v2-m3")
    monkeypatch.setattr(databricks_custom_reranker, "_invoke_reranker", lambda queries, documents: [[-8.0, 5.0]])
    candidates = [
        RetrievedChunk("lexical", "d", "report.pdf", "Test report", 1, "unrelated setup", 1.0, []),
        RetrievedChunk("relevant", "d", "report.pdf", "Test report", 12, "measured 0.0 liters", 0.2, []),
    ]

    selected, diagnostics = asyncio.run(rerank_candidates(
        "electrolyte spillage",
        candidates,
        top_k=2,
        strict=True,
    ))

    assert [item.chunk_id for item in selected] == ["relevant", "lexical"]
    assert selected[0].metadata["reranker_backend"] == "databricks-custom-bge"
    assert diagnostics["used"] is True


def test_batches_preserve_every_document_and_query_score(monkeypatch):
    monkeypatch.setattr(databricks_custom_reranker.settings, "DATABRICKS_TOKEN", "test")
    monkeypatch.setattr(databricks_custom_reranker.settings, "DATABRICKS_CUSTOM_RERANKER_BATCH_SIZE", 2)
    monkeypatch.setattr(databricks_custom_reranker, "_workspace_hostname", lambda: "workspace.example")
    calls = []

    def post(url, **kwargs):
        query, encoded = kwargs["json"]["dataframe_split"]["data"][0]
        documents = json.loads(encoded)
        calls.append((query, documents))
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "predictions": [{"scores": [float(d) + (10 if query == "second" else 0) for d in documents]}],
        })

    monkeypatch.setattr(databricks_custom_reranker.httpx, "post", post)
    scores = _invoke_reranker(["first", "second"], ["1", "2", "3"])
    assert scores == [[1, 2, 3], [11, 12, 13]]
    assert calls == [("first", ["1", "2"]), ("first", ["3"]), ("second", ["1", "2"]), ("second", ["3"])]


def test_timeout_retries_once_and_preserves_strict_failure(monkeypatch):
    monkeypatch.setattr(databricks_custom_reranker.settings, "DATABRICKS_TOKEN", "test")
    monkeypatch.setattr(databricks_custom_reranker, "_workspace_hostname", lambda: "workspace.example")
    monkeypatch.setattr(databricks_custom_reranker.time, "sleep", lambda seconds: None)
    calls = []

    def post(url, **kwargs):
        calls.append(kwargs)
        raise httpx.ReadTimeout("endpoint unavailable")

    monkeypatch.setattr(databricks_custom_reranker.httpx, "post", post)
    with pytest.raises(RuntimeError, match="no reranker scores were applied"):
        _invoke_reranker(["query"], ["doc"])
    assert len(calls) == 2
    assert calls[0]["timeout"] <= 60


def test_transient_timeout_can_recover_without_changing_candidates(monkeypatch):
    monkeypatch.setattr(databricks_custom_reranker.settings, "DATABRICKS_TOKEN", "test")
    monkeypatch.setattr(databricks_custom_reranker, "_workspace_hostname", lambda: "workspace.example")
    monkeypatch.setattr(databricks_custom_reranker.time, "sleep", lambda seconds: None)
    calls = []

    def post(url, **kwargs):
        calls.append(kwargs["json"])
        if len(calls) == 1:
            raise httpx.ReadTimeout("temporary")
        return httpx.Response(200, request=httpx.Request("POST", url), json={"predictions": [{"scores": [1.5]}]})

    monkeypatch.setattr(databricks_custom_reranker.httpx, "post", post)
    assert _invoke_reranker(["query"], ["doc"]) == [[1.5]]
    assert calls[0] == calls[1]


def test_authorization_errors_are_not_retried(monkeypatch):
    monkeypatch.setattr(databricks_custom_reranker.settings, "DATABRICKS_TOKEN", "test")
    monkeypatch.setattr(databricks_custom_reranker, "_workspace_hostname", lambda: "workspace.example")
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        return httpx.Response(403, request=httpx.Request("POST", url))

    monkeypatch.setattr(databricks_custom_reranker.httpx, "post", post)
    with pytest.raises(httpx.HTTPStatusError):
        _invoke_reranker(["query"], ["doc"])
    assert len(calls) == 1
