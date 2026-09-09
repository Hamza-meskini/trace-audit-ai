"""Regression tests for the extraction-to-verification benchmark boundary."""

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.services.extraction import (
    ExtractionResult,
    DiscoveredRequirement,
    ExtractedClauseCoverage,
    ExtractedCondition,
    ExtractedRequirement,
    ExtractedRequirementLogic,
    ExtractedSemanticClause,
    RequirementClausePlan,
    RequirementDiscoveryResult,
    RequirementPlanningResult,
    _build_extraction_prompt,
    _canonicalize_logic_tree,
    _contract_validation_issues,
    _extract_chunk_with_retry,
    _extract_chunk_staged,
    _estimated_atomic_obligations,
    _normalize_extracted_requirements,
    _source_requirement_description,
    _semantic_repair_issues,
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


def test_structured_extraction_coerces_null_list_fields():
    result = ExtractionResult.model_validate({
        "requirements": [{
            "req_code": "REQ-NULL-001",
            "title": "Null collection tolerance",
            "description": "The controller shall log the event.",
            "parameters": None,
            "conditions": None,
            "clause_coverage": None,
            "unmapped_obligations": None,
            "logic": {"operator": "ALL_OF", "condition_ids": None, "then_condition_ids": None},
        }]
    })
    item = result.requirements[0]
    assert item.parameters == []
    assert item.conditions == []
    assert item.logic.condition_ids == []
    assert item.logic.then_condition_ids == []


def test_structured_extraction_coerces_null_logic_to_default_all_of():
    result = ExtractionResult.model_validate({
        "requirements": [{
            "req_code": "REQ-NULL-LOGIC-001",
            "title": "Null logic tolerance",
            "description": "Every instrument shall have valid calibration.",
            "logic": None,
        }]
    })

    assert result.requirements[0].logic.operator == "ALL_OF"


def test_source_description_recovery_restores_wrapped_normative_sentence():
    block = """REQ-MET-010 - Instrument traceability
Every instrument used for acceptance data shall be identified by serial number and have calibration
valid within 12 months on the test date.
Page 6 of 7"""

    recovered = _source_requirement_description(block, "REQ-MET-010 - Instrument traceability")

    assert recovered == (
        "Every instrument used for acceptance data shall be identified by serial number and have calibration "
        "valid within 12 months on the test date."
    )


def test_condition_operator_aliases_are_canonicalized():
    assert ExtractedCondition(description="limit", operator="LESS_THAN_OR_EQUAL_TO").operator == "<="
    assert ExtractedCondition(description="state", operator="equal to").operator == "=="
    assert ExtractedCondition(description="range", operator="WITHIN").operator == "between"


def test_extraction_prompt_defines_canonical_atomic_boundaries_with_general_examples():
    prompt = _build_extraction_prompt(
        "REQ-X: The component shall operate as specified.",
        "generic-specification.pdf",
        "section 1",
    )

    assert "one subject, one property or action" in prompt
    assert "Assign sequential IDs C1, C2, C3" in prompt
    assert "Do not split a continuous numeric range" in prompt
    assert "Never create an umbrella condition" in prompt
    assert "Example 7 — performance across a verification envelope" in prompt
    assert "When an action/state and its timing can fail independently" in prompt
    assert "Example 2 — trigger, required state, and independent latency" in prompt
    assert "Example 6 — true alternatives" in prompt
    assert "REQ-NVA" not in prompt


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


class TestStagedAtomicDecomposition(unittest.IsolatedAsyncioTestCase):
    def _plan(self) -> RequirementClausePlan:
        return RequirementClausePlan(
            req_code="REQ-GATE-001",
            semantic_clauses=[
                ExtractedSemanticClause(
                    clause_id="CL1",
                    clause_type="APPLICABILITY",
                    source_span="If charging is connected",
                    subject="charging",
                    predicate="is connected",
                    relationship="IF",
                ),
                ExtractedSemanticClause(
                    clause_id="CL2",
                    clause_type="VERIFICATION",
                    source_span="torque shall remain zero",
                    subject="torque",
                    predicate="shall remain zero",
                    relationship="THEN",
                ),
            ],
            logic_tree={
                "operator": "IF_THEN",
                "antecedent": {"operator": "CONDITION", "clause_id": "CL1"},
                "consequent": {"operator": "CONDITION", "clause_id": "CL2"},
            },
            decomposition_confidence=0.92,
        )

    def _contract(self) -> ExtractedRequirement:
        return ExtractedRequirement(
            req_code="REQ-GATE-001",
            title="Drive-away inhibition",
            description="If charging is connected, torque shall remain zero.",
            conditions=[
                ExtractedCondition(
                    condition_id="C1",
                    condition_role="APPLICABILITY",
                    description="Charging is connected",
                    source_span="If charging is connected",
                    source_parameter="charging connection",
                    canonical_parameter="charging_connected",
                    parameter="charging_connected",
                    operator="==",
                    threshold=True,
                    clause_ids=["CL1"],
                ),
                ExtractedCondition(
                    condition_id="C2",
                    description="Torque remains zero",
                    source_span="torque shall remain zero",
                    source_parameter="torque",
                    canonical_parameter="propulsion_torque",
                    parameter="propulsion_torque",
                    operator="==",
                    threshold=0,
                    clause_ids=["CL2"],
                ),
            ],
            logic=ExtractedRequirementLogic(
                operator="IF_THEN",
                condition_ids=["C1", "C2"],
                if_condition_id="C1",
                then_condition_ids=["C2"],
            ),
            logic_tree={
                "operator": "IF_THEN",
                "antecedent": {"operator": "CONDITION", "condition_id": "C1"},
                "consequent": {"operator": "CONDITION", "condition_id": "C2"},
            },
            clause_coverage=[
                ExtractedClauseCoverage(
                    clause_id="CL1", clause="If charging is connected", condition_ids=["C1"]
                ),
                ExtractedClauseCoverage(
                    clause_id="CL2", clause="torque shall remain zero", condition_ids=["C2"]
                ),
            ],
            contract_complete=True,
            decomposition_confidence=0.9,
            decomposition_method="staged",
        )

    async def test_discovery_planning_and_atomic_construction_are_separate_calls(self):
        source = "REQ-GATE-001: If charging is connected, torque shall remain zero."
        discovered = RequirementDiscoveryResult(requirements=[DiscoveredRequirement(
            req_code="REQ-GATE-001",
            title="Drive-away inhibition",
            description="If charging is connected, torque shall remain zero.",
            category="Safety",
            severity="High",
        )])
        planned = RequirementPlanningResult(plans=[self._plan()])
        constructed = ExtractionResult(requirements=[self._contract()])
        mocked = AsyncMock(side_effect=[discovered, planned, constructed])

        with patch("app.services.extraction.generate_structured", mocked):
            result = await _extract_chunk_staged(
                source,
                doc_name="requirements.pdf",
                active_model="test-model",
                thinking_level=None,
                chunk_label="Section 1/1",
                allow_rule_fallback=False,
                allow_model_fallback=False,
            )

        self.assertEqual(mocked.await_count, 3)
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0].contract_complete)
        self.assertEqual(result[0].validation_issues, [])
        self.assertEqual(result[0].conditions[1].canonical_parameter, "propulsion_torque")
        self.assertEqual(result[0].logic_tree["operator"], "IF_THEN")

    async def test_atomic_stages_use_the_reasoning_model_split(self):
        source = "REQ-GATE-001: If charging is connected, torque shall remain zero."
        discovered = RequirementDiscoveryResult(requirements=[DiscoveredRequirement(
            req_code="REQ-GATE-001",
            title="Drive-away inhibition",
            description="If charging is connected, torque shall remain zero.",
            category="Safety",
            severity="High",
        )])
        mocked = AsyncMock(side_effect=[
            discovered,
            RequirementPlanningResult(plans=[self._plan()]),
            ExtractionResult(requirements=[self._contract()]),
        ])

        with patch("app.services.extraction.generate_structured", mocked):
            result = await _extract_chunk_staged(
                source,
                doc_name="requirements.pdf",
                active_model="system.ai.llama-4-maverick",
                thinking_level="HIGH",
                atomic_model="z-ai/glm-5.3-free",
                atomic_thinking_level="PROVIDER_DEFAULT",
                atomic_fallback_model="system.ai.llama-4-maverick",
                chunk_label="Section 1/1",
                allow_rule_fallback=False,
                allow_model_fallback=False,
            )

        self.assertEqual(len(result), 1)
        self.assertEqual(mocked.await_args_list[0].kwargs["model"], "system.ai.llama-4-maverick")
        self.assertEqual(mocked.await_args_list[1].kwargs["model"], "z-ai/glm-5.3-free")
        self.assertEqual(mocked.await_args_list[2].kwargs["model"], "z-ai/glm-5.3-free")
        self.assertEqual(mocked.await_args_list[1].kwargs["thinking_level"], "PROVIDER_DEFAULT")

    def test_logic_tree_syntax_is_canonicalized_before_validation(self):
        logic = ExtractedRequirementLogic(
            operator="IF_THEN",
            condition_ids=["C1", "C2", "C3"],
            if_condition_id="C1",
            then_condition_ids=["C2", "C3"],
        )
        known = {"C1", "C2", "C3"}
        variants = [
            {"operator": "IF_THEN", "antecedent": "C1", "consequent": {"ALL_OF": ["C2", "C3"]}},
            {"IF_THEN": {"if_condition_id": "C1", "then_condition_ids": ["C2", "C3"]}},
            {"operator": "IF_THEN", "antecedent": {"CONDITION": "C1"}, "consequent": {"operator": "ALL_OF", "condition_ids": ["C2", "C3"]}},
        ]

        for variant in variants:
            canonical = _canonicalize_logic_tree(variant, logic, known)
            self.assertEqual(canonical["operator"], "IF_THEN")
            self.assertEqual(canonical["antecedent"]["condition_id"], "C1")
            self.assertEqual(
                {item["condition_id"] for item in canonical["consequent"]["children"]},
                {"C2", "C3"},
            )

    def test_nonsemantic_validation_does_not_trigger_a_second_llm_repair(self):
        issues = [
            "Condition C1 has no grounded source_span.",
            "Clause CL1 has unmapped numeric value(s): 2.",
            "Condition C2 uses generic parameter 'value'.",
        ]
        self.assertEqual(_semantic_repair_issues(issues), [])

    async def test_schema_invalid_atomic_response_gets_one_correction_retry(self):
        source = "REQ-GATE-001: If charging is connected, torque shall remain zero."
        discovered = RequirementDiscoveryResult(requirements=[DiscoveredRequirement(
            req_code="REQ-GATE-001",
            title="Drive-away inhibition",
            description="If charging is connected, torque shall remain zero.",
            category="Safety",
            severity="High",
        )])
        mocked = AsyncMock(side_effect=[
            discovered,
            RequirementPlanningResult(plans=[self._plan()]),
            None,
            ExtractionResult(requirements=[self._contract()]),
        ])

        with patch("app.services.extraction.generate_structured", mocked):
            result = await _extract_chunk_staged(
                source,
                doc_name="requirements.pdf",
                active_model="test-model",
                thinking_level=None,
                chunk_label="Section 1/1",
                allow_rule_fallback=False,
                allow_model_fallback=False,
            )

        self.assertEqual(mocked.await_count, 4)
        self.assertEqual(len(result), 1)
        self.assertIn("QUALIFIER is permitted only inside semantic_clauses", mocked.await_args_list[3].kwargs["prompt"])

    def test_ungrounded_condition_span_blocks_completeness(self):
        item = self._contract()
        item.semantic_clauses = self._plan().semantic_clauses
        item.conditions[1].source_span = "a sentence that does not exist"

        normalized = _normalize_extracted_requirements(
            [item],
            source_by_code={
                "REQ-GATE-001": "If charging is connected, torque shall remain zero."
            },
        )[0]

        self.assertFalse(normalized.contract_complete)
        self.assertTrue(any("grounded source_span" in issue for issue in normalized.validation_issues))

    def test_structural_validator_rejects_generic_parameters_and_dangling_logic(self):
        item = self._contract()
        item.semantic_clauses = self._plan().semantic_clauses
        item.conditions[1].canonical_parameter = "value"
        item.conditions[1].parameter = "value"
        item.logic_tree["consequent"]["condition_id"] = "C99"

        issues = _contract_validation_issues(
            item,
            "If charging is connected, torque shall remain zero.",
        )

        self.assertTrue(any("generic parameter" in issue for issue in issues))
        self.assertTrue(any("unknown condition" in issue for issue in issues))


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
