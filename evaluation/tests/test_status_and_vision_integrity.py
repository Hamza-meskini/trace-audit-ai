"""Regression coverage for malformed statuses and stale visual evidence."""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.config import settings
from app.schemas.enum_normalization import coerce_enum
from app.schemas.contract import RequirementContract, AtomicConditionContract, parse_requirement_contract
from app.schemas.verification_result import ConditionVerificationResult, VerificationAnalysisResult
from app.services.verification_reasoner import _audit_llm_condition_metadata
from app.services import visual_analysis
from app.services.taxonomy import condition_to_verdict


@pytest.mark.parametrize("status", ["scheduled", "planned", "deferred"])
def test_no_execution_alias_is_not_partial_progress(status):
    assert ConditionVerificationResult(condition_id="C1", status=status).status == "UNTESTED"


@pytest.mark.parametrize("status", ["garbage", "", None, "NOT_PROVEN"])
def test_invalid_status_requests_schema_repair_instead_of_missing_verdict(status):
    with pytest.raises(ValidationError):
        ConditionVerificationResult(condition_id="C1", status=status)
    with pytest.raises(ValueError):
        condition_to_verdict(status)


def test_typo_repair_is_unique_and_bounded():
    assert coerce_enum("CONFIRED", ("CONFIRMED", "UNCONFIRMED")) == "CONFIRMED"
    assert coerce_enum("CONFIR", ("CONFIRMED",)) is None
    assert coerce_enum("CAT", ("BAT", "CAR")) is None
    assert ConditionVerificationResult(condition_id="C1", status="PROVEN", subject_identity="CONFIRED").subject_identity == "CONFIRMED"
    assert ConditionVerificationResult(condition_id="C1", status="UNTESTD").status == "UNTESTED"


def test_relevant_but_inconclusive_evidence_survives_execution_metadata():
    contract = RequirementContract(requirement_id="R", req_code="R", title="Test",
        atomic_conditions=[AtomicConditionContract(condition_id="C1", description="Observed outcome")])
    result = VerificationAnalysisResult(status="UNKNOWN", confidence=80, reason="Wrong method",
        condition_results=[ConditionVerificationResult(condition_id="C1", status="INCONCLUSIVE",
            execution_state="NOT_EXECUTED", evidence_ids=["E1"], quote="Simulation only")])
    audited = _audit_llm_condition_metadata(contract, result)
    assert audited.condition_results[0].status == "INCONCLUSIVE"
    assert audited.condition_results[0].validation_state != "VALID"


def test_requirement_subject_precedes_category():
    contract = parse_requirement_contract(req_code="R", title="ECU shall record events", description="", category="Cybersecurity")
    assert contract.scope == "ECU"


def test_stale_visual_description_replaced_and_current_cache_reused(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test")
    monkeypatch.setattr(settings, "GEMINI_VISION_MODEL", "configured-vision-model")
    monkeypatch.setattr(visual_analysis, "_render_figure_png", lambda *_: b"png")
    call = AsyncMock(return_value={"text": "Recorded test value: 5 V", "provider": "gemini", "attempted": []})
    monkeypatch.setattr(visual_analysis, "call_vision_with_fallback", call)
    candidate = {"id": "C", "document_id": "D", "page_number": 1,
        "content": "Original caption\nVISUAL DESCRIPTION: stale claim",
        "metadata": {"block_type": "figure", "visual_analysis": {"status": "complete", "description": "stale claim"}}}
    items = [{"candidate_chunks": [candidate]}]
    first = asyncio.run(visual_analysis.describe_figure_candidates(items, {"D": "unused"}, model="text-model"))
    assert first["vision_analyzed"] == 1
    assert "stale claim" not in candidate["content"]
    assert "stale claim" not in call.await_args.args[0]
    assert call.await_args.kwargs["gemini_model"] == "configured-vision-model"
    assert candidate["content"].count("VISUAL DESCRIPTION:") == 1
    second = asyncio.run(visual_analysis.describe_figure_candidates(items, {"D": "unused"}, model="text-model"))
    assert second["vision_cache_hits"] == 1
    assert call.await_count == 1
    monkeypatch.setattr(settings, "GEMINI_VISION_MODEL", "new-vision-model")
    call.return_value = {"text": "", "provider": "", "attempted": []}
    third = asyncio.run(visual_analysis.describe_figure_candidates(items, {"D": "unused"}, model="text-model"))
    assert third["vision_unavailable"] == 1
    assert candidate["content"] == "Original caption"
