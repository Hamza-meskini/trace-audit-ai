"""Deterministic accuracy levers in the verdict pipeline; no model calls.

Each section covers one lever measured with evaluation/replay_verification.py.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.schemas.contract import AtomicConditionContract, RequirementContract
from app.schemas.evidence_qualification import EvidenceQualification
from app.schemas.verification_result import ConditionVerificationResult, VerificationAnalysisResult
from app.services import verdict_aggregator as va
from app.services.classification import _finalize_assessment
from app.services.validators import ValidationOutcome


REPORT = (
    "| REQ-DEMO-001 Shutdown band | PASS - Executed for REQ-DEMO-001; the recorded result confirms: "
    "Shutdown is commanded at or above 90 deg C. | Bench record |\n"
    "|  | FAIL - Executed for REQ-DEMO-001; the observed result does not satisfy: "
    "Shutdown is not commanded below 85 deg C. |  |"
)
PASS_QUOTE = "PASS - Executed for REQ-DEMO-001; the recorded result confirms: Shutdown is commanded at or above 90 deg C."
FAIL_QUOTE = "FAIL - Executed for REQ-DEMO-001; the observed result does not satisfy: Shutdown is not commanded below 85 deg C."


def leaf(cid):
    return {"operator": "CONDITION", "condition_id": cid}


def make_contract(tree=None, ids=("C1", "C2"), complete=True, **kw):
    return RequirementContract(
        requirement_id="REQ-DEMO-001", req_code="REQ-DEMO-001", title="Shutdown band",
        raw_text="Shutdown shall be commanded at or above 90 deg C and not below 85 deg C.",
        contract_complete=complete,
        atomic_conditions=[AtomicConditionContract(condition_id=cid, description=f"Condition {cid}") for cid in ids],
        logic_tree=tree or {"operator": "ALL_OF", "children": [leaf(cid) for cid in ids]},
        **kw,
    )


def unestablished_scope_qualification(evidence_id="E1"):
    """An authoritative record whose entity scope could not be established."""
    return EvidenceQualification(
        evidence_id=evidence_id, document_name="report.pdf", source_authority="EMPIRICAL_TEST",
        passage_modality="physical_test", relevance_status="RELEVANT", entity_scope="Pack",
        verification_method="physical_test", required_verification_method="physical_test",
        method_compatible=True, scope_compatible=None, parameter_compatible=None,
        is_authoritative=True, qualification_status="PARTIALLY_QUALIFIED",
        reason="Entity scope of the evidence could not be established.",
    )


def analysis(results, provisional="PARTIAL", confidence=85):
    result = VerificationAnalysisResult(
        status=provisional, confidence=confidence, reason="Whole-clause interpretation.",
        condition_results=results,
    )
    result._diagnostics = {
        "decision_source": "llm",
        "llm_provisional_status": provisional,
        "llm_provisional_confidence": confidence,
    }
    return result


def cited(cid, status, quote):
    return ConditionVerificationResult(condition_id=cid, status=status, evidence_ids=["E1"], quote=quote)


def preset(cid, status, state):
    return ConditionVerificationResult(condition_id=cid, status=status, validation_state=state)


def assess(contract, final):
    outcome = ValidationOutcome(
        status=final.status, confidence=float(final.confidence), reason=final.reason,
        highlight=final.highlight, condition_results=final.condition_results,
    )
    return _finalize_assessment(contract, [], outcome, pipeline_diagnostics=dict(final._diagnostics))


# ── Lever 1: an admissibility-only abstention keeps the atomic CONFLICT ──────

@pytest.mark.parametrize("provisional", ["PARTIAL", "MISSING", "CONFLICT"])
@pytest.mark.parametrize("tree", [
    {"operator": "ALL_OF", "children": [leaf("C1"), leaf("C2")]},
    {"operator": "IF_THEN", "antecedent": leaf("C1"), "consequent": leaf("C2")},
])
def test_unresolved_failure_keeps_atomic_conflict_and_stays_under_review(provisional, tree):
    contract = make_contract(tree)
    final = va.finalize_verdict(
        contract,
        analysis([cited("C1", "PROVEN", PASS_QUOTE), cited("C2", "FAILED", FAIL_QUOTE)], provisional),
        [unestablished_scope_qualification()],
        qualified_contents={"E1": REPORT},
    )

    assert [r.validation_state for r in final.condition_results] == ["UNRESOLVED", "UNRESOLVED"]
    assert final.status == "CONFLICT"
    assert final.confidence <= 80
    assert final._diagnostics["atomic_conflict_retained"] is True
    assert final._diagnostics["atomic_advisory_fallback"] is False
    assert final._diagnostics["aggregator_abstained"] is True
    assert final._diagnostics["aggregator_decision"] == "retained_atomic_conflict"
    assert "Mandatory review is required" in final.reason

    # Review-only even when the holistic label agrees and the contract is
    # complete: an unresolved failure can never auto-close.
    assessment = assess(contract, final)
    assert assessment.coverage_status == "Conflict"
    assert assessment.review_state == "Needs review"
    reasons = assessment.pipeline_diagnostics["review_gate"]["reasons"]
    assert "Failure evidence remains unresolved for: C2." in reasons
    assert any(reason.startswith("Deterministic aggregation abstained") for reason in reasons)


def test_malformed_bound_on_a_sibling_keeps_atomic_conflict(monkeypatch):
    monkeypatch.setattr(va, "audit_condition_evidence", lambda contract, results, *args: results)
    contract = make_contract()
    contract.atomic_conditions[0].operator = "between"
    contract.atomic_conditions[0].min_value = 85.0
    final = va.finalize_verdict(
        contract, analysis([preset("C1", "PROVEN", "VALID"), preset("C2", "FAILED", "UNRESOLVED")]), [])

    assert any(getattr(i, "code", None) == "PREDICATE_BOUND" for i in final._diagnostics["aggregator_blocking_issues"])
    assert final.status == "CONFLICT"
    assert final._diagnostics["atomic_conflict_retained"] is True
    assert assess(contract, final).review_state == "Needs review"


def test_contradicted_citation_keeps_the_holistic_fallback():
    contract = make_contract()
    final = va.finalize_verdict(
        contract,
        analysis([cited("C1", "PROVEN", PASS_QUOTE), cited("C2", "FAILED", "The bootloader accepted the image.")]),
        [unestablished_scope_qualification()],
        qualified_contents={"E1": REPORT},
    )

    assert final.condition_results[1].validation_state == "CONTRADICTED"
    assert final._diagnostics["atomic_aggregate_status"] == "CONFLICT"
    assert final.status == "PARTIAL"
    assert final._diagnostics["atomic_advisory_fallback"] is True
    assert final._diagnostics["atomic_conflict_retained"] is False


@pytest.mark.parametrize("defect", ["unsupported_operator", "omitted_condition", "invalid_operator_node"])
def test_structural_blockers_keep_the_holistic_fallback(monkeypatch, defect):
    monkeypatch.setattr(va, "audit_condition_evidence", lambda contract, results, *args: results)
    contract = make_contract(ids=("C1", "C2", "C3"))
    if defect == "unsupported_operator":
        contract.atomic_conditions[0].operator = "shall_spin"
    elif defect == "omitted_condition":
        contract.logic_tree = {"operator": "ALL_OF", "children": [leaf("C1"), leaf("C2")]}
    else:
        contract.logic_tree = {"operator": "ALL_OF", "children": [leaf("C1"), leaf("C2"), {"operator": "INVALID"}]}
    results = [preset("C1", "PROVEN", "UNRESOLVED"), preset("C2", "FAILED", "UNRESOLVED"), preset("C3", "PROVEN", "UNRESOLVED")]
    final = va.finalize_verdict(contract, analysis(results), [])

    assert final._diagnostics["aggregator_abstained"] is True
    assert final.status == "PARTIAL"
    assert final._diagnostics["atomic_conflict_retained"] is False


def test_non_conflict_aggregate_keeps_the_holistic_fallback(monkeypatch):
    monkeypatch.setattr(va, "audit_condition_evidence", lambda contract, results, *args: results)
    contract = make_contract()
    final = va.finalize_verdict(
        contract, analysis([preset("C1", "PROVEN", "UNRESOLVED"), preset("C2", "PROVEN", "UNRESOLVED")]), [])

    assert final._diagnostics["atomic_aggregate_status"] == "SUPPORTED"
    assert final.status == "PARTIAL"
    assert final._diagnostics["atomic_advisory_fallback"] is True
    assert final._diagnostics["atomic_conflict_retained"] is False


def test_untyped_or_operator_issues_are_not_admissibility_only():
    contract = make_contract()
    results = [preset("C1", "PROVEN", "UNRESOLVED"), preset("C2", "FAILED", "UNRESOLVED")]
    assert not va.abstention_is_admissibility_only(contract, results, [])
    assert not va.abstention_is_admissibility_only(
        contract, results, ["condition C2 has FAILED status with UNRESOLVED evidence validation"])
    contract.atomic_conditions[1].operator = "shall_spin"
    issues = va.aggregation_blocking_issues(va.aggregation_input_issues(contract, results))
    assert any(getattr(i, "code", None) == "PREDICATE_OPERATOR" for i in issues)
    assert not va.abstention_is_admissibility_only(contract, results, issues)


# ── Lever 2: bound defects on non-decisive conditions do not block ───────────

def interval_without_upper_bound(contract, index=0):
    contract.atomic_conditions[index].operator = "between"
    contract.atomic_conditions[index].min_value = 0.75
    return contract


@pytest.mark.parametrize("status", ["UNTESTED", "INCONCLUSIVE"])
def test_bound_defect_on_a_non_decisive_condition_is_advisory(monkeypatch, status):
    contract = interval_without_upper_bound(make_contract())
    results = [preset("C1", status, "UNRESOLVED"), preset("C2", "INCONCLUSIVE", "UNRESOLVED")]
    issues = va.aggregation_input_issues(contract, results)

    bound = [issue for issue in issues if getattr(issue, "code", None) == "PREDICATE_BOUND"]
    assert bound and bound[0].blocking is False
    assert va.aggregation_blocking_issues(issues) == []

    monkeypatch.setattr(va, "audit_condition_evidence", lambda contract, results, *args: results)
    final = va.finalize_verdict(contract, analysis(results, provisional="MISSING"), [])
    assert final.status == "UNKNOWN"
    assert final._diagnostics["aggregator_abstained"] is False
    assert "condition C1 has an interval without both bounds" in final._diagnostics["aggregator_advisory_issues"]


@pytest.mark.parametrize("status", ["PROVEN", "FAILED", "PENDING", "NOT_APPLICABLE"])
def test_bound_defect_on_a_decisive_condition_still_blocks(status):
    contract = interval_without_upper_bound(make_contract())
    results = [preset("C1", status, "VALID"), preset("C2", "INCONCLUSIVE", "UNRESOLVED")]
    blocking = va.aggregation_blocking_issues(va.aggregation_input_issues(contract, results))
    assert "condition C1 has an interval without both bounds" in blocking


def test_operator_defect_on_an_untested_condition_still_blocks():
    contract = make_contract()
    contract.atomic_conditions[0].operator = "shall_spin"
    results = [preset("C1", "UNTESTED", "UNRESOLVED"), preset("C2", "INCONCLUSIVE", "UNRESOLVED")]
    blocking = va.aggregation_blocking_issues(va.aggregation_input_issues(contract, results))
    assert any(getattr(issue, "code", None) == "PREDICATE_OPERATOR" for issue in blocking)
