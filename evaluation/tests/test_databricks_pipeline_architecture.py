from __future__ import annotations

import asyncio
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.schemas.contract import AtomicConditionContract, RequirementContract
from app.schemas.verification_result import (
    ConditionVerificationResult,
    SemanticAdjudicationResult,
    VerificationAnalysisResult,
)
from app.services import databricks_document_ai
from app.services.databricks_document_ai import _adapt_parse_result
from app.services.ingestion import build_structure_aware_chunks
from app.services import retrieval
from app.services.embedding import _content_hash
from app.services.retrieval import build_requirement_search_queries, retrieve_candidate_evidence
from app.services.verdict_aggregator import finalize_verdict
from app.services import verification_reasoner
from app.services.verification_reasoner import build_verification_prompt


def test_databricks_parse_adapter_preserves_table_figure_and_coordinates(tmp_path: Path) -> None:
    result = {
        "document": {
            "pages": [{"id": 0, "image_uri": "/Volumes/demo/page-1.jpg"}],
            "elements": [
                {"id": 1, "type": "section_header", "content": "Results", "bbox": [{"page_id": 0, "coord": [1, 2, 30, 10]}]},
                {"id": 2, "type": "table", "content": "<table><tr><th>Parameter</th><th>Value</th></tr><tr><td>Spillage</td><td>0.0 L</td></tr></table>", "confidence": 0.99, "bbox": [{"page_id": 0, "coord": [10, 20, 200, 80]}]},
                {"id": 3, "type": "figure", "content": "Yes (Fail) X No", "description": "The X selects No.", "bbox": [{"page_id": 0, "coord": [10, 90, 200, 140]}]},
                {"id": 4, "type": "caption", "content": "Figure 1: Electrolyte checklist", "bbox": [{"page_id": 0, "coord": [10, 145, 200, 160]}]},
            ],
        },
        "error_status": [],
        "metadata": {"version": "2.0"},
    }
    elements, page_count, diagnostics = _adapt_parse_result(
        result,
        file_path="report.pdf",
        source_sha256="abc",
        cache_path=tmp_path / "parse.json",
        cache_hit=True,
    )

    assert page_count == 1
    assert diagnostics["tables_detected"] == 1
    assert diagnostics["figure_descriptions"] == 1
    table = next(element for element in elements if element.element_type == "table")
    assert table.metadata["rows"] == [["Parameter", "Value"], ["Spillage", "0.0 L"]]
    assert table.page_number == 1
    assert table.bbox and table.bbox.x1 == 200

    chunks = build_structure_aware_chunks(elements, source_sha256="abc", parser_backend="databricks-ai-parse-2.0")
    figure = next(chunk for chunk in chunks if chunk.metadata["block_type"] == "figure")
    assert "Figure 1: Electrolyte checklist" in figure.content
    assert "VISUAL DESCRIPTION: The X selects No." in figure.content
    assert figure.metadata["visual_analysis"]["status"] == "complete"
    assert figure.metadata["visual_analysis"]["source"] == "databricks-ai-parse"


def test_retrieval_unions_whole_clause_atomic_and_identifier_queries() -> None:
    queries = build_requirement_search_queries(
        "FMVSS-305-S5.2",
        "Battery retention",
        "S5.2 The battery shall remain secured after impact.",
        [{
            "condition_id": "C1",
            "description": "No battery movement into the passenger compartment",
            "source_span": "The battery shall remain secured",
            "parameter": "battery retention",
        }],
        [{"source_span": "after impact", "subject": "battery", "predicate": "remains secured"}],
    )
    assert queries[0].startswith("FMVSS-305-S5.2")
    assert any("No battery movement" in query for query in queries)
    assert any("after impact" in query for query in queries)
    assert "FMVSS-305-S5.2" in queries

    chunks = [
        {
            "id": f"c{index}",
            "document_id": "d1",
            "document_name": "report.pdf",
            "doc_type": "test_report",
            "page_number": index,
            "content": content,
            "metadata": {"block_type": "table"},
        }
        for index, content in enumerate([
            "S5.2 Battery retention after impact: no movement or intrusion observed.",
            "Battery test setup and instrumentation.",
            "Post-impact passenger compartment inspection completed.",
            "Battery mounting photographs and results.",
            "General test conclusion.",
        ], 1)
    ]
    retrieved = retrieve_candidate_evidence(queries[0], chunks, top_k=4, condition_queries=queries[1:])
    ranked = [item for item in retrieved if not (item.metadata or {}).get("context_only")]
    assert ranked[0].chunk_id == "c1"
    assert len(ranked) == 4


