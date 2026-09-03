"""Regression tests for the extraction-to-verification benchmark boundary."""

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.services.extraction import (
    ExtractionResult,
    ExtractedClauseCoverage,
    ExtractedCondition,
    ExtractedRequirement,
    ExtractedRequirementLogic,
    _extract_chunk_with_retry,
    _estimated_atomic_obligations,
    _normalize_extracted_requirements,
    _requirement_blocks,
    _requirement_code_from_block,
    _split_text_into_chunks,
)
from evaluation.run_complex_benchmark import (
    _build_pipeline_requirements,
    _count_exactly_matched_conditions,
    _count_semantically_matched_conditions,
    _decomposed_contract_metrics,
    _ground_truth_condition_status,
    _match_extracted_contracts,
)


def requirement(code: str, text: str) -> ExtractedRequirement:
    return ExtractedRequirement(
        req_code=code,
        title=text,
        description=text,
        category="Electrical",
        severity="High",
        conditions=[
            ExtractedCondition(
                condition_id=f"{code}-C1",
                description=text,
                parameter="voltage",
                operator="<=",
                threshold=12.0,
                unit="V",
            )
        ],
        clause_coverage=[
            ExtractedClauseCoverage(
                clause=text,
                condition_ids=[f"{code}-C1"],
            )
        ],
        unmapped_obligations=[],
        contract_complete=True,
    )


