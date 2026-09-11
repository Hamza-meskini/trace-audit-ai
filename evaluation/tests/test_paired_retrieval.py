import pytest

from evaluation.compare_fmvss_retrieval import paired_contracts


def truth(clause):
    return {
        "requirement_id": "REF-" + clause, "clause": clause,
        "title": "Supply voltage", "requirement_text": "Voltage shall be below 20 V.",
        "logic": {"operator": "ALL_OF"},
        "conditions": [{"condition_id": "C1", "description": "Voltage limit",
                        "parameter": "voltage", "operator": "<", "threshold": 20,
                        "expected_status": "PROVEN", "evidence": [{"page": 99, "quote": "secret answer"}]}],
    }


def test_pairing_preserves_nested_identifiers_and_excludes_misses():
    predictions = [{"req_code": "S12.2(b)(1)"}, {"req_code": "S12.2(b)(2)"}]
    pairs, missing = paired_contracts([truth("S12.2(b)(2)"), truth("S12.3")], predictions)
    assert len(pairs) == 1
    assert pairs[0][1] is predictions[1]
    assert missing == ["REF-S12.3"]
    reference_condition = pairs[0][2]["conditions"][0]
    assert "evidence" not in reference_condition
    assert "expected_status" not in reference_condition
    assert "secret answer" not in str(pairs[0][2])


def test_duplicate_predictions_are_not_silently_selected():
    with pytest.raises(ValueError, match="Duplicate"):
        paired_contracts([truth("S12.3")], [{"req_code": "S12.3"}, {"req_code": "S12.3"}])