def test_hybrid_retrieval_embeds_whole_clause_and_atomic_queries(monkeypatch) -> None:
    chunks = [
        {
            "id": "overall",
            "document_id": "d1",
            "document_name": "report.pdf",
            "doc_type": "test_report",
            "page_number": 1,
            "content": "REQ-X overall system verification conclusion.",
            "metadata": {},
        },
        {
            "id": "thermal",
            "document_id": "d1",
            "document_name": "report.pdf",
            "doc_type": "test_report",
            "page_number": 2,
            "content": "REQ-X thermal chamber reached 65 C.",
            "metadata": {},
        },
    ]
    captured: dict[str, list[str]] = {}

    async def fake_embed_batch(texts: list[str], task_type: str):
        captured[task_type] = texts
        return [[1.0, 0.0], [0.0, 1.0]]

    monkeypatch.setattr(retrieval, "_has_embedding_key", lambda: True)
    monkeypatch.setattr(retrieval, "embed_batch", fake_embed_batch)
    results = asyncio.run(retrieval.retrieve_candidate_evidence_hybrid(
        "REQ-X overall system requirement",
        chunks,
        chunk_embeddings=[[1.0, 0.0], [0.0, 1.0]],
        top_k=2,
        condition_queries=["REQ-X thermal chamber 65 C"],
    ))

    assert captured["RETRIEVAL_QUERY"] == [
        "REQ-X overall system requirement",
        "REQ-X thermal chamber 65 C",
    ]
    assert {item.chunk_id for item in results} == {"overall", "thermal"}
    assert _content_hash("same text", "RETRIEVAL_QUERY") != _content_hash(
        "same text", "RETRIEVAL_DOCUMENT"
    )


def test_verifier_keeps_targeted_extraction_advisory() -> None:
    contract = RequirementContract(requirement_id="R1", req_code="R1", title="Limit", raw_text="Value shall be <= 5")
    prompt, _ = build_verification_prompt(
        contract,
        [],
        targeted_extraction={"response": {"observed_facts": {"value": "Measured value 4"}}},
    )
    assert "Measured value 4" in prompt
    assert "cannot replace or overrule raw evidence" in prompt


def test_shared_targeted_enrichment_feeds_production_and_benchmark(monkeypatch) -> None:
    monkeypatch.setattr(
        databricks_document_ai.settings,
        "DATABRICKS_TARGETED_EXTRACTION_ENABLED",
        True,
    )
    monkeypatch.setattr(
        databricks_document_ai,
        "extract_targeted_evidence",
        lambda requirement, chunks: {"response": {"observed_facts": {"value": requirement}}},
    )
    items = [{
        "req_code": "R1",
        "title": "Limit",
        "description": "Value shall be <= 5",
        "candidate_chunks": [{"content": "Measured value 4"}],
    }]

    stats = asyncio.run(databricks_document_ai.enrich_targeted_evidence_items(items))

    assert stats == {"attempted": 1, "succeeded": 1, "failed": 0}
    assert items[0]["targeted_extraction"]["response"]["observed_facts"]["value"].startswith("R1 Limit")


