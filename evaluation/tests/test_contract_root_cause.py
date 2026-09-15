"""Regressions for contract construction and shared Boolean semantics."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import TypeAdapter, ValidationError

from app.schemas.contract import AtomicConditionContract, RequirementContract, parse_requirement_contract
from app.schemas.contract_logic import LogicNode, flat_projection
from app.schemas.verification_result import ConditionVerificationResult
from app.services import extraction, llm_client
from app.services.classification import _supporting_results_for_review
from app.services.verdict_aggregator import aggregate_condition_statuses, aggregation_input_issues
from app.services.verification_reasoner import _logic_line, _logic_retry_condition_ids


def leaf(identifier):
    return {"operator": "CONDITION", "condition_id": identifier}


def contract(tree, ids=("C1", "C2", "C3", "C4")):
    return RequirementContract(
        requirement_id="test", req_code="REQ-TEST-001", title="Alternative compliance paths",
        logic_tree=tree,
        atomic_conditions=[AtomicConditionContract(condition_id=i) for i in ids],
    )


def result(identifier, status):
    return ConditionVerificationResult(condition_id=identifier, status=status)


def alternatives():
    return {"operator": "ANY_OF", "children": [
        {"operator": "ALL_OF", "children": [leaf("C1"), leaf("C2")]},
        {"operator": "ALL_OF", "children": [leaf("C3"), leaf("C4")]},
    ]}


def test_nested_branch_needs_all_its_obligations_in_both_aggregation_and_review():
    c = contract(alternatives())
    results = [result("C1", "PROVEN"), result("C2", "FAILED"),
               result("C3", "PROVEN"), result("C4", "INCONCLUSIVE")]
    assert flat_projection(c.logic_tree) is None
    assert aggregate_condition_statuses(c, results)[0] != "SUPPORTED"
    assert _supporting_results_for_review(c, results)[1]
    assert '"ALL_OF"' in _logic_line(c)
    assert set(_logic_retry_condition_ids(c, results)) == {"C1", "C2", "C3", "C4"}
    results[-1] = result("C4", "PROVEN")
    proof, missing = _supporting_results_for_review(c, results)
    assert not missing
    assert {r.condition_id for r in proof} == {"C3", "C4"}
    assert aggregate_condition_statuses(c, results)[0] == "SUPPORTED"


def test_nonmandatory_antecedent_is_not_dropped_or_zipped_to_wrong_result():
    c = contract({"operator": "IF_THEN", "antecedent": leaf("C1"), "consequent": leaf("C2")}, ("C1", "C2"))
    c.atomic_conditions[0].mandatory = False
    c.atomic_conditions[0].condition_role = "APPLICABILITY"
    assert aggregate_condition_statuses(c, [result("C2", "FAILED"), result("C1", "PROVEN")])[0] == "CONFLICT"


def test_single_leaf_does_not_fail_due_to_legacy_all_of_projection():
    c = contract(leaf("C1"), ("C1",))
    assert c.logic.operator == "ALL_OF"
    assert aggregation_input_issues(c, [result("C1", "INCONCLUSIVE")]) == []


@pytest.mark.parametrize("tree", [
    {"operator": "IF_THEN", "antecedent": leaf("C1")},
    {"operator": "ANY_OF", "children": [leaf("C1")]},
    {"operator": "CONDITION", "condition_id": "C1", "children": [leaf("C2")]},
])
def test_recursive_schema_rejects_missing_or_mixed_node_shapes(tree):
    with pytest.raises(ValidationError):
        TypeAdapter(LogicNode).validate_python(tree)


def test_schema_accepts_enum_values_and_contains_no_flat_graph():
    condition = extraction.ExtractedCondition(threshold=["activated", "ready-to-drive"], operator="in")
    parsed = AtomicConditionContract(condition_id="C1", **condition.model_dump(exclude={"condition_id"}))
    assert parsed.threshold == ["activated", "ready-to-drive"]
    schema = extraction.AtomicContractDraftResult.model_json_schema()
    properties = schema["$defs"]["AtomicContractDraft"]["properties"]
    assert "logic" not in properties
    assert "logic_tree" not in properties
    assert "logic_nodes" in properties
    assert set(schema["$defs"]["AtomicLogicNode"]["properties"]["operator"]["enum"]) == {
        "CONDITION", "ALL_OF", "ANY_OF", "IF_THEN",
    }
    c = parse_requirement_contract(
        req_code="REQ-TEST-001", title="State",
        structured_conditions=[parsed.model_dump()], logic_tree=leaf("C1"),
    )
    assert RequirementContract.model_validate(c.model_dump()).expected_value == parsed.threshold


def test_invalid_child_is_preserved_for_validation_and_cannot_fall_back_to_flat_logic():
    tree = {"operator": "ALL_OF", "children": [leaf("C1"), {"operator": "XOR"}]}
    normalized = extraction._canonicalize_logic_tree(tree, extraction.ExtractedRequirementLogic(), {"C1"})
    assert len(normalized["children"]) == 2
    assert extraction._logic_tree_structure_issues(normalized, {"C1"})
    c = contract(normalized, ("C1",))
    assert aggregate_condition_statuses(c, [result("C1", "PROVEN")])[0] == "UNKNOWN"


def test_omitted_obligation_is_not_relabelled_as_applicability():
    source, draft = make_draft()
    discovered = extraction.DiscoveredRequirement(req_code="REQ-TEST-001", title="State", description=source)
    c = extraction._compile_atomic_contract_draft(draft.requirements[0], discovered, source)
    c.conditions.append(extraction.ExtractedCondition(
        **{**c.conditions[0].model_dump(), "condition_id": "C2", "canonical_parameter": "other_obligation"}
    ))
    extraction._normalize_extracted_requirements([c])
    assert c.conditions[1].condition_role == "VERIFICATION"
    assert not c.contract_complete
    assert any("omits mandatory verification" in issue for issue in c.validation_issues)


def test_numeric_fidelity_excludes_identifiers_but_keeps_real_limits():
    assert extraction._numeric_tokens("S7.6.6: If V1 is greater than V2, measure 10 MΩ; see section 5.3.") == ["10"]
    assert extraction._numeric_tokens("Maintain -10 to +20 °C and no more than 0.1 ohm.") == ["-10", "+20", "0.1"]
    assert extraction._numeric_values(extraction.ExtractedCondition(threshold="V2")) == set()
    assert extraction._numeric_tokens(
        "barrier that conforms to part 587 of this chapter moving at 54 km/h, "
        "with the 49 CFR part 572 dummies specified in 571.214 of this chapter"
    ) == ["54"]


def make_draft():
    source = "The operating state shall be activated or ready-to-drive."
    item = {
        "req_code": "REQ-TEST-001",
        "conditions": [{"condition_id": "C1", "condition_role": "VERIFICATION",
                        "description": source, "source_span": source, "source_parameter": "operating state",
                        "canonical_parameter": "operating_state", "operator": "in",
                        "threshold": ["activated", "ready-to-drive"], "clause_ids": ["CL1"]}],
        "semantic_clauses": [{"clause_id": "CL1", "clause_type": "VERIFICATION", "source_span": source}],
        "clause_coverage": [{"clause_id": "CL1", "clause": source, "condition_ids": ["C1"]}],
        "logic_nodes": [{"node_id": "N1", "operator": "CONDITION", "condition_id": "C1"}],
        "root_node_id": "N1", "decomposition_confidence": .95, "contract_complete": True,
    }
    return source, extraction.AtomicContractDraftResult.model_validate({"requirements": [item]})


def test_default_construction_skips_independent_planning_call(monkeypatch):
    source, draft = make_draft()
    discovered = extraction.DiscoveredRequirement(req_code="REQ-TEST-001", title="Operating state", description=source)
    monkeypatch.setattr(extraction, "_discover_requirements", AsyncMock(return_value=[discovered]))
    planner = AsyncMock()
    monkeypatch.setattr(extraction, "_plan_requirement_clauses", planner)
    generate = AsyncMock(return_value=draft)
    monkeypatch.setattr(extraction, "generate_structured", generate)
    monkeypatch.setattr(extraction.settings, "ATOMIC_CONSTRUCTION_MODE", "direct")
    contracts = asyncio.run(extraction._extract_chunk_staged(
        "REQ-TEST-001: " + source, doc_name="requirements.pdf", active_model="test",
        thinking_level=None, chunk_label="1", allow_rule_fallback=False, allow_model_fallback=False,
    ))
    planner.assert_not_awaited()
    generate.assert_awaited_once()
    assert contracts[0].contract_complete
    assert contracts[0].validation_issues == []


def test_schema_retry_uses_actual_error_and_preserves_response_trace(monkeypatch):
    source, draft = make_draft()
    calls = []

    async def generate(**kwargs):
        calls.append(kwargs["prompt"])
        if len(calls) == 1:
            kwargs["diagnostics"].update(error="logic_tree.consequent: Field required", raw_response="bad contract")
            return None
        kwargs["diagnostics"].update(raw_response=draft.model_dump_json(), finish_reason="stop", usage={"completion_tokens": 200})
        return draft

    monkeypatch.setattr(extraction, "generate_structured", generate)
    c = asyncio.run(extraction._construct_atomic_contract(
        extraction.DiscoveredRequirement(req_code="REQ-TEST-001", title="State", description=source),
        None, source, doc_name="req.pdf", active_model="test", fallback_model=None,
        thinking_level=None, allow_rule_fallback=False, allow_model_fallback=False,
    ))
    assert "logic_tree.consequent: Field required" in calls[1]
    assert "bad contract" in calls[1]
    assert "Do not stringify the unit list or drop a limit" in calls[1]
    assert c.construction_diagnostics[0]["raw_response"] == "bad contract"
    assert c.construction_diagnostics[1]["usage"]["completion_tokens"] == 200


def fake_http(monkeypatch, response):
    monkeypatch.setattr(llm_client.settings, "DATABRICKS_TOKEN", "test-key")
    monkeypatch.setattr(llm_client.settings, "DATABRICKS_BASE_URL", "https://workspace.example/api")
    client = AsyncMock()
    client.post.return_value = response
    context = AsyncMock()
    context.__aenter__.return_value = client
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", lambda **kwargs: context)
    return client


def test_unrelated_http400_does_not_disable_structured_outputs(monkeypatch):
    model = "test-contract-endpoint"
    llm_client._DATABRICKS_JSON_SCHEMA_UNSUPPORTED_MODELS.discard(model)
    client = fake_http(monkeypatch, MagicMock(status_code=400, text="max_tokens exceeds context length"))
    diagnostics = {}
    assert asyncio.run(llm_client.call_databricks_chat_completions(
        "test", model=model, response_schema={"type": "object"}, diagnostics=diagnostics,
    )) is None
    client.post.assert_awaited_once()
    assert model not in llm_client._DATABRICKS_JSON_SCHEMA_UNSUPPORTED_MODELS
    assert "max_tokens" in diagnostics["error"]


def test_truncation_is_recorded_without_repeating_identical_request(monkeypatch):
    response = MagicMock(status_code=200)
    response.json.return_value = {"choices": [{"message": {"content": '{"requirements":['}, "finish_reason": "length"}],
                                  "usage": {"completion_tokens": 8192}}
    client = fake_http(monkeypatch, response)
    diagnostics = {}
    assert asyncio.run(llm_client.call_databricks_chat_completions(
        "test", json_mode=True, diagnostics=diagnostics,
    )) is None
    client.post.assert_awaited_once()
    assert diagnostics["finish_reason"] == "length"
    assert diagnostics["usage"]["completion_tokens"] == 8192
    assert "truncated" in diagnostics["error"]


def test_shallow_draft_compiles_stable_ids_and_nested_logic():
    source = "The system shall isolate power or reduce voltage."
    draft = extraction.AtomicContractDraft.model_validate({
        "req_code": "REQ-TEST-002",
        "semantic_clauses": [
            {"clause_id": "second", "clause_type": "VERIFICATION", "source_span": "reduce voltage"},
            {"clause_id": "first", "clause_type": "VERIFICATION", "source_span": "isolate power"},
        ],
        "conditions": [
            {"condition_id": "voltage", "description": "Reduce voltage", "source_span": "reduce voltage",
             "source_parameter": "voltage", "canonical_parameter": "reduced_voltage", "operator": "==",
             "threshold": True, "clause_ids": ["second"]},
            {"condition_id": "isolation", "description": "Isolate power", "source_span": "isolate power",
             "source_parameter": "power", "canonical_parameter": "power_isolated", "operator": "==",
             "threshold": True, "clause_ids": ["first"]},
        ],
        "logic_nodes": [
            {"node_id": "root", "operator": "ANY_OF", "child_ids": ["left", "right"]},
            {"node_id": "left", "operator": "CONDITION", "condition_id": "isolation"},
            {"node_id": "right", "operator": "CONDITION", "condition_id": "voltage"},
        ],
        "root_node_id": "root",
        "decomposition_confidence": .9,
    })
    compiled = extraction._compile_atomic_contract_draft(
        draft,
        extraction.DiscoveredRequirement(req_code="REQ-TEST-002", title="Power", description=source),
        source,
    )
    normalized = extraction._normalize_extracted_requirements(
        [compiled], source_by_code={"REQ-TEST-002": source}
    )[0]
    assert [item.canonical_parameter for item in normalized.conditions] == ["power_isolated", "reduced_voltage"]
    assert [item.condition_id for item in normalized.conditions] == ["C1", "C2"]
    assert normalized.logic_tree == {
        "operator": "ANY_OF",
        "children": [leaf("C1"), leaf("C2")],
    }
    assert normalized.contract_complete


def test_misnested_legacy_conditions_are_salvaged_without_an_llm_retry():
    source = "Voltage shall not exceed 30 VAC."
    raw = {
        "requirements": [{
            "req_code": "REQ-TEST-003",
            "semantic_clauses": [
                {"clause_id": "CL1", "clause_type": "VERIFICATION", "source_span": source}
            ],
            "clause_coverage": [{"clause_id": "CL1", "clause": source, "condition_ids": ["C9"]}],
            "logic_tree": {
                "operator": "CONDITION", "condition_id": "C9",
                "description": source, "source_span": source, "source_parameter": "Voltage",
                "canonical_parameter": "maximum_ac_voltage", "operator_for_value": "<=",
                "threshold": 30, "unit": "VAC", "clause_ids": ["CL1"],
            },
            "decomposition_confidence": .8,
        }]
    }
    recovered = extraction._salvage_atomic_contract_response(
        json.dumps(raw),
        extraction.DiscoveredRequirement(req_code="REQ-TEST-003", title="Voltage", description=source),
    )
    assert recovered is not None
    compiled = extraction._compile_atomic_contract_draft(
        recovered.requirements[0],
        extraction.DiscoveredRequirement(req_code="REQ-TEST-003", title="Voltage", description=source),
        source,
    )
    normalized = extraction._normalize_extracted_requirements(
        [compiled], source_by_code={"REQ-TEST-003": source}
    )[0]
    assert len(normalized.conditions) == 1
    assert normalized.conditions[0].threshold == 30
    assert normalized.logic_tree == leaf("C1")
    assert normalized.contract_complete


def test_construction_accepts_salvaged_legacy_response_without_second_call(monkeypatch):
    source = "Voltage shall not exceed 30 VAC."
    raw = json.dumps({
        "requirements": [{
            "req_code": "REQ-TEST-004",
            "semantic_clauses": [
                {"clause_id": "CL1", "clause_type": "VERIFICATION", "source_span": source}
            ],
            "clause_coverage": [{"clause_id": "CL1", "clause": source, "condition_ids": ["old"]}],
            "logic_tree": {
                "operator": "CONDITION", "condition_id": "old", "description": source,
                "source_span": source, "source_parameter": "Voltage",
                "canonical_parameter": "maximum_ac_voltage", "operator_for_value": "<=",
                "threshold": 30, "unit": "VAC", "clause_ids": ["CL1"],
            },
            "decomposition_confidence": .8,
        }]
    })
    calls = 0

    async def generate(**kwargs):
        nonlocal calls
        calls += 1
        kwargs["diagnostics"].update(error="legacy tree shape", raw_response=raw)
        return None

    monkeypatch.setattr(extraction, "generate_structured", generate)
    result = asyncio.run(extraction._construct_atomic_contract(
        extraction.DiscoveredRequirement(req_code="REQ-TEST-004", title="Voltage", description=source),
        None, source, doc_name="req.pdf", active_model="test", fallback_model=None,
        thinking_level=None, allow_rule_fallback=False, allow_model_fallback=False,
    ))
    assert calls == 1
    assert result.contract_complete
    assert result.contract_schema_version == "2.0"
    assert result.contract_source_sha256
    assert result.construction_diagnostics[0]["structurally_salvaged"] is True


def test_requirement_contract_calls_use_bounded_parallelism(monkeypatch):
    active = 0
    maximum = 0

    discovered = [
        extraction.DiscoveredRequirement(req_code=f"REQ-{index}", title=f"R{index}", description="State shall be active.")
        for index in range(1, 5)
    ]

    async def construct(item, *_args, **_kwargs):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(.01)
        active -= 1
        return extraction.ExtractedRequirement(
            req_code=item.req_code, title=item.title, description=item.description
        )

    monkeypatch.setattr(extraction, "_discover_requirements", AsyncMock(return_value=discovered))
    monkeypatch.setattr(extraction, "_construct_atomic_contract", construct)
    monkeypatch.setattr(extraction.settings, "ATOMIC_CONTRACT_CONCURRENCY", 2)
    contracts = asyncio.run(extraction._extract_chunk_staged(
        "State shall be active.", doc_name="req.pdf", active_model="test",
        thinking_level=None, chunk_label="1", allow_rule_fallback=False,
        allow_model_fallback=False, construction_mode="direct",
    ))
    assert [item.req_code for item in contracts] == ["REQ-1", "REQ-2", "REQ-3", "REQ-4"]
    assert maximum == 2