class TestRequirementBoundaryChunking(unittest.TestCase):
    def test_explicit_requirements_are_never_cut_between_character_windows(self):
        text = "\n".join(
            f"REQ-AUT-{index:03d}: requirement {index} shall remain complete " + ("x" * 120)
            for index in range(1, 13)
        )
        chunks = _split_text_into_chunks(text, chunk_size=800, max_requirements_per_chunk=5)

        self.assertEqual(sum(chunk.count("REQ-AUT-") for chunk in chunks), 12)
        self.assertTrue(all(chunk.count("REQ-AUT-") <= 5 for chunk in chunks))
        for index in range(1, 13):
            marker = f"REQ-AUT-{index:03d}"
            self.assertEqual(sum(marker in chunk for chunk in chunks), 1)

    def test_non_numeric_identifier_in_numeric_bound_is_recovered_as_none(self):
        condition = ExtractedCondition(
            description="calibration mode",
            min_value="CAL-2",
            max_value="<= 50.0 µs",
        )
        self.assertIsNone(condition.min_value)
        self.assertEqual(condition.max_value, 50.0)

    def test_numeric_parameter_value_is_coerced_without_rejecting_extraction(self):
        from app.services.extraction import ExtractedParameter

        parameter = ExtractedParameter(name="duration", value=30, unit="min")

        self.assertEqual(parameter.value, "30")

    def test_regulatory_clause_headings_are_preserved_as_blocks(self):
        text = (
            "S5.1 Electrolyte spillage shall not exceed 5.0 liters.\n"
            "Continuation text for the same clause.\n"
            "S6.3 Side moving deformable barrier impact shall meet S5.1, S5.2, and S5.3.\n"
            "S7.6.6 If V1 is greater than V2, insert Ro, measure V1 prime, and calculate Ri."
        )

        blocks = _requirement_blocks(text)

        self.assertEqual([_requirement_code_from_block(block) for block in blocks], [
            "S5.1", "S6.3", "S7.6.6",
        ])
        self.assertIn("Continuation text", blocks[0])

    def test_atomic_obligation_estimate_catches_compressed_procedures(self):
        clause = (
            "S7.6.6 If V1 is greater than or equal to V2, insert resistance Ro, "
            "measure V1 prime, calculate total isolation resistance, and divide by working voltage."
        )

        self.assertGreaterEqual(_estimated_atomic_obligations(clause), 4)

    def test_lettered_clause_is_recovered_from_pdf_section_path(self):
        text = (
            "S7.1 Electric energy storage shall be measured.\n"
            "SECTION: 49 CFR 571.305 > S7.1(c) (enhanced display)\n"
            "(c) If the voltage is at least 60 V, the indicator shall display."
        )

        blocks = _requirement_blocks(text)

        self.assertEqual([_requirement_code_from_block(block) for block in blocks], [
            "S7.1", "S7.1(c)",
        ])

    def test_complete_contract_requires_consistent_clause_coverage(self):
        extracted = requirement(
            "REQ-AUT-001",
            "Detect corruption and boot the golden image within three seconds.",
        )

        normalized = _normalize_extracted_requirements([extracted])[0]

        self.assertTrue(normalized.contract_complete)
        self.assertEqual(normalized.unmapped_obligations, [])

    def test_unknown_condition_reference_rejects_completeness_claim(self):
        extracted = requirement("REQ-AUT-001", "Detect corruption.")
        extracted.clause_coverage = [
            ExtractedClauseCoverage(
                clause="Detect corruption",
                condition_ids=["REQ-AUT-001-C99"],
            )
        ]

        normalized = _normalize_extracted_requirements([extracted])[0]

        self.assertFalse(normalized.contract_complete)
        self.assertEqual(normalized.unmapped_obligations, ["Detect corruption"])

    def test_if_then_antecedent_is_normalized_as_applicability(self):
        extracted = ExtractedRequirement(
            req_code="REQ-GATE-001",
            title="Drive-away inhibition",
            description="If charging is connected, torque shall remain zero.",
            conditions=[
                ExtractedCondition(condition_id="C0", description="charging is connected"),
                ExtractedCondition(condition_id="C1", description="torque remains zero"),
            ],
            logic=ExtractedRequirementLogic(
                operator="IF_THEN",
                condition_ids=["C0", "C1"],
                if_condition_id="C0",
                then_condition_ids=["C1"],
            ),
            clause_coverage=[
                ExtractedClauseCoverage(
                    clause="If charging is connected, torque shall remain zero.",
                    condition_ids=["C0", "C1"],
                )
            ],
            contract_complete=True,
        )

        normalized = _normalize_extracted_requirements([extracted])[0]

        self.assertEqual(normalized.conditions[0].condition_role, "APPLICABILITY")
        self.assertEqual(normalized.conditions[1].condition_role, "VERIFICATION")

    def test_applicability_context_does_not_invalidate_obligation_coverage(self):
        extracted = ExtractedRequirement(
            req_code="REQ-CONTEXT-001",
            title="Post-impact isolation",
            description="After impact, isolation shall be at least 500 ohm/V.",
            conditions=[
                ExtractedCondition(
                    condition_id="C0",
                    condition_role="APPLICABILITY",
                    description="after impact",
                ),
                ExtractedCondition(
                    condition_id="C1",
                    description="isolation is at least 500 ohm/V",
                ),
            ],
            logic=ExtractedRequirementLogic(operator="ALL_OF", condition_ids=["C1"]),
            clause_coverage=[
                ExtractedClauseCoverage(
                    clause="isolation shall be at least 500 ohm/V",
                    condition_ids=["C1"],
                )
            ],
            contract_complete=True,
        )

        normalized = _normalize_extracted_requirements([extracted])[0]

        self.assertTrue(normalized.contract_complete)


