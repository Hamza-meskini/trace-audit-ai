"""Regression checks for the synthetic multimodal diagnostic pack."""

from pathlib import Path

from evaluation.multimodal_benchmark.validate_multimodal_benchmark import validate
from evaluation.run_multimodal_benchmark import (
    MULTIMODAL_DEFAULT_MODEL,
    evidence_keys,
    load_dataset,
)
from evaluation.run_fmvss305_benchmark import _atomic_metrics
from app.services.ingestion import parse_document_with_metadata


def test_multimodal_dataset_is_structurally_valid():
    result = validate()
    assert result["valid"], result["errors"]
    assert result["counts"] == {
        "documents": 3,
        "requirements": 12,
        "atomic_conditions": 24,
        "tables_detected": 15,
        "figures_detected": 6,
    }
    assert result["warnings"] == []


def test_requirement_pdf_does_not_leak_atomic_answer_keys():
    dataset = load_dataset()
    filename = dataset["documents"]["requirements"]["filename"]
    parsed = parse_document_with_metadata(
        str(Path(__file__).resolve().parents[1] / "multimodal_benchmark" / "documents" / filename)
    )
    source = "\n".join(chunk.content for chunk in parsed.chunks)

    assert "C1:" not in source
    assert "C2:" not in source
    assert "C3:" not in source


def test_multimodal_dataset_covers_all_final_states():
    result = validate()
    assert result["final_distribution"] == {
        "SUPPORTED": 5,
        "PARTIAL": 2,
        "CONFLICT": 3,
        "UNKNOWN": 1,
        "MISSING": 1,
    }


def test_evidence_keys_include_document_identity():
    dataset = load_dataset()
    coolant = next(item for item in dataset["requirements"] if item["requirement_id"] == "MM-REQ-005")
    keys = evidence_keys(coolant)
    assert ("02_Aquila_Design_Validation_Test_Report.pdf", 6) in keys
    assert ("03_Aquila_Qualification_Inspection_Dossier.pdf", 3) in keys
    assert len(keys) == 2


def test_multimodal_default_model_is_llama_4_maverick():
    assert MULTIMODAL_DEFAULT_MODEL == "system.ai.llama-4-maverick"


def test_atomic_scoring_ignores_extra_applicability_condition():
    requirements = [{
        "requirement_id": "MM-REQ-011",
        "conditions": [
            {"condition_id": "MM-011-C1", "description": "torque remains zero", "expected_status": "UNTESTED"},
            {"condition_id": "MM-011-C2", "description": "movement remains bounded", "expected_status": "UNTESTED"},
        ],
    }]
    predictions = {
        "MM-REQ-011": [
            {"condition_id": "C0", "status": "PROVEN"},
            {"condition_id": "C1", "status": "UNTESTED"},
            {"condition_id": "C2", "status": "UNTESTED"},
        ]
    }

    metrics = _atomic_metrics(requirements, predictions)

    assert metrics["accuracy"] == 100.0
    assert metrics["alignment_coverage"] == 100.0
    assert metrics["alignment_methods"] == {"condition_ordinal": 2}