def test_incomplete_atomic_contract_is_advisory_without_hiding_failures() -> None:
    contract = RequirementContract(
        requirement_id="R1",
        req_code="R1",
        title="Compound requirement",
        raw_text="A and B shall be demonstrated.",
        contract_complete=False,
        unmapped_obligations=["B shall be demonstrated"],
        atomic_conditions=[
            AtomicConditionContract(condition_id="C1", description="A"),
            AtomicConditionContract(condition_id="C2", description="B"),
        ],
    )
    provisional = VerificationAnalysisResult(
        status="SUPPORTED",
        confidence=94,
        reason="The complete clause is supported by the supplied report.",
        condition_results=[ConditionVerificationResult(condition_id="C1", status="PROVEN")],
    )
    result = finalize_verdict(contract, provisional, qualifications=[])
    assert result.status == "SUPPORTED"
    assert result.confidence == 80
    assert result._diagnostics["atomic_aggregate_status"] == "PARTIAL"
    assert result._diagnostics["atomic_advisory_fallback"] is True

    conflict = VerificationAnalysisResult(
        status="SUPPORTED",
        condition_results=[
            ConditionVerificationResult(condition_id="C1", status="FAILED"),
            ConditionVerificationResult(condition_id="C2", status="PROVEN"),
        ],
        reason="",
    )
    conflict_result = finalize_verdict(contract, conflict, qualifications=[])
    assert conflict_result.status == "CONFLICT"
    assert conflict_result._diagnostics["atomic_advisory_fallback"] is False


def test_batch_size_one_uses_single_requirement_path(monkeypatch) -> None:
    monkeypatch.setattr(verification_reasoner.settings, "DATABRICKS_TOKEN", "test")
    response_models = []

    async def fake_generate_structured(**kwargs):
        response_models.append(kwargs["response_model"])
        return VerificationAnalysisResult(
            status="MISSING",
            confidence=90,
            reason="No evidence addresses the condition.",
            condition_results=[ConditionVerificationResult(
                condition_id="C1",
                status="UNTESTED",
                relationship="NOT_ADDRESSED",
                execution_state="NOT_EXECUTED",
                evidence_value_role="NOT_ADDRESSED",
            )],
        )

    monkeypatch.setattr(verification_reasoner, "generate_structured", fake_generate_structured)
    contract = RequirementContract(
        requirement_id="R1",
        req_code="R1",
        title="No movement",
        raw_text="Movement shall not occur.",
        atomic_conditions=[AtomicConditionContract(condition_id="C1", description="No movement")],
    )
    results = asyncio.run(verification_reasoner.evaluate_batch_verification([{
        "contract": contract,
        "candidate_chunks": [],
    }]))

    assert results["R1"].status == "MISSING"
    assert response_models == [VerificationAnalysisResult]


def test_compact_recovery_turns_valid_untested_conditions_into_missing(monkeypatch) -> None:
    monkeypatch.setattr(verification_reasoner.settings, "DATABRICKS_TOKEN", "test")
    response_models = []

    async def fake_generate_structured(**kwargs):
        response_model = kwargs["response_model"]
        response_models.append(response_model)
        if response_model is VerificationAnalysisResult:
            raise ValueError("malformed full response")
        return SemanticAdjudicationResult(
            reason="No supplied excerpt addresses either condition.",
            condition_results=[
                ConditionVerificationResult(
                    condition_id=condition_id,
                    status="UNTESTED",
                    relationship="NOT_ADDRESSED",
                    execution_state="NOT_EXECUTED",
                    evidence_value_role="NOT_ADDRESSED",
                )
                for condition_id in ("C1", "C2")
            ],
        )

    monkeypatch.setattr(verification_reasoner, "generate_structured", fake_generate_structured)
    contract = RequirementContract(
        requirement_id="R2",
        req_code="R2",
        title="Drive-away protection",
        raw_text="If connected for charging, movement shall not occur.",
        atomic_conditions=[
            AtomicConditionContract(condition_id="C1", description="Connected for charging"),
            AtomicConditionContract(condition_id="C2", description="Movement prevented"),
        ],
    )
    result = asyncio.run(verification_reasoner.evaluate_requirement_verification(
        contract,
        [],
        allow_deterministic_fallback=False,
    ))

    assert result.status == "MISSING"
    assert result._diagnostics["compact_recovery"] is True
    assert response_models == [VerificationAnalysisResult, SemanticAdjudicationResult]
