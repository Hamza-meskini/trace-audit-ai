"""Regression tests for the extraction-to-verification benchmark boundary."""

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.services.extraction import (
    ExtractionResult,
    ExtractedCondition,
    ExtractedRequirement,
    _extract_chunk_with_retry,
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
        self.assertEqual(oracle[0]["description"], "oracle requirement text")
        self.assertEqual(oracle[0]["conditions"][0]["condition_id"], "C-001-1")

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
