"""Integrity regressions for the 48-requirement Nova end-to-end suite."""

import json
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from evaluation.extraction_benchmark.validate_end_to_end_benchmark import EXPECTED, validate
from evaluation.run_nova_end_to_end_benchmark import expected_evidence_keys, oracle_evidence
from app.services.verdict_aggregator import _aggregate_status_group
from app.services.extraction import ExtractedRequirement
from app.services.contract_transport import extraction_contract_payload
from evaluation import run_nova_end_to_end_benchmark as runner


BENCHMARK = Path(__file__).resolve().parents[1] / "extraction_benchmark"


def dataset() -> dict:
    return json.loads((BENCHMARK / "ground_truth.json").read_text(encoding="utf-8"))


def test_nova_end_to_end_corpus_is_complete_and_frozen():
    result = validate()
    assert result["valid"], result["errors"]
    assert result["warnings"] == []
    assert result["counts"] == EXPECTED
    assert set(result["final_distribution"]) == {"SUPPORTED", "PARTIAL", "CONFLICT", "MISSING", "UNKNOWN"}


def test_every_atomic_condition_has_one_document_scoped_evidence_annotation():
    data = dataset()
    evidence_names = {item["filename"] for item in data["evidence_documents"]}
    for requirement in data["requirements"]:
        keys = expected_evidence_keys(requirement)
        assert keys
        assert all(document in evidence_names and 2 <= page <= 5 for document, page in keys)
        for condition in requirement["conditions"]:
            assert condition["expected_status"] in {"PROVEN", "FAILED", "PENDING", "UNTESTED", "INCONCLUSIVE"}
            assert len(condition["evidence"]) == 1


def test_oracle_passage_selection_preserves_document_identity():
    requirement = dataset()["requirements"][0]
    document, page = next(iter(expected_evidence_keys(requirement)))
    chunks = [
        {"id": "wrong-doc", "document_name": "other.pdf", "page_number": page, "content": requirement["conditions"][0]["evidence"][0]["quote"], "metadata": {}},
        {"id": "wrong-page", "document_name": document, "page_number": page + 1, "content": requirement["conditions"][0]["evidence"][0]["quote"], "metadata": {}},
        {"id": "correct", "document_name": document, "page_number": page, "content": requirement["conditions"][0]["evidence"][0]["quote"], "metadata": {}},
    ]
    selected = oracle_evidence(requirement, chunks)
    assert [item["id"] for item in selected] == ["correct"]


def test_oracle_visual_selection_prefers_the_figure_block_on_the_annotated_page():
    requirement = next(item for item in dataset()["requirements"] if item["source_modality"] == "figure")
    annotation = requirement["conditions"][0]["evidence"][0]
    chunks = [
        {"id": "caption", "document_name": annotation["document"], "page_number": 5, "content": requirement["requirement_id"], "metadata": {"block_type": "text_section"}},
        {"id": "figure", "document_name": annotation["document"], "page_number": 5, "content": "Embedded figure", "metadata": {"block_type": "figure"}},
    ]
    selected = oracle_evidence(requirement, chunks)
    assert "figure" in {item["id"] for item in selected}


def test_visual_requirements_are_grounded_in_image_only_result_pages():
    data = dataset()
    visual_requirements = [item for item in data["requirements"] if item["source_modality"] == "figure"]
    assert len(visual_requirements) == 8
    for requirement in visual_requirements:
        for condition in requirement["conditions"]:
            annotation = condition["evidence"][0]
            assert annotation["page"] == 5
            assert annotation["block_type"] == "figure"


def test_expected_final_labels_follow_the_production_mechanical_aggregation():
    atomic_to_final = {
        "PROVEN": "SUPPORTED",
        "FAILED": "CONFLICT",
        "PENDING": "PARTIAL",
        "UNTESTED": "MISSING",
        "INCONCLUSIVE": "UNKNOWN",
    }
    for requirement in dataset()["requirements"]:
        statuses = [atomic_to_final[item["expected_status"]] for item in requirement["conditions"]]
        operator = requirement["logic"]["operator"]
        if operator == "IF_THEN":
            predicted = statuses[0] if statuses[0] != "SUPPORTED" else _aggregate_status_group("ALL_OF", statuses[1:])
        else:
            predicted = _aggregate_status_group(operator, statuses)
        assert predicted == requirement["expected_status"], requirement["requirement_id"]


