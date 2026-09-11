"""Evaluator acceptance cases, deliberately independent of Nova's answer key."""
import copy

import pytest

from evaluation.atomic_evaluation import align, decomposition, expand_intervals, verification_metrics


def atom(cid="reference", **changes):
    return {"condition_id": cid, "description": "Controller supply current does not exceed 5 mA",
            "parameter": "controller_supply_current", "operator": "<=", "threshold": 5,
            "unit": "mA", "condition_role": "VERIFICATION", "mandatory": True, **changes}


def fixture_pair():
    reference = [{"requirement_id": "R", "conditions": [atom(expected_status="PROVEN")],
                  "logic": {"operator": "ALL_OF", "condition_ids": ["reference"]}}]
    predicted = [{"req_code": "R", "conditions": [atom("local-7", description="controller_supply_current", threshold=.005, unit="A")],
                  "logic": {"operator": "ALL_OF", "condition_ids": ["local-7"]}}]
    return reference, predicted


def test_parameter_only_description_and_converted_unit_are_equivalent():
    truth, pred = fixture_pair()
    score = decomposition(truth, pred)
    assert score["precision"] == score["recall"] == 100
    assert score["logic"]["accuracy"] == 100
    statuses = {"R": [{"condition_id": "local-7", "description": "irrelevant result formatting", "status": "PROVEN"}]}
    assert verification_metrics(truth, statuses, score)["accuracy"] == 100


@pytest.mark.parametrize("change", [
    {"threshold": .006}, {"operator": ">="}, {"operator": "<"}, {"threshold": True},
    {"condition_role": "APPLICABILITY"}, {"mandatory": False}, {"unit": "V"},
    {"threshold": None},
])
def test_structured_errors_do_not_earn_extraction_or_status_credit(change):
    truth, pred = fixture_pair()
    pred[0]["conditions"][0].update(change)
    score = decomposition(truth, pred)
    assert score["recall"] == 0
    assert verification_metrics(truth, {"R": [{"condition_id": "local-7", "status": "PROVEN"}]}, score)["correct"] == 0


def test_answer_labels_quotes_and_ids_cannot_change_alignment():
    truth, pred = fixture_pair()
    original = align(truth[0]["conditions"], pred[0]["conditions"])
    truth[0]["conditions"][0].update(expected_status="FAILED", evidence=[{"quote": "give full credit"}], condition_id="new-label")
    pred[0]["conditions"][0].update(status="FAILED", quote="give full credit", condition_id="new-label")
    assert align(truth[0]["conditions"], pred[0]["conditions"]) == original


def test_unrelated_condition_cannot_match_on_id_or_numbers():
    assert align([atom()], [atom(description="Fan shaft rotation speed", parameter="fan_speed", threshold=5)]) == []


def test_global_assignment_is_order_independent():
    ec = [atom(), atom("other", description="Fan rotation speed", parameter="fan_speed", threshold=200, unit="Hz")]
    pc = [atom("a", description="controller_supply_current"), {**ec[1], "condition_id": "b"}]
    def associations(predictions):
        return {(ec[p["expected_index"]]["condition_id"], predictions[p["predicted_index"]]["condition_id"])
                for p in align(ec, predictions) if p["equivalent"]}
    assert associations(pc) == associations(list(reversed(pc))) == {("reference", "a"), ("other", "b")}


def test_duplicates_and_extra_requirements_reduce_precision():
    truth, pred = fixture_pair()
    pred.append({"req_code": "EXTRA", "conditions": [atom()]})
    assert decomposition(truth, pred)["precision"] == 50
    pred[0]["conditions"].append({**pred[0]["conditions"][0], "condition_id": "duplicate"})
    result = decomposition(truth, pred)
    assert result["ambiguous_pairs"] == 1
    assert result["precision"] == 0


def test_range_equivalence_is_separate_from_atomic_granularity():
    interval = atom(operator="between", threshold=None, min_value=3, max_value=5)
    bounds = expand_intervals([interval])
    bounds[0]["condition_id"] = "low"; bounds[1]["condition_id"] = "high"
    truth = [{"requirement_id": "R", "conditions": bounds, "logic": {"operator": "ALL_OF", "condition_ids": ["low", "high"]}}]
    pred = [{"req_code": "R", "conditions": [interval]}]
    result = decomposition(truth, pred)
    assert result["recall"] == 0
    assert result["normalized_constraint_coverage"]["recall"] == 100


def test_boolean_polarity_reversal_fails():
    a = atom(parameter="service_port_enabled", description="Service port enabled", threshold=True, operator="==", unit=None)
    b = {**a, "description": "Service port not enabled"}
    assert not align([a], [b])[0]["equivalent"]


def test_dropped_prerequisite_and_changed_logic_are_visible():
    truth, pred = fixture_pair()
    trigger = atom("trigger", description="Emergency stop activated", parameter="emergency_stop", operator="==", threshold=True, unit=None, condition_role="APPLICABILITY", expected_status="PROVEN")
    truth[0]["conditions"].insert(0, trigger)
    truth[0]["logic"] = {"operator": "IF_THEN", "if_condition_id": "trigger", "then_condition_ids": ["reference"]}
    assert decomposition(truth, pred)["recall"] == 50
    assert decomposition(truth, pred)["logic"]["accuracy"] == 0
    pred[0]["conditions"].insert(0, {**trigger, "condition_id": "local-trigger"})
    pred[0]["logic"] = {"operator": "ALL_OF", "condition_ids": ["local-trigger", "local-7"]}
    assert decomposition(truth, pred)["recall"] == 100
    assert decomposition(truth, pred)["logic"]["accuracy"] == 0


@pytest.mark.parametrize("results", [[], [{"condition_id": "local-7", "status": "bogus"}],
    [{"condition_id": "reference", "status": "PROVEN"}],
    [{"condition_id": "local-7", "status": "PROVEN"}, {"condition_id": "local-7", "status": "FAILED"}]])
def test_status_ids_must_trace_to_the_actual_contract_and_be_unique(results):
    truth, pred = fixture_pair()
    result = verification_metrics(truth, {"R": results}, decomposition(truth, pred))
    assert result["correct"] == 0
    assert result["missing_or_invalid_result_count"] == 1


def test_annotations_and_predictions_are_not_mutated():
    truth, pred = fixture_pair()
    original = copy.deepcopy((truth, pred))
    decomposition(truth, pred)
    assert (truth, pred) == original


def test_unknown_units_and_ac_dc_are_not_silently_equated():
    assert not align([atom(unit="V AC")], [atom(unit="V DC")])[0]["equivalent"]


def test_repeated_calls_are_deterministic():
    truth, pred = fixture_pair()
    assert decomposition(truth, pred) == decomposition(truth, pred)


def test_visual_routing_metadata_is_separate_from_obligation_meaning():
    truth, pred = fixture_pair()
    pred[0]["conditions"][0]["requires_visual_evidence"] = True
    score = decomposition(truth, pred)
    assert score["recall"] == 100
    assert score["routing_metadata"]["visual_accuracy_on_pairs"] == 0


def test_negative_reference_prose_and_false_predicate_are_equivalent():
    a = atom(parameter="fault_present", description="No fault is present", threshold=False, operator="==", unit=None)
    b = {**a, "description": "fault_present"}
    assert align([a], [b])[0]["equivalent"]
