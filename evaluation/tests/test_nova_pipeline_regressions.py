"""Regressions found in the completed Nova run; no live model calls."""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.schemas.evidence_qualification import normalize_entity_scope
from app.schemas.claim import classify_passage_modality
from app.schemas.contract import RequirementContract, AtomicConditionContract, RequirementLogicContract
from app.schemas.verification_result import ConditionVerificationResult
from app.services.verdict_aggregator import aggregate_condition_statuses
from app.services import requirement_visual_recovery as recovery


def test_scope_uses_passage_before_report_domain_and_matches_whole_terms():
    assert normalize_entity_scope("ECU test passed", "Cybersecurity Assurance Report") == "ECU"
    assert normalize_entity_scope("The packet was recorded") == "System"
    assert normalize_entity_scope("Onboard charger test passed", "ECU report") == "OnboardCharger"
    assert normalize_entity_scope("ASIC rating for the BCU") == "ASIC"


def test_test_photograph_does_not_imply_inspection_method():
    assert classify_passage_modality("Photograph of recorded test result", {"verification_basis": "physical_test"}) == "physical_test"
    assert classify_passage_modality("Visual inspection of the connector", {"verification_basis": "physical_test"}) == "inspection"
    assert classify_passage_modality("A photograph") == "unknown"


def test_inconclusive_consequent_is_not_missing_evidence():
    contract = RequirementContract(requirement_id="R", req_code="R", title="Conditional test", raw_text="When A, B",
        atomic_conditions=[AtomicConditionContract(condition_id="A", description="Trigger"), AtomicConditionContract(condition_id="B", description="Outcome")],
        logic=RequirementLogicContract(operator="IF_THEN", if_condition_id="A", then_condition_ids=["B"]))
    results = [ConditionVerificationResult(condition_id="A", description="Trigger", status="UNTESTED"),
               ConditionVerificationResult(condition_id="B", description="Outcome", status="INCONCLUSIVE")]
    assert aggregate_condition_statuses(contract, results)[0] == "UNKNOWN"
    results[1].status = "UNTESTED"
    assert aggregate_condition_statuses(contract, results)[0] == "MISSING"


def test_recovery_retries_moderation_output(monkeypatch):
    monkeypatch.setattr(recovery, "_render_page_png", lambda *_: b"png")
    call = AsyncMock(side_effect=[
        {"text": '{"safe": true}', "provider": "openrouter", "attempted": []},
        {"text": "REQ-1: The indicator shall illuminate red.", "provider": "gemini", "attempted": []}])
    monkeypatch.setattr(recovery, "call_vision_with_fallback", call)
    result = asyncio.run(recovery.recover_requirement_text_from_pages(file_path="unused", model="text",
        chunks=[{"id": "x", "page_number": 1, "content": "caption", "metadata": {"block_type": "figure"}}]))
    assert result["recovered_pages"] == 1
    assert "openrouter" in call.await_args.kwargs["skip_providers"]
    assert "safe" not in result["text"]
