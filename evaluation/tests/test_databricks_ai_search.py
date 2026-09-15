from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services import databricks_ai_search
from app.services.databricks_ai_search import _source_rows, retrieve_with_fallback
from app.services.databricks_document_ai import _adapt_prep_search_result


@pytest.fixture(autouse=True)
def disable_custom_reranker(monkeypatch):
    monkeypatch.setattr(
        databricks_ai_search.settings,
        "DATABRICKS_CUSTOM_RERANKER_ENABLED",
        False,
    )


def test_ai_prep_search_adapter_preserves_retrieval_and_embedding_representations() -> None:
    result = {
        "document": {
            "source_uri": "/Volumes/catalog/schema/docs/report.pdf",
            "contents": [{
                "chunk_id": "managed-1",
                "chunk_position": 7,
                "chunk_to_retrieve": "Results > S5.1\nTotal spillage was 0.0 liters.",
                "chunk_to_embed": "FMVSS S5.1 electrolyte retention results total spillage",
                "metadata": {"section": "Results"},
                "pages": [{"page_id": 4, "image_uri": "/Volumes/images/page-5.jpg"}],
            }],
        },
        "error_status": [],
    }

    chunks = _adapt_prep_search_result(
        result,
        source_sha256="abc",
        ingestion_schema_version="3.1",
    )

    assert len(chunks) == 1
    assert chunks[0].page_number == 5
    assert chunks[0].content.startswith("Results > S5.1")
    assert chunks[0].metadata["chunk_to_embed"].startswith("FMVSS S5.1")
    assert chunks[0].metadata["databricks_prep_chunk_id"] == "managed-1"
    assert chunks[0].metadata["image_uris"] == ["/Volumes/images/page-5.jpg"]


def test_source_rows_namespace_index_key_and_preserve_local_traceability_id() -> None:
    rows = _source_rows("project-1", [{
        "id": "local-db-id",
        "document_id": "doc-1",
        "document_name": "report.pdf",
        "doc_type": "Test report",
        "page_number": 5,
        "content": "citable content",
        "metadata": {"chunk_to_embed": "enriched search representation"},
    }])

    assert rows[0][0].endswith(":local-db-id")
    assert rows[0][0] != "local-db-id"
    assert rows[0][1] == "project-1"
    assert rows[0][6] == "citable content"
    assert rows[0][7] == "enriched search representation"
    assert json.loads(rows[0][8])["traceaudit_chunk_id"] == "local-db-id"


def test_managed_retrieval_uses_hybrid_results_and_discards_stale_index_rows(monkeypatch) -> None:
    monkeypatch.setattr(databricks_ai_search.settings, "DATABRICKS_AI_SEARCH_INDEX", "main.audit.chunks_index")
    monkeypatch.setattr(databricks_ai_search.settings, "DATABRICKS_AI_SEARCH_SOURCE_TABLE", "main.audit.chunks")
    monkeypatch.setattr(databricks_ai_search.settings, "DATABRICKS_AI_SEARCH_ENDPOINT", "auditrace")
    monkeypatch.setattr(databricks_ai_search.settings, "DATABRICKS_AI_SEARCH_RERANK_ENABLED", True)

    def fake_query(query: str, project_id: str, num_results: int):
        assert project_id == "p1"
        return [
            {
                "chunk_id": "good",
                "document_id": "d1",
                "document_name": "report.pdf",
                "doc_type": "Test report",
                "page_number": "2",
                "chunk_to_retrieve": "S5.2 battery remained outside the occupant compartment.",
                "metadata_json": '{"block_type":"table"}',
                "score": "0.94",
            },
            {
                "chunk_id": "stale",
                "document_id": "old",
                "document_name": "old.pdf",
                "chunk_to_retrieve": "stale result",
                "score": "0.99",
            },
        ]

    monkeypatch.setattr(databricks_ai_search, "_query_once", fake_query)
    chunks = [{
        "id": "good",
        "document_id": "d1",
        "document_name": "report.pdf",
        "doc_type": "Test report",
        "page_number": 2,
        "content": "S5.2 battery remained outside the occupant compartment.",
        "document_profile": {"primary_role": "test_report"},
        "metadata": {"block_type": "table"},
    }]

    values, diagnostics = asyncio.run(retrieve_with_fallback(
        "S5.2 battery retention",
        chunks,
        project_id="p1",
        top_k=1,
        backend="databricks",
        strict=True,
    ))

    assert [item.chunk_id for item in values] == ["good"]
    assert values[0].document_profile == {"primary_role": "test_report"}
    assert values[0].metadata["retrieval_backend"] == "databricks-ai-search"
    assert diagnostics.backend_used == "databricks-ai-search"
    assert diagnostics.reranker_enabled is True


def test_explicit_managed_benchmark_does_not_silently_fallback(monkeypatch) -> None:
    async def fail(*args, **kwargs):
        raise RuntimeError("index unavailable")

    monkeypatch.setattr(databricks_ai_search, "_retrieve_managed", fail)
    with pytest.raises(RuntimeError, match="index unavailable"):
        asyncio.run(retrieve_with_fallback(
            "requirement",
            [],
            project_id="benchmark",
            backend="databricks",
            strict=True,
        ))


def test_workspace_reranker_policy_error_retries_managed_search_without_reranker(monkeypatch) -> None:
    monkeypatch.setattr(databricks_ai_search.settings, "DATABRICKS_AI_SEARCH_INDEX", "main.audit.chunks_index")
    monkeypatch.setattr(databricks_ai_search.settings, "DATABRICKS_AI_SEARCH_SOURCE_TABLE", "main.audit.chunks")
    monkeypatch.setattr(databricks_ai_search.settings, "DATABRICKS_AI_SEARCH_ENDPOINT", "auditrace")
    monkeypatch.setattr(databricks_ai_search.settings, "DATABRICKS_AI_SEARCH_RERANK_ENABLED", True)
    calls = []

    class Indexes:
        def query_index(self, **kwargs):
            calls.append(kwargs)
            if "reranker" in kwargs:
                raise RuntimeError("workspace-level configuration prevents access to the reranker model")
            return SimpleNamespace(
                manifest=SimpleNamespace(columns=[
                    SimpleNamespace(name="chunk_id"),
                    SimpleNamespace(name="chunk_to_retrieve"),
                ]),
                result=SimpleNamespace(data_array=[["c1", "measured 0.0 liters"]]),
            )

    monkeypatch.setattr(
        databricks_ai_search,
        "_workspace_client",
        lambda: SimpleNamespace(vector_search_indexes=Indexes()),
    )
    result = databricks_ai_search._query_once("spillage", "p1", 5)

    assert len(calls) == 2
    assert "reranker" in calls[0]
    assert "reranker" not in calls[1]
    assert result["reranker_used"] is False
    assert result["rows"][0]["chunk_id"] == "c1"
