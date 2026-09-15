"""Production invariants using invented requirements, with no model calls."""

import pytest
from pydantic import ValidationError

from app.services import extraction as e
from app.schemas.contract import AtomicConditionContract, RequirementContract, parse_requirement_contract
from app.schemas.verification_result import ConditionVerificationResult
from app.services.verdict_aggregator import (
    _logic_tree_structure_issues, aggregation_blocking_issues, aggregation_input_issues,
    aggregate_condition_statuses,
)
from app.services.verification_reasoner import _condition_line


def leaf(cid):
    return {"operator": "CONDITION", "condition_id": cid}


def draft():
    return e.AtomicContractDraft(
        req_code="REQ-FAN-001",
        conditions=[e.ExtractedCondition(condition_id="C1", description="Fan runs",
            source_span="The fan shall run.", source_parameter="fan", canonical_parameter="fan_running",
            operator="==", threshold=True, clause_ids=["CL1"])],
        semantic_clauses=[e.ExtractedSemanticClause(clause_id="CL1", clause_type="VERIFICATION",
            source_span="The fan shall run.")],
        logic_nodes=[e.AtomicLogicNode(node_id="N1", operator="CONDITION", condition_id="C1")],
        root_node_id="N1",
    )


def test_header_and_unrelated_alternative_do_not_change_local_source():
    text = "Device manual\nREQ-FAN-001 The fan shall run.\nREQ-LAMP-002 Either lamp A or lamp B shall light."
    mapping = {e._requirement_code_from_block(b): b for b in e._requirement_blocks(text)}
    discovered = e.DiscoveredRequirement(req_code="REQ-FAN-001", title="Fan", description="The fan shall run.")
    source = e._discovered_source_block(discovered, mapping, text)
    assert source == "REQ-FAN-001 The fan shall run."
    contract = e._compile_atomic_contract_draft(draft(), discovered, source)
    assert e._contract_validation_issues(contract, source) == []


def test_failed_association_does_not_borrow_other_requirements():
    r = e.DiscoveredRequirement(req_code="REQ-OTHER-003", title="Fan", description="The fan shall run.")
    assert e._discovered_source_block(r, {"REQ-LAMP-002": "lamp text"}, "whole section") == r.description


def test_unknown_operator_rejected_and_unambiguous_alias_normalized():
    with pytest.raises(ValidationError):
        e.ExtractedCondition(operator="shall_spin", threshold=True)
    assert e.ExtractedCondition(operator="is_less_than_or_equal_to", threshold=4).operator == "<="
    assert e.ExtractedCondition(operator="within_range", min_value=2, max_value=4).operator == "between"


def test_coverage_repair_clears_obsolete_missing_id_error():
    d = draft()
    d.clause_coverage = [e.ExtractedClauseCoverage(clause="The fan shall run.", condition_ids=["C1"])]
    assert e._stable_draft_ids(d, "The fan shall run.") == []
    assert d.clause_coverage[0].clause_id == "CL1"


def test_unknown_coverage_reference_is_not_silently_repaired():
    d = draft()
    d.clause_coverage = [e.ExtractedClauseCoverage(clause_id="CL99", clause="invented", condition_ids=["C1"])]
    assert any("CL99" in issue for issue in e._stable_draft_ids(d, "The fan shall run."))


def test_shorthand_graph_references_survive_condition_renumbering():
    d = draft()
    d.conditions[0].condition_id = "C9"
    d.logic_nodes = [e.AtomicLogicNode(node_id="N1", operator="ALL_OF", child_ids=["C9"])]
    r = e.DiscoveredRequirement(req_code=d.req_code, title="Fan", description="The fan shall run.")
    c = e._compile_atomic_contract_draft(d, r, r.description)
    assert c.logic_tree == {"operator": "ALL_OF", "children": [leaf("C1")]}
    assert c.validation_issues == []


@pytest.mark.parametrize("bad", [{"operator": "INVALID"}, leaf("C99"), None])
def test_nested_structural_errors_always_block(bad):
    issues = _logic_tree_structure_issues({"operator": "ALL_OF", "children": [bad, leaf("C1")]}, {"C1"})
    assert issues
    assert aggregation_blocking_issues(issues) == issues
    assert aggregation_blocking_issues([str(issue) for issue in issues])


def test_distinct_actions_are_not_duplicate_predicates():
    d = draft()
    d.conditions.append(d.conditions[0].model_copy(update={"condition_id": "C2", "source_span": "The fan shall remain attached.", "description": "Fan remains attached"}))
    r = e.DiscoveredRequirement(req_code=d.req_code, title="Fan", description="The fan shall run. The fan shall remain attached.")
    c = e._compile_atomic_contract_draft(d, r, r.description)
    assert not any("duplicates another" in issue for issue in e._contract_validation_issues(c, r.description))
    c.conditions[1].source_span = c.conditions[0].source_span
    c.conditions[1].description = c.conditions[0].description
    assert any("duplicates another" in issue for issue in e._contract_validation_issues(c, r.description))


