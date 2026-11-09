"""EVA-06 strict probes for the trust holes closed in batch 3.

ACC-03: a citation must be grounded in the evidence the model cited.
ACC-02: a PROVEN result must carry an executed, observed value that is in its quote.
TRU-02: machine auto-closes are not human reviews; comments are not decisions.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_verdict_aggregation import cond, make_contract, qual  # noqa: E402

from app.schemas.verification_result import ConditionVerificationResult
from app.services import review_status
from app.services.classification import _supported_review_gate
from app.services.validators import ValidationOutcome
from app.services.verdict_aggregator import audit_condition_evidence
from app.services.verification_reasoner import _attach_literal_citation_spans


def proven(quote, evidence_ids=("E1",), **fields):
    defaults = dict(execution_state="EXECUTED", relationship="SATISFIES", evidence_value_role="OBSERVED")
    defaults.update(fields)
    return ConditionVerificationResult(
        condition_id="C1", status="PROVEN", evidence_ids=list(evidence_ids), quote=quote, **defaults)


def audit(result, contents, req_code="REQ-NVA-CHG-003", condition=None):
    contract = make_contract(req_code=req_code, conditions=[condition or cond("C1")])
    quals = [qual(eid, "lab.pdf", "QUALIFIED", True, True) for eid in contents]
    return audit_condition_evidence(contract, [result], quals, contents)[0]


LATENCY = cond("C1", parameter="latency", operator="<=", threshold=5.0, unit="ms")


# ── ACC-03: citation grounding ───────────────────────────────────────────────

def test_row_for_another_requirement_cannot_prove_this_one():
    row = "REQ-NVA-CHG-002 | Charge inhibit test | Executed | PASS | 4.1 ms"
    out = audit(proven("Charge inhibit test | Executed | PASS | 4.1 ms"), {"E1": row})
    assert out.status == "INCONCLUSIVE"
    assert out.validation_state == "CONTRADICTED"
    assert "REQ-NVA-CHG-002" in " ".join(out.validation_notes)


def test_row_naming_this_requirement_is_kept():
    row = "REQ-NVA-CHG-003 | Charge inhibit test | Executed | PASS | 4.1 ms"
    out = audit(proven("Charge inhibit test | Executed | PASS | 4.1 ms", observed_value="4.1"),
                {"E1": row}, condition=LATENCY)
    assert out.status == "PROVEN"
    assert out.validation_state != "CONTRADICTED"


def test_quote_found_only_in_uncited_evidence_is_not_repointed():
    quote = "Measured isolation was 1200 ohms per volt after rollover."
    out = audit(proven(quote), {"E1": "A different qualified measurement appears here.",
                                "E2": f"Test result: {quote} Verdict: Passed."})
    assert out.evidence_ids == ["E1"]
    assert out.validation_state == "CONTRADICTED"
    assert "not re-pointed" in " ".join(out.validation_notes)


def test_token_sized_quote_is_not_grounded():
    result = proven("PASS")
    assert not _attach_literal_citation_spans(result, {"E1": "Result: PASS"}, {})
    assert not result.evidence_spans


def test_span_search_is_limited_to_cited_items():
    quote = "Opening time measured at 18 ms on unit 4"
    result = proven(quote, evidence_ids=("E1",))
    assert not _attach_literal_citation_spans(result, {"E1": "unrelated text", "E2": quote}, {})
    assert result.evidence_ids == ["E1"]


def test_literal_span_in_cited_item_is_attached():
    quote = "Opening time measured at 18 ms on unit 4"
    result = proven(quote)
    assert _attach_literal_citation_spans(result, {"E1": f"Results: {quote}."}, {})
    assert result.evidence_spans[0].evidence_id == "E1"


# ── ACC-02: what PROVEN must carry ───────────────────────────────────────────

def test_observed_value_missing_from_quote_is_flagged():
    out = audit(proven("Latency measured 6.2 ms", observed_value="4.2"),
                {"E1": "Latency measured 6.2 ms in run 3"}, condition=LATENCY)
    assert out.status == "PROVEN"  # the model's label stands ...
    assert any("4.2" in flag for flag in out.review_flags)  # ... but cannot auto-close


def test_observed_value_in_quote_is_not_flagged():
    out = audit(proven("Latency measured 4.2 ms", observed_value="4.2"),
                {"E1": "Latency measured 4.2 ms in run 3"}, condition=LATENCY)
    assert out.review_flags == []


def test_partial_execution_is_flagged():
    out = audit(proven("Latency measured 4.2 ms", observed_value="4.2", execution_state="PARTIAL"),
                {"E1": "Latency measured 4.2 ms in run 3"}, condition=LATENCY)
    assert any("execution" in flag for flag in out.review_flags)


def test_status_word_cannot_prove_a_numeric_limit():
    out = audit(proven("Latency test result PASS for unit 4", evidence_value_role="STATUS_ONLY"),
                {"E1": "Latency test result PASS for unit 4"}, condition=LATENCY)
    assert any("status word" in flag for flag in out.review_flags)


def test_planned_value_is_flagged():
    out = audit(proven("Latency shall be verified at 4.2 ms", observed_value="4.2",
                       evidence_value_role="REQUIRED_OR_PLANNED"),
                {"E1": "Latency shall be verified at 4.2 ms"}, condition=LATENCY)
    assert any("planned" in flag for flag in out.review_flags)


def test_flagged_proof_keeps_supported_under_review():
    result = proven("Latency measured 6.2 ms", observed_value="4.2")
    result.review_flags.append("observed value 4.2 is not in the quoted span")
    outcome = ValidationOutcome(status="SUPPORTED", confidence=0.9, reason="r", condition_results=[result])
    reasons = _supported_review_gate(outcome, {}, make_contract(conditions=[LATENCY]))
    assert any("Proof needs human confirmation" in reason for reason in reasons)


# ── TRU-02: honest review labels ─────────────────────────────────────────────

def event(action, **fields):
    return {"id": fields.pop("id", action), "action": action, "reviewer": "ana", "comment": action, **fields}


def test_machine_reviewed_state_is_auto_closed():
    assert review_status.review_label([], "Reviewed") == "Auto-closed"
    assert not review_status.is_human_reviewed([], "Reviewed")


def test_human_mark_reviewed_counts_as_human():
    history = [event("Reviewed")]
    assert review_status.review_label(history, "Reviewed") == "Reviewed"
    assert review_status.is_human_reviewed(history, "Reviewed")


def test_later_comment_is_not_the_decision():
    history = [event("Approved", id="a", human_verdict="Supported"),
               event("Comment", id="c", human_verdict="Conflict")]
    decision = review_status.latest_human_decision(history, "Approved")
    assert decision["id"] == "a" and decision["human_verdict"] == "Supported"
    assert [c["id"] for c in review_status.comments_after(history, decision)] == ["c"]


def test_reaudit_reopen_drops_the_human_decision():
    history = [event("Approved", id="a"), event("Needs review", id="s", system=True)]
    assert review_status.latest_human_decision(history, "Needs review") is None
    assert review_status.review_origin(history, "Needs review") == "re-audit"
    assert not review_status.is_human_reviewed(history, "Needs review")
