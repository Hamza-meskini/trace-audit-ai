"""Integrity and scoring regressions for the extraction-only benchmark."""

from evaluation.extraction_benchmark.validate_extraction_benchmark import EXPECTED, validate
from evaluation.run_extraction_benchmark import (
    align_conditions,
    extraction_metrics,
    load_extraction_checkpoint,
    normalized_id,
    save_extraction_checkpoint,
)


def test_extraction_benchmark_is_valid_and_frozen():
    result = validate()
    assert result["valid"], result["errors"]
    assert result["counts"] == EXPECTED
    assert result["warnings"] == []


def test_atomic_answer_keys_do_not_leak_into_pdfs():
    result = validate()
    assert not [error for error in result["errors"] if "leaked" in error]


def test_condition_alignment_uses_semantics_not_condition_labels():
    expected = [{"condition_id": "REQ-1-C1", "description": "latency does not exceed 20 ms", "parameter": "latency", "operator": "<=", "threshold": 20, "unit": "ms"}]
    actual = [{"condition_id": "invented-name", "description": "maximum permitted latency is 20 milliseconds", "parameter": "latency", "operator": "<=", "threshold": 20, "unit": "ms"}]
    pairs = align_conditions(expected, actual)
    assert len(pairs) == 1
    assert pairs[0][2] >= 0.30


def test_identifier_normalization_preserves_exact_source_identity():
    assert normalized_id("NVA-ELE-001") == normalized_id("nva_ele_001")
    assert normalized_id("NVA-ELE-001") != normalized_id("NVA-ELE-002")


def test_benchmark_separates_semantic_alignment_from_structural_provenance():
    dataset = {"requirements": [{
        "requirement_id": "REQ-1",
        "document": "spec.pdf",
        "source_modality": "body_text",
        "requirement_text": "The valve shall open within 200 ms.",
        "conditions": [{
            "condition_id": "C1",
            "description": "valve opens within 200 ms",
            "parameter": "valve_open_latency",
            "operator": "<=",
            "threshold": 200,
            "unit": "ms",
            "condition_role": "VERIFICATION",
            "requires_visual_evidence": False,
        }],
        "logic": {"operator": "ALL_OF"},
    }]}
    predictions = {"spec.pdf": [{
        "req_code": "REQ-1",
        "conditions": [
            {
                "condition_id": "C1",
                "description": "valve opens within 200 ms",
                "source_span": "valve shall open within 200 ms",
                "source_parameter": "valve open time",
                "canonical_parameter": "valve_open_latency",
                "parameter": "valve_open_latency",
                "operator": "<=",
                "threshold": 200,
                "unit": "ms",
                "condition_role": "VERIFICATION",
                "requires_visual_evidence": False,
                "clause_ids": ["CL1"],
            },
            {
                "condition_id": "C2",
                "description": "duplicate invented atom",
                "source_span": "valve shall open",
                "source_parameter": "valve",
                "canonical_parameter": "valve_open",
                "parameter": "valve_open",
                "operator": "==",
                "threshold": True,
                "condition_role": "VERIFICATION",
                "requires_visual_evidence": False,
                "clause_ids": ["CL1"],
            },
        ],
        "semantic_clauses": [{
            "clause_id": "CL1",
            "clause_type": "VERIFICATION",
            "source_span": "valve shall open within 200 ms",
        }],
        "clause_coverage": [{"clause_id": "CL1", "clause": "valve shall open within 200 ms", "condition_ids": ["C1", "C2"]}],
        "logic": {"operator": "ALL_OF", "condition_ids": ["C1", "C2"]},
        "logic_tree": {"operator": "ALL_OF", "children": [
            {"operator": "CONDITION", "condition_id": "C1"},
            {"operator": "CONDITION", "condition_id": "C2"},
        ]},
        "contract_complete": True,
        "validation_issues": [],
    }]}

    metrics, _ = extraction_metrics(dataset, predictions)

    assert metrics["atomic_decomposition"]["recall"] == 100.0
    assert metrics["atomic_decomposition"]["precision"] == 50.0
    assert metrics["atomic_decomposition"]["exact_contracts"] == 0
    assert metrics["structural_quality"]["source_span_grounding_rate"] == 100.0
    assert metrics["structural_quality"]["normative_clause_coverage_rate"] == 100.0


def test_extraction_checkpoint_round_trip_is_scoped_and_compatible(tmp_path):
    path = tmp_path / "checkpoint.json"
    dataset = {"benchmark_id": "nova", "version": "1"}
    documents = {"one.pdf", "two.pdf"}
    predictions = {"one.pdf": [{"req_code": "REQ-1"}]}

    save_extraction_checkpoint(path, dataset, "test-model", documents, predictions)
    restored = load_extraction_checkpoint(path, dataset, "test-model", documents)

    assert restored == predictions
    assert load_extraction_checkpoint(path, dataset, "different-model", documents) == {}
