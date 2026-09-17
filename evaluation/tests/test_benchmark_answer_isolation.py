from pathlib import Path
import sys
import copy
import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.extraction import ExtractedRequirement
from evaluation.run_fmvss305_benchmark import _atomic_metrics, _oracle_contracts
from evaluation.run_nova_end_to_end_benchmark import prediction_contracts


def truth(description="Insulation resistance exceeds 500 ohms", status="PROVEN"):
    return [{"requirement_id": "R1", "conditions": [{"condition_id": "C1", "description": description, "expected_status": status}]}]


def test_local_condition_id_and_position_cannot_earn_atomic_credit():
    predictions = {"R1": [{"condition_id": "C1", "description": "The enclosure is blue", "status": "PROVEN"}]}
    result = _atomic_metrics(truth(), predictions, strict=True)
    assert result["correct"] == 0
    assert result["missing_or_unaligned_predictions"] == 1


def test_atomic_alignment_does_not_consult_expected_status_and_cannot_reuse_prediction():
    requirements = truth()
    requirements[0]["conditions"].append({**requirements[0]["conditions"][0], "condition_id": "C2", "expected_status": "FAILED"})
    predictions = {"R1": [{"condition_id": "unrelated", "description": requirements[0]["conditions"][0]["description"], "status": "PROVEN"}]}
    before = _atomic_metrics(requirements, predictions, strict=True)
    changed = copy.deepcopy(requirements)
    for condition in changed[0]["conditions"]:
        condition["expected_status"] = "UNTESTED"
    after = _atomic_metrics(changed, predictions, strict=True)
    assert before["aligned"] == after["aligned"] == 1
    assert before["alignment_methods"] == after["alignment_methods"]


@pytest.mark.parametrize("status", [None, "", "garbage"])
def test_invalid_atomic_status_is_not_credited_as_untested(status):
    predictions = {"R1": [{"condition_id": "C1", "status": status}]}
    assert _atomic_metrics(truth(status="UNTESTED"), predictions)["correct"] == 0


def test_inference_keeps_unexpected_predictions_and_uses_no_reference_content():
    prediction = ExtractedRequirement.model_validate({"req_code": "EXTRA-999", "title": "Model-only title", "description": "Model-only description", "category": "Safety"})
    contracts = prediction_contracts([prediction])
    assert len(contracts) == 1
    assert contracts[0]["req_code"] == "EXTRA-999"
    assert contracts[0]["description"] == "Model-only description"
    with pytest.raises(ValueError, match="Duplicate"):
        prediction_contracts([prediction, prediction])


def test_oracle_contracts_strip_status_and_evidence_annotations():
    from evaluation.tests.test_nova_end_to_end_benchmark import dataset
    original = dataset()["requirements"]
    changed = copy.deepcopy(original)
    for requirement in changed:
        requirement["expected_status"] = "SECRET_LABEL"
        requirement["expected_review_state"] = "SECRET_REVIEW"
        for condition in requirement["conditions"]:
            condition["expected_status"] = "SECRET_ATOMIC_LABEL"
            condition["evidence"] = [{"quote": "SECRET_EVIDENCE"}]
    assert _oracle_contracts(original)[0] == _oracle_contracts(changed)[0]


@pytest.mark.parametrize("description", ["Insulation resistance exceeds 50 ohms", "Insulation resistance does not exceed 500 ohms"])
def test_changed_bound_or_negation_cannot_earn_atomic_credit(description):
    predictions = {"R1": [{"condition_id": "C1", "description": description, "status": "PROVEN"}]}
    assert _atomic_metrics(truth(), predictions, strict=True)["correct"] == 0