def contract(op="ALL_OF", complete=True):
    return RequirementContract(requirement_id="demo", req_code="REQ-DEMO-001", title="Two obligations",
        contract_complete=complete, atomic_conditions=[AtomicConditionContract(condition_id="C1"), AtomicConditionContract(condition_id="C2")],
        logic_tree={"operator": op, "children": [leaf("C1"), leaf("C2")]})


def results(first, second, valid_first=True):
    return [ConditionVerificationResult(condition_id="C1", status=first, validation_state="VALID" if valid_first else "UNRESOLVED"),
            ConditionVerificationResult(condition_id="C2", status=second, validation_state="UNRESOLVED")]


@pytest.mark.parametrize("op,first,second", [("ALL_OF", "FAILED", "PROVEN"), ("ANY_OF", "PROVEN", "FAILED")])
def test_valid_decisive_branch_does_not_require_unrelated_proof(op, first, second):
    c, rs = contract(op), results(first, second)
    issues = aggregation_input_issues(c, rs)
    assert issues  # unresolved evidence remains visible
    assert not aggregation_blocking_issues(issues)


@pytest.mark.parametrize("op,first,second", [("ALL_OF", "PROVEN", "PROVEN"), ("ANY_OF", "FAILED", "FAILED")])
def test_conclusions_requiring_all_branches_still_block(op, first, second):
    assert aggregation_blocking_issues(aggregation_input_issues(contract(op), results(first, second)))


def test_incomplete_contract_cannot_bypass_evidence_validation():
    assert aggregation_blocking_issues(aggregation_input_issues(contract(complete=False), results("FAILED", "PROVEN")))


def test_unknown_applicability_cannot_establish_consequent_failure():
    c = contract()
    c.logic_tree = {"operator": "IF_THEN", "antecedent": leaf("C2"), "consequent": leaf("C1")}
    c.atomic_conditions[1].condition_role = "APPLICABILITY"
    assert aggregation_blocking_issues(aggregation_input_issues(c, results("FAILED", "PROVEN")))


def test_legacy_unknown_operator_blocks_execution():
    c = contract()
    c.atomic_conditions[0].operator = "shall_spin"
    assert any("unsupported comparison" in s for s in aggregation_blocking_issues(aggregation_input_issues(c, results("FAILED", "PROVEN"))))


def test_relational_operand_reaches_verifier():
    c = parse_requirement_contract("REQ-VERSION-001", "Version", "Reject older versions",
        structured_conditions=[{"condition_id": "C1", "operator": "<", "parameter": "incoming_version", "right_operand": "installed_version"}])
    assert c.atomic_conditions[0].right_operand == "installed_version"
    assert "installed_version" in _condition_line(c.atomic_conditions[0])


def test_missing_numeric_operand_prevents_contract_completeness():
    d = draft()
    d.conditions[0].operator = "<="
    d.conditions[0].threshold = None
    r = e.DiscoveredRequirement(req_code=d.req_code, title="Fan", description="The fan shall run.")
    c = e._compile_atomic_contract_draft(d, r, r.description)
    assert any("without a right operand" in s for s in e._contract_validation_issues(c, r.description))


def test_finalizer_uses_decisive_failure_without_erasing_unresolved_evidence(monkeypatch):
    from app.services import verdict_aggregator as va
    from app.schemas.verification_result import VerificationAnalysisResult
    # Isolate aggregation from evidence auditing, which has its own tests.
    monkeypatch.setattr(va, "audit_condition_evidence", lambda c, rs, *args: rs)
    analysis = VerificationAnalysisResult(status="PARTIAL", confidence=70, reason="Provisional whole-clause interpretation",
        condition_results=results("FAILED", "PROVEN"))
    final = va.finalize_verdict(contract(), analysis, [])
    assert final.status == "CONFLICT"
    assert final.condition_results[1].validation_state == "UNRESOLVED"
    assert final._diagnostics["aggregator_abstained"] is False
    assert final._diagnostics["aggregator_advisory_issues"]


def test_graph_cycles_and_unknown_references_remain_invalid():
    for nodes in ([e.AtomicLogicNode(node_id="N1", operator="ALL_OF", child_ids=["N1"])],
                  [e.AtomicLogicNode(node_id="N1", operator="ALL_OF", child_ids=["missing"])]):
        tree, issues = e._logic_tree_from_nodes(nodes, "N1", {"C1"})
        assert issues
        assert aggregation_blocking_issues(_logic_tree_structure_issues(tree, {"C1"}))
