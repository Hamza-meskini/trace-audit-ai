"""Focused tests for second-model adjudication and extractive provenance."""

import unittest
from unittest.mock import AsyncMock, patch

from app.schemas.contract import AtomicConditionContract, RequirementContract
from app.schemas.verification_result import (
    CitationGroundingResult,
    CitationSpanCandidate,
    ConditionVerificationResult,
    SemanticAdjudicationResult,
)
from app.services.verification_reasoner import (
    _apply_secondary_adjudication,
    _ground_condition_citations,
)
from app.services.verdict_aggregator import (
    condition_attribution_is_traceable,
    locate_exact_quote_span,
)


def contract(*, visual: bool = False) -> RequirementContract:
    return RequirementContract(
        requirement_id="REQ-1",
        req_code="REQ-1",
        title="Endpoint verification",
        raw_text="The endpoint shall be tested and satisfy the acceptance limit.",
        atomic_conditions=[AtomicConditionContract(
            condition_id="C1",
            description="endpoint satisfies the acceptance limit",
            requires_visual_evidence=visual,
        )],
    )


class TestExactCitationSpans(unittest.IsolatedAsyncioTestCase):
    def test_literal_span_offsets_reproduce_source(self):
        source = "Header\nMeasured endpoint value: 8.2 ms. Result: PASS.\nFooter"
        quote = "Measured endpoint value: 8.2 ms. Result: PASS."

        self.assertEqual(locate_exact_quote_span(quote, source), (7, 53))

    async def test_paraphrase_is_replaced_only_by_verified_literal_span(self):
        source = "Header\nMeasured endpoint value: 8.2 ms. Result: PASS.\nFooter"
        result = ConditionVerificationResult(
            condition_id="C1",
            status="PROVEN",
            evidence_ids=["E1"],
            quote="The endpoint passed at 8.2 ms.",
        )
        response = CitationGroundingResult(citations=[CitationSpanCandidate(
            condition_id="C1",
            evidence_id="E1",
            exact_quote="Measured endpoint value: 8.2 ms. Result: PASS.",
        )])

        with patch(
            "app.services.verification_reasoner.generate_structured",
            new=AsyncMock(return_value=response),
        ):
            results, diagnostics = await _ground_condition_citations(
                contract(),
                [{"document_name": "report.pdf", "page_number": 4, "content": source}],
                {"E1": source},
                [result],
                "system.ai.llama-4-maverick",
            )

        grounded = results[0]
        self.assertEqual(grounded.status, "PROVEN")
        self.assertEqual(grounded.quote, response.citations[0].exact_quote)
        self.assertEqual(grounded.evidence_spans[0].document_name, "report.pdf")
        self.assertEqual(grounded.evidence_spans[0].page_number, 4)
        span = grounded.evidence_spans[0]
        self.assertEqual(source[span.start_offset:span.end_offset], span.exact_quote)
        self.assertTrue(condition_attribution_is_traceable(grounded, {"E1": source}))
        self.assertEqual(diagnostics["citation_grounding_failed_ids"], [])

    async def test_non_literal_grounding_is_rejected_without_status_change(self):
        source = "Measured endpoint value: 8.2 ms. Result: PASS."
        original_quote = "The endpoint passed at 8.2 ms."
        result = ConditionVerificationResult(
            condition_id="C1",
            status="PROVEN",
            evidence_ids=["E1"],
            quote=original_quote,
        )
        response = CitationGroundingResult(citations=[CitationSpanCandidate(
            condition_id="C1",
            evidence_id="E1",
            exact_quote="Endpoint passed with a compliant measured result.",
        )])

        with patch(
            "app.services.verification_reasoner.generate_structured",
            new=AsyncMock(return_value=response),
        ):
            results, diagnostics = await _ground_condition_citations(
                contract(),
                [{"document_name": "report.pdf", "page_number": 4, "content": source}],
                {"E1": source},
                [result],
                "system.ai.llama-4-maverick",
            )

        self.assertEqual(results[0].status, "PROVEN")
        self.assertEqual(results[0].quote, original_quote)
        self.assertEqual(results[0].evidence_spans, [])
        self.assertEqual(diagnostics["citation_grounding_failed_ids"], ["C1"])


class TestSecondaryAdjudicator(unittest.IsolatedAsyncioTestCase):
    async def test_independent_model_resolves_not_executed_failure(self):
        primary = ConditionVerificationResult(
            condition_id="C1",
            status="FAILED",
            execution_state="NOT_EXECUTED",
            evidence_value_role="NOT_ADDRESSED",
            relationship="NOT_ADDRESSED",
            reason="The endpoint was not measured.",
        )
        response = SemanticAdjudicationResult(condition_results=[
            ConditionVerificationResult(
                condition_id="C1",
                status="UNTESTED",
                execution_state="NOT_EXECUTED",
                evidence_value_role="NOT_ADDRESSED",
                relationship="NOT_ADDRESSED",
                reason="The endpoint was explicitly not measured.",
            )
        ])
        mocked = AsyncMock(return_value=response)

        with patch("app.services.verification_reasoner.generate_structured", new=mocked):
            results, diagnostics = await _apply_secondary_adjudication(
                contract(),
                [{"content": "The endpoint was not measured."}],
                [primary],
                "system.ai.llama-4-maverick",
            )

        self.assertEqual(results[0].status, "UNTESTED")
        self.assertEqual(diagnostics["secondary_adjudication_resolved_ids"], ["C1"])
        self.assertNotEqual(
            mocked.await_args.kwargs["model"],
            "system.ai.llama-4-maverick",
        )

    async def test_inconsistent_second_answer_does_not_replace_primary(self):
        primary = ConditionVerificationResult(
            condition_id="C1",
            status="FAILED",
            execution_state="NOT_EXECUTED",
            reason="The endpoint was not measured.",
        )
        response = SemanticAdjudicationResult(condition_results=[
            ConditionVerificationResult(
                condition_id="C1",
                status="FAILED",
                execution_state="NOT_EXECUTED",
                reason="The endpoint was not measured.",
            )
        ])

        with patch(
            "app.services.verification_reasoner.generate_structured",
            new=AsyncMock(return_value=response),
        ):
            results, diagnostics = await _apply_secondary_adjudication(
                contract(),
                [{"content": "The endpoint was not measured."}],
                [primary],
                "system.ai.llama-4-maverick",
            )

        self.assertIs(results[0], primary)
        self.assertEqual(diagnostics["secondary_adjudication_unresolved_ids"], ["C1"])


if __name__ == "__main__":
    unittest.main()
