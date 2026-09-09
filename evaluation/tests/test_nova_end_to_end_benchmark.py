"""Integrity regressions for the 48-requirement Nova end-to-end suite."""

import json
from pathlib import Path

from evaluation.extraction_benchmark.validate_end_to_end_benchmark import EXPECTED, validate
from evaluation.run_nova_end_to_end_benchmark import expected_evidence_keys, oracle_evidence
from app.services.verdict_aggregator import _aggregate_status_group


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
