"""Saved evaluation reproducibility and reference-integrity checks."""
import hashlib
import json

import pytest

from evaluation.rescore_atomic_benchmark import rescore


def test_rescore_uses_embedded_contracts_and_refuses_changed_reference(tmp_path):
    truth = tmp_path / "truth.json"
    result = tmp_path / "result.json"
    condition = {"condition_id": "gold", "description": "Valve opening time",
                 "parameter": "valve_opening_time", "operator": "<=", "threshold": 20,
                 "unit": "ms", "expected_status": "PROVEN"}
    truth.write_text(json.dumps({"requirements": [{"requirement_id": "R", "conditions": [condition],
        "logic": {"operator": "ALL_OF", "condition_ids": ["gold"]}}]}))
    result.write_text(json.dumps({
        "evaluated_contracts": [{"req_code": "R", "conditions": [{**condition, "condition_id": "model-local"}],
                                "logic": {"operator": "ALL_OF", "condition_ids": ["model-local"]}}],
        "metrics": {"final_atomic": {"accuracy": 0}, "integrity": {
            "ground_truth_sha256": hashlib.sha256(truth.read_bytes()).hexdigest()}},
        "requirements": [{"requirement_id": "R",
            "raw_llm_condition_results": [{"condition_id": "model-local", "status": "PROVEN"}],
            "final_condition_results": [{"condition_id": "model-local", "status": "PROVEN"}]}],
    }))
    before = result.read_bytes()
    assert rescore(result, truth)["final_atomic"]["accuracy"] == 100
    assert result.read_bytes() == before
    truth.write_text(truth.read_text().replace("PROVEN", "FAILED"))
    with pytest.raises(ValueError, match="reference dataset changed"):
        rescore(result, truth)


def test_legacy_run_requires_an_explicit_extraction_artifact(tmp_path):
    result, truth = tmp_path / "result.json", tmp_path / "truth.json"
    result.write_text("{}")
    truth.write_text('{"requirements": []}')
    with pytest.raises(ValueError, match="provide its original"):
        rescore(result, truth)