class TestAdaptiveExtractionRetry(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_large_response_retries_smaller_requirement_groups(self):
        text = "\n".join(
            f"REQ-AUT-{index:03d}: requirement {index} shall be verified."
            for index in range(1, 5)
        )
        first_half = ExtractionResult(requirements=[
            requirement("REQ-AUT-001", "one"),
            requirement("REQ-AUT-002", "two"),
        ])
        second_half = ExtractionResult(requirements=[
            requirement("REQ-AUT-003", "three"),
            requirement("REQ-AUT-004", "four"),
        ])

        mocked = AsyncMock(side_effect=[None, first_half, second_half])
        with patch("app.services.extraction.generate_structured", mocked):
            recovered = await _extract_chunk_with_retry(
                text,
                doc_name="SRS.docx",
                active_model="test-model",
                thinking_level=None,
                chunk_label="Section 1/1",
            )

        self.assertEqual([item.req_code for item in recovered], [
            "REQ-AUT-001", "REQ-AUT-002", "REQ-AUT-003", "REQ-AUT-004",
        ])
        self.assertEqual(mocked.await_count, 3)
        self.assertTrue(all(call.kwargs["max_output_tokens"] == 8192 for call in mocked.await_args_list))

    async def test_valid_but_incomplete_json_retries_omitted_requirement(self):
        text = (
            "REQ-AUT-001: first requirement shall be verified.\n"
            "REQ-AUT-002: second requirement shall be verified."
        )
        incomplete = ExtractionResult(requirements=[requirement("REQ-AUT-001", "first")])
        recovered_second = ExtractionResult(requirements=[requirement("REQ-AUT-002", "second")])
        mocked = AsyncMock(side_effect=[incomplete, recovered_second])

        with patch("app.services.extraction.generate_structured", mocked):
            recovered = await _extract_chunk_with_retry(
                text,
                doc_name="SRS.docx",
                active_model="test-model",
                thinking_level=None,
                chunk_label="Section 1/1",
            )

        self.assertEqual([item.req_code for item in recovered], ["REQ-AUT-001", "REQ-AUT-002"])
        self.assertEqual(mocked.await_count, 2)


class TestBenchmarkRequirementSource(unittest.TestCase):
    def test_end_to_end_mode_uses_extracted_contract_not_ground_truth_contract(self):
        extracted = [requirement("REQ-AUT-001", "extracted voltage clause")]
        ground_truth = [{
            "requirement_id": "REQ-AUT-001",
            "title": "oracle title",
            "requirement_text": "oracle requirement text",
            "category": "Electrical",
            "conditions": [{"condition_id": "C-001-1", "description": "oracle condition"}],
        }]

        end_to_end = _build_pipeline_requirements("end-to-end", extracted, ground_truth)
        oracle = _build_pipeline_requirements("oracle", extracted, ground_truth)

        self.assertEqual(end_to_end[0]["description"], "extracted voltage clause")
        self.assertEqual(end_to_end[0]["conditions"][0]["condition_id"], "REQ-AUT-001-C1")
        self.assertTrue(end_to_end[0]["contract_complete"])
        self.assertEqual(
            end_to_end[0]["clause_coverage"][0]["condition_ids"],
            ["REQ-AUT-001-C1"],
        )
        self.assertEqual(oracle[0]["description"], "oracle requirement text")
        self.assertEqual(oracle[0]["conditions"][0]["condition_id"], "C-001-1")
        self.assertTrue(oracle[0]["contract_complete"])

    def test_condition_recall_is_semantic_not_tied_to_oracle_ids(self):
        extracted = requirement("REQ-AUT-001", "voltage shall not exceed 12 V")
        ground_truth = [{
            "requirement_id": "REQ-AUT-001",
            "conditions": [{
                "condition_id": "C-001-1",
                "description": "maximum voltage",
                "parameter": "voltage",
                "operator": "<=",
                "threshold": 12.0,
                "unit": "V",
            }],
        }]

        self.assertEqual(
            _count_semantically_matched_conditions(ground_truth, {extracted.req_code: extracted}),
            1,
        )
        self.assertEqual(
            _count_exactly_matched_conditions(ground_truth, {extracted.req_code: extracted}),
            1,
        )

        extracted.conditions[0].threshold = 13.0
        self.assertEqual(
            _count_exactly_matched_conditions(ground_truth, {extracted.req_code: extracted}),
            0,
        )

    def test_oracle_evidence_mode_does_not_oracle_the_contract(self):
        extracted = [requirement("REQ-AUT-001", "extracted voltage clause")]
        ground_truth = [{
            "requirement_id": "REQ-AUT-001",
            "title": "oracle title",
            "requirement_text": "oracle requirement text",
            "category": "Electrical",
            "conditions": [{"condition_id": "C-001-1", "description": "oracle condition"}],
        }]

        evidence_oracle = _build_pipeline_requirements("oracle-evidence", extracted, ground_truth)
        combined_oracle = _build_pipeline_requirements("oracle-contracts-evidence", extracted, ground_truth)

        self.assertEqual(evidence_oracle[0]["description"], "extracted voltage clause")
        self.assertEqual(combined_oracle[0]["description"], "oracle requirement text")

    def test_contract_metrics_separate_field_failures(self):
        extracted = requirement("REQ-AUT-001", "phase overcurrent threshold")
        extracted.conditions[0].parameter = "phase overcurrent threshold"
        extracted.conditions[0].operator = "<"
        ground_truth = [{
            "requirement_id": "REQ-AUT-001",
            "conditions": [{
                "condition_id": "C-001-1",
                "description": "current threshold",
                "parameter": "current_threshold",
                "operator": "<=",
                "threshold": 12.0,
                "unit": "V",
            }],
        }]

        result = _decomposed_contract_metrics(
            _match_extracted_contracts(ground_truth, {extracted.req_code: extracted})
        )

        self.assertEqual(result["metrics"]["decomposition_recall"], 100.0)
        self.assertEqual(result["metrics"]["operator_accuracy"], 0.0)
        self.assertEqual(result["metrics"]["threshold_accuracy"], 100.0)
        self.assertEqual(result["metrics"]["full_exact_recall"], 0.0)

    def test_between_min_max_matches_legacy_threshold_string(self):
        extracted = requirement("REQ-AUT-001", "torque operating range")
        extracted.conditions[0] = ExtractedCondition(
            condition_id="C-001-1",
            description="torque operating range",
            parameter="torque_range",
            operator="between",
            min_value=50.0,
            max_value=350.0,
            unit="Nm",
        )
        ground_truth = [{
            "requirement_id": "REQ-AUT-001",
            "conditions": [{
                "condition_id": "C-001-1",
                "description": "torque operating range",
                "parameter": "torque_range",
                "operator": "between",
                "threshold": "50.0-350.0",
                "min_value": "",
                "max_value": "",
                "unit": "Nm",
            }],
        }]

        pairs = _match_extracted_contracts(ground_truth, {extracted.req_code: extracted})
        result = _decomposed_contract_metrics(pairs)

        self.assertEqual(_count_exactly_matched_conditions(
            ground_truth, {extracted.req_code: extracted}
        ), 1)
        self.assertEqual(result["metrics"]["threshold_accuracy"], 100.0)
        self.assertEqual(result["metrics"]["full_exact_recall"], 100.0)
        self.assertEqual(result["mismatches"], [])

    def test_blank_optional_unit_is_not_scored_as_a_failure(self):
        extracted = requirement("REQ-AUT-001", "feature enabled")
        extracted.conditions[0].parameter = "feature_enabled"
        extracted.conditions[0].operator = "=="
        extracted.conditions[0].threshold = True
        extracted.conditions[0].unit = None
        ground_truth = [{
            "requirement_id": "REQ-AUT-001",
            "conditions": [{
                "condition_id": "C-001-1",
                "description": "feature enabled",
                "parameter": "feature_enabled",
                "operator": "==",
                "threshold": True,
                "unit": "",
            }],
        }]

        result = _decomposed_contract_metrics(
            _match_extracted_contracts(ground_truth, {extracted.req_code: extracted})
        )

        self.assertNotIn("unit", result["metrics"]["field_denominators"])
        self.assertEqual(result["mismatches"], [])

    def test_explicit_atomic_truth_overrides_legacy_inference(self):
        requirement_data = {"expected_status": "CONFLICT"}
        link = {"condition_statuses": {"C1": "PROVEN", "C2": "FAILED"}}

        self.assertEqual(
            _ground_truth_condition_status(requirement_data, link, {"condition_id": "C1"}),
            ("PROVEN", "explicit"),
        )
        self.assertEqual(
            _ground_truth_condition_status(requirement_data, link, {"condition_id": "C2"}),
            ("FAILED", "explicit"),
        )

    def test_legacy_atomic_truth_is_marked_inferred(self):
        requirement_data = {"expected_status": "PARTIAL"}
        link = {"missing_conditions": ["C2 (pending test)"]}

        self.assertEqual(
            _ground_truth_condition_status(requirement_data, link, {"condition_id": "C1"}),
            ("PROVEN", "inferred_from_requirement"),
        )
        self.assertEqual(
            _ground_truth_condition_status(requirement_data, link, {"condition_id": "C2"}),
            ("PENDING", "inferred_from_requirement"),
        )


if __name__ == "__main__":
    unittest.main()