def nested_prediction():
    leaf = lambda cid: {"operator": "CONDITION", "condition_id": cid}
    return ExtractedRequirement.model_validate({
        "req_code": "REQ-INVENTED-001", "title": "Alternatives", "description": "Either path can satisfy the obligation.",
        "conditions": [{"condition_id": f"C{i}", "operator": "<", "right_operand": "reference quantity"} for i in range(1, 5)],
        "logic_tree": {"operator": "ANY_OF", "children": [
            {"operator": "ALL_OF", "children": [leaf("C1"), leaf("C2")]},
            {"operator": "ALL_OF", "children": [leaf("C3"), leaf("C4")]},
        ]},
        "validation_issues": ["Source context needs review"], "ambiguities": ["Unspecified test phase"],
        "contract_complete": False,
        "semantic_clauses": [{"clause_id": "CL1", "clause_type": "VERIFICATION", "source_span": "Either path"}],
    })


def test_transport_matches_production_and_preserves_nested_logic():
    prediction = nested_prediction()
    output = runner.prediction_contracts([prediction])[0]
    for key, value in extraction_contract_payload(prediction).items():
        assert output[key] == value
    assert output["logic_tree"] == prediction.logic_tree
    assert output["conditions"][0]["right_operand"] == "reference quantity"
    output["logic_tree"]["children"].clear()
    assert len(prediction.logic_tree["children"]) == 2


def test_cli_defaults_to_fresh_extraction(monkeypatch):
    monkeypatch.setattr("sys.argv", ["nova"])
    assert runner.parse_args().no_resume is True
    assert runner.parse_args().retrieval_backend == "auto"


@pytest.mark.parametrize("mode,backend", [("end-to-end", "local"), ("end-to-end", "databricks"), ("oracle-contracts-evidence", "local")])
def test_runner_enriches_actual_verification_inputs_without_reference_leakage(monkeypatch, tmp_path, mode, backend):
    data = dataset()
    prediction = nested_prediction()
    monkeypatch.setattr(runner, "RESULTS", tmp_path)
    monkeypatch.setattr(runner, "_ingest_document", lambda path, doc_type: [{
        "id": path.stem, "document_id": path.stem, "document_name": path.name,
        "page_number": 2, "content": "Source content", "doc_type": doc_type, "metadata": {},
    }])
    profiles = {Path(d["filename"]).stem: SimpleNamespace(model_dump=lambda: {"document_role": "TEST_REPORT"})
                for d in runner.all_documents(data)}
    monkeypatch.setattr(runner, "profile_documents", AsyncMock(return_value=profiles))
    monkeypatch.setattr(runner, "document_profile_metrics", lambda *args: {"role_accuracy": 100})
    monkeypatch.setattr(runner, "run_extraction_stage", AsyncMock(return_value={
        "predictions": {"invented.pdf": [prediction.model_dump()]}, "metrics": {"extraction": {}},
    }))
    monkeypatch.setattr(runner, "precompute_chunk_embeddings", AsyncMock(return_value=[]))
    search = AsyncMock(return_value=([], SimpleNamespace(as_dict=lambda: {"backend_used": "local-hybrid"})))
    monkeypatch.setattr(runner, "retrieve_with_fallback", search)
    monkeypatch.setattr(runner, "sync_project_chunks", AsyncMock(return_value=SimpleNamespace(
        backend_used="databricks-ai-search", as_dict=lambda: {"backend_used": "databricks-ai-search"})))

    async def enrich(items, *, evidence_corpus):
        assert (evidence_corpus is None) == (mode == "oracle-contracts-evidence")
        if evidence_corpus:
            assert all(c["document_name"] in {d["filename"] for d in data["evidence_documents"]} for c in evidence_corpus)
        for item in items:
            assert "expected_status" not in json.dumps(item)
            assert "expected_review_state" not in json.dumps(item)
            item["targeted_extraction"] = {"test_marker": "enriched"}
            if evidence_corpus:
                item["candidate_chunks"] = [evidence_corpus[0]]
        return {"attempted": len(items), "succeeded": len(items), "failed": 0}

    monkeypatch.setattr(runner, "enrich_targeted_evidence_items", enrich)
    monkeypatch.setattr(runner, "describe_figure_candidates", AsyncMock(return_value={}))
    assess = AsyncMock(return_value={})
    monkeypatch.setattr(runner, "batch_assess_requirements", assess)
    result = asyncio.run(runner.run(mode, "test-model", None, "test-model",
        None, "", "test-model", None, 4, False, backend))
    supplied = assess.call_args.kwargs["req_items"]
    assert all(item["targeted_extraction"]["test_marker"] == "enriched" for item in supplied)
    assert result["verification_inputs"] == supplied
    assert result["metrics"]["integrity"]["runner_version"] == runner.RUNNER_VERSION
    assert "before_ai_extract_discovery" in result["metrics"]["retrieval"]
    assert search.call_args.kwargs["top_k"] == 8
    assert search.call_args.kwargs["backend"] == backend
    if mode == "end-to-end":
        assert supplied[0]["logic_tree"] == prediction.logic_tree
        assert supplied[0]["validation_issues"] == prediction.validation_issues
        assert supplied[0]["candidate_chunks"]
