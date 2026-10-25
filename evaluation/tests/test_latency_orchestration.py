"""Regression tests for bounded concurrency added to the production audit path."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.config import settings
from app.schemas.contract import parse_requirement_contract
from app.schemas.verification_result import (
    BatchVerificationItemResult,
    BatchVerificationResult,
    ConditionVerificationResult,
    VerificationAnalysisResult,
)
from app.services.classification import batch_assess_requirements
from app.services.classification import EvidenceLinkAssessment, RequirementAssessment
from app.services.pipeline import (
    _assessment_cache_payload,
    _cached_assessment,
    _verification_fingerprint,
)
from app.models.requirement import Requirement
from app.services.verification_reasoner import evaluate_batch_verification


def _contract(code: str):
    return parse_requirement_contract(
        req_code=code,
        title="Opening time",
        description="The unit shall open within 20 ms.",
        category="Performance",
        structured_conditions=[{
            "condition_id": f"{code}-C1",
            "description": "Opening time is at most 20 ms",
            "parameter": "opening time",
            "operator": "<=",
            "threshold": 20,
            "unit": "ms",
        }],
    )


def test_citation_grounding_for_batch_items_runs_concurrently():
    contracts = [_contract("REQ-1"), _contract("REQ-2")]
    response = BatchVerificationResult(batch_results=[
        BatchVerificationItemResult(
            req_code=contract.req_code,
            status="SUPPORTED",
            confidence=92,
            condition_results=[ConditionVerificationResult(
                condition_id=contract.atomic_conditions[0].condition_id,
                status="PROVEN",
            )],
            reason="Supported",
        )
        for contract in contracts
    ])

    async def scenario():
        active = 0
        peak = 0
        both_started = asyncio.Event()

        async def ground(_contract, _chunks, _contents, results, _model):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if active == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=1)
            active -= 1
            return results, {}

        with (
            patch.object(settings, "AUDIT_CITATION_CONCURRENCY", 2),
            patch("app.services.verification_reasoner.generate_structured", new=AsyncMock(return_value=response)),
            patch("app.services.verification_reasoner._ground_condition_citations", new=ground),
            patch("app.services.verification_reasoner._semantic_retry_condition_ids", return_value=[]),
            patch("app.services.verification_reasoner._logic_retry_condition_ids", return_value=[]),
            patch("app.services.verification_reasoner.finalize_verdict", side_effect=lambda **kwargs: kwargs["analysis"]),
        ):
            results = await evaluate_batch_verification([
                {"contract": contract, "candidate_chunks": []}
                for contract in contracts
            ], model="test-model")
        return peak, results

    peak, results = asyncio.run(scenario())
    assert peak == 2
    assert set(results) == {"REQ-1", "REQ-2"}


def test_requirement_batches_run_with_bounded_concurrency():
    contracts = [_contract(f"REQ-{index}") for index in range(1, 5)]

    async def scenario():
        active = 0
        peak = 0
        both_started = asyncio.Event()
        callbacks = []

        async def evaluate(batch_items, **_kwargs):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if active == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=1)
            active -= 1
            return {
                item["contract"].req_code: VerificationAnalysisResult(
                    status="SUPPORTED",
                    confidence=90,
                    condition_results=[ConditionVerificationResult(
                        condition_id=item["contract"].atomic_conditions[0].condition_id,
                        status="PROVEN",
                    )],
                    reason="Supported",
                )
                for item in batch_items
            }

        async def on_results(completed):
            callbacks.append(set(completed))

        with (
            patch.object(settings, "AUDIT_VERIFICATION_BATCH_CONCURRENCY", 2),
            patch("app.services.classification._deterministic_prechecks", return_value=(([], [], []), None)),
            patch("app.services.verification_reasoner.evaluate_batch_verification", new=evaluate),
            patch(
                "app.services.classification._finalize_assessment",
                side_effect=lambda contract, *_args, **_kwargs: SimpleNamespace(req_code=contract.req_code),
            ),
        ):
            results = await batch_assess_requirements([
                {
                    "req_code": contract.req_code,
                    "title": contract.title,
                    "description": contract.description,
                    "category": contract.category,
                    "conditions": [condition.model_dump() for condition in contract.atomic_conditions],
                    "candidate_chunks": [],
                }
                for contract in contracts
            ], batch_size=2, on_results=on_results)
        return peak, callbacks, results

    peak, callbacks, results = asyncio.run(scenario())
    assert peak == 2
    assert len(callbacks) == 2
    assert set(results) == {contract.req_code for contract in contracts}


def test_verification_cache_is_versioned_by_the_complete_corpus():
    item = {
        "req_code": "REQ-1",
        "title": "Opening time",
        "description": "The unit shall open within 20 ms.",
        "category": "Performance",
        "conditions": [{"condition_id": "REQ-1-C1", "description": "Opening time"}],
        "candidate_chunks": [{"id": "chunk-1", "content": "Opening time 18 ms", "metadata": {}}],
    }
    first = _verification_fingerprint(
        item, corpus_fingerprint="corpus-a", model="model-a", thinking_level="LOW"
    )
    changed = _verification_fingerprint(
        item, corpus_fingerprint="corpus-b", model="model-a", thinking_level="LOW"
    )
    assert first != changed

    assessment = RequirementAssessment(
        coverage_status="Supported",
        confidence=94,
        review_state="Reviewed",
        ai_analysis="Measured at 18 ms.",
        ai_recommendation="Retain the report.",
        evidence_links=[EvidenceLinkAssessment(
            chunk_id="chunk-1",
            document_name="report.pdf",
            page_number=3,
            quote="Opening time 18 ms",
            status="Supports requirement",
            label="Test report",
        )],
        condition_results=[ConditionVerificationResult(
            condition_id="REQ-1-C1", status="PROVEN"
        )],
        pipeline_diagnostics={"decision_source": "llm"},
    )
    requirement = Requirement(
        id="req-1",
        project_id="project-1",
        req_code="REQ-1",
        title="Opening time",
        extracted_parameters={
            "verification": {
                "input_fingerprint": first,
                "result_cache": _assessment_cache_payload(assessment),
            }
        },
    )
    restored = _cached_assessment(requirement, first)
    assert restored is not None
    assert restored.coverage_status == "Supported"
    assert restored.evidence_links[0].chunk_id == "chunk-1"
    assert restored.pipeline_diagnostics["verification_cache_hit"] is True
    assert _cached_assessment(requirement, changed) is None
