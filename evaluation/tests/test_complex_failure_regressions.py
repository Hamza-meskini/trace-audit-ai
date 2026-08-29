"""Regression coverage for the eleven failures observed in complex benchmark run 3."""

import json
import sys
import unittest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "backend"))

from app.schemas.claim import classify_source_authority, extract_all_evidence_claims, extract_claims_from_chunk
from app.schemas.contract import parse_requirement_contract
from app.schemas.evidence_qualification import extract_parameters_from_text
from app.schemas.verification_result import ConditionVerificationResult, VerificationAnalysisResult
from app.services.classification import (
    _deterministic_prechecks,
    assess_requirement_coverage,
    batch_assess_requirements,
)
from app.services.evidence_qualification import qualify_evidence, qualify_evidence_chunks
from app.services.verdict_aggregator import (
    aggregate_condition_statuses,
    condition_attribution_is_traceable,
    condition_results_from_claims,
    finalize_verdict,
)
from app.services.verification_reasoner import (
    _repair_contradicted_conditions,
    _reconcile_llm_conditions_with_deterministic_facts,
    rule_based_multi_condition_verification,
)


ROOT = Path(__file__).resolve().parents[1] / "complex_benchmark"
REQUIREMENTS = {
    item["requirement_id"]: item
    for item in json.loads((ROOT / "requirements.json").read_text(encoding="utf-8"))
}


def contract_for(code: str):
    req = REQUIREMENTS[code]
    return parse_requirement_contract(
        req_code=code,
        title=req["title"],
        description=req["requirement_text"],
        category=req["category"],
        structured_conditions=req["conditions"],
    )


def chunk(document_name: str, content: str, doc_type: str = "Test report") -> dict:
    return {
        "id": f"{document_name}-1",
        "document_name": document_name,
        "doc_type": doc_type,
        "page_number": 1,
        "content": content,
    }


class TestObservedComplexBenchmarkFailures(unittest.TestCase):
    def test_structured_contracts_preserve_all_atomic_conditions(self):
        expected_counts = {
            "REQ-AUT-014": 3,
            "REQ-AUT-034": 3,
            "REQ-AUT-063": 2,
            "REQ-AUT-071": 2,
            "REQ-AUT-072": 2,
            "REQ-AUT-091": 2,
        }
        for code, count in expected_counts.items():
            with self.subTest(code=code):
                contract = contract_for(code)
                self.assertEqual(len(contract.atomic_conditions), count)
                self.assertEqual(
                    [c.condition_id for c in contract.atomic_conditions],
                    [c["condition_id"] for c in REQUIREMENTS[code]["conditions"]],
                )

    def test_req_011_ignores_unrelated_five_second_datasheet_rating(self):
        contract = contract_for("REQ-AUT-011")
        candidates = [
            chunk(
                "08_BMS_Functional_Safety_Validation_Report.pdf",
                "TC-INV-011 Phase Overcurrent: peak current 680 A; gates disabled in 1100 ns (1.1 µs). Verdict: PASS.",
            ),
            chunk(
                "06_Traction_Inverter_IGBT_Module_Datasheet.pdf",
                "Peak pulse output current: 220 A for <= 5 seconds.",
            ),
        ]
        claims = extract_all_evidence_claims(candidates, contract)
        self.assertFalse(any(c.unit == "seconds" and c.value == 5.0 for c in claims))
        self.assertTrue(any(c.unit in ("ns", "µs") for c in claims))

    def test_req_014_resolver_evidence_uses_inverter_scope(self):
        contract = contract_for("REQ-AUT-014")
        qualification = qualify_evidence(
            contract,
            "E1",
            "08_BMS_Functional_Safety_Validation_Report.pdf",
            "TC-INV-014 Resolver tracking error measured at 25°C up to 15000 rpm was 0.14 degrees; 20000 rpm and 105°C pending.",
        )
        self.assertEqual(contract.scope, "Inverter")
        self.assertNotEqual(qualification.qualification_status, "NOT_QUALIFIED")

    def test_req_018_not_started_wins_over_unrelated_pass(self):
        contract = contract_for("REQ-AUT-018")
        candidates = [
            chunk(
                "20_Master_Compliance_Verification_Matrix.xlsx",
                "Requirement ID: REQ-AUT-018; Verification Status: NOT STARTED; thermal bench awaiting flow meter calibration.",
                "Compliance matrix",
            ),
            chunk("19_OnBoard_Charger_AC_Validation_Report.pdf", "Thermal cutoff test completed. Verdict: PASS."),
        ]
        _, decided = _deterministic_prechecks(contract, candidates)
        self.assertIsNotNone(decided)
        self.assertEqual(decided.coverage_status, "Missing")

    def test_req_020_architecture_and_matrix_are_not_technical_proof(self):
        contract = contract_for("REQ-AUT-020")
        candidates = [
            chunk(
                "20_Master_Compliance_Verification_Matrix.xlsx",
                "Requirement ID: REQ-AUT-020; architecture description only; Verdict: REVIEW REQUIRED.",
                "Compliance matrix",
            ),
            chunk("02_System_Architecture_Interface_Spec.pdf", "FOC flux weakening vector control block architecture and equations."),
        ]
        result = rule_based_multi_condition_verification(contract, candidates)
        self.assertEqual(result.status, "UNKNOWN")

    def test_req_034_omitted_load_condition_cannot_become_supported(self):
        contract = contract_for("REQ-AUT-034")
        returned = [
            ConditionVerificationResult(condition_id=contract.atomic_conditions[0].condition_id, status="PROVEN"),
            ConditionVerificationResult(condition_id=contract.atomic_conditions[1].condition_id, status="PROVEN"),
        ]
        status, _, _ = aggregate_condition_statuses(contract, returned)
        self.assertEqual(status, "PARTIAL")

    def test_req_063_in_progress_wins_over_unrelated_security_pass(self):
        contract = contract_for("REQ-AUT-063")
        candidates = [
            chunk(
                "20_Master_Compliance_Verification_Matrix.xlsx",
                "Requirement ID: REQ-AUT-063; Verification Status: IN PROGRESS; Level 0x01 validated, Level 0x03 pending.",
                "Compliance matrix",
            ),
            chunk("17_Cybersecurity_HSM_SecOC_Validation_Report.pdf", "Secure boot authentication benchmark. Verdict: PASS."),
        ]
        _, decided = _deterministic_prechecks(contract, candidates)
        self.assertIsNotNone(decided)
        self.assertEqual(decided.coverage_status, "Partial")

        context, deferred = _deterministic_prechecks(
            contract, candidates, defer_partial=True
        )
        self.assertIsNone(deferred)
        self.assertIsNotNone(context)

    def test_batch_reasoner_enriches_partial_conditions_without_closing_workflow(self):
        contract = contract_for("REQ-AUT-063")
        candidates = [chunk(
            "20_Master_Compliance_Verification_Matrix.xlsx",
            "Requirement ID: REQ-AUT-063; Verification Status: IN PROGRESS; "
            "Level 0x01 validated, Level 0x03 pending.",
            "Compliance matrix",
        )]
        reasoner_result = VerificationAnalysisResult(
            status="SUPPORTED",
            confidence=95,
            condition_results=[ConditionVerificationResult(
                condition_id=condition.condition_id,
                description=condition.description,
                status="PROVEN",
            ) for condition in contract.atomic_conditions],
            reason="The model considered every condition complete.",
        )

        async def run_scenario():
            mocked = AsyncMock(return_value={contract.req_code: reasoner_result})
            with patch("app.services.verification_reasoner.evaluate_batch_verification", mocked):
                return await batch_assess_requirements([{
                    "req_code": contract.req_code,
                    "title": contract.title,
                    "description": contract.description,
                    "category": contract.category,
                    "conditions": [condition.model_dump() for condition in contract.atomic_conditions],
                    "candidate_chunks": candidates,
                }])

        assessments = asyncio.run(run_scenario())
        assessment = assessments[contract.req_code]
        self.assertEqual(assessment.coverage_status, "Partial")
        self.assertTrue(all(result.status == "PROVEN" for result in assessment.condition_results))
        self.assertIn("remains IN PROGRESS", assessment.ai_analysis)

    def test_req_071_watchdog_passage_qualifies_at_local_scope(self):
        contract = contract_for("REQ-AUT-071")
        q = qualify_evidence(
            contract,
            "E1",
            "08_BMS_Functional_Safety_Validation_Report.pdf",
            "TC-SAF-071 Window Watchdog: SPI challenge-response verified at 15.2 ms. Verdict: PASS.",
        )
        self.assertEqual(q.qualification_status, "QUALIFIED")

    def test_req_072_brownout_passage_qualifies_at_local_scope(self):
        contract = contract_for("REQ-AUT-072")
        q = qualify_evidence(
            contract,
            "E1",
            "08_BMS_Functional_Safety_Validation_Report.pdf",
            "TC-SAF-072 Core Brownout: Vcore reduced to 2.94 V and PMIC RESET asserted in 2.1 µs. Verdict: PASS.",
        )
        self.assertEqual(q.qualification_status, "QUALIFIED")

    def test_req_078_not_started_cannot_use_cybersecurity_timing(self):
        contract = contract_for("REQ-AUT-078")
        candidates = [
            chunk(
                "20_Master_Compliance_Verification_Matrix.xlsx",
                "Requirement ID: REQ-AUT-078; Verification Status: NOT STARTED; BIST startup profiling pending.",
                "Compliance matrix",
            ),
            chunk("17_Cybersecurity_HSM_SecOC_Validation_Report.pdf", "AES-128 CMAC generation measured at 28.5 µs. Verdict: PASS."),
        ]
        claims = extract_all_evidence_claims(candidates, contract)
        self.assertFalse(any(c.value == 28.5 for c in claims))
        _, decided = _deterministic_prechecks(contract, candidates)
        self.assertEqual(decided.coverage_status, "Missing")

    def test_req_090_review_required_matrix_is_not_proof(self):
        contract = contract_for("REQ-AUT-090")
        candidates = [
            chunk(
                "20_Master_Compliance_Verification_Matrix.xlsx",
                "Requirement ID: REQ-AUT-090; secure logging event format described; Verdict: REVIEW REQUIRED.",
                "Compliance matrix",
            ),
            chunk("02_System_Architecture_Interface_Spec.pdf", "Secure logging event format and flash partition architecture."),
        ]
        result = rule_based_multi_condition_verification(contract, candidates)
        self.assertEqual(result.status, "UNKNOWN")

    def test_req_091_frequency_and_margin_evidence_qualifies(self):
        contract = contract_for("REQ-AUT-091")
        self.assertEqual(contract.atomic_conditions[0].unit, "MHz")
        q = qualify_evidence(
            contract,
            "E1",
            "11_EMC_Radiated_Immunity_Test_Report.pdf",
            "TC-EMC-091 radiated emissions sweep 150 kHz to 2.5 GHz maintained 6.4 dB margin. Verdict: PASS.",
        )
        self.assertEqual(q.qualification_status, "QUALIFIED")

    def test_pdf_replacement_symbols_preserve_microseconds_and_mixed_unit_ranges(self):
        brownout = rule_based_multi_condition_verification(
            contract_for("REQ-AUT-072"),
            [chunk(
                "08_BMS_Functional_Safety_Validation_Report.pdf",
                "TC-SAF-072 Core Brownout: Vcore reduced to 2.94 V and PMIC RESET asserted in 2.1 �s. Verdict: PASS.",
            )],
        )
        emissions = rule_based_multi_condition_verification(
            contract_for("REQ-AUT-091"),
            [chunk(
                "11_EMC_Radiated_Immunity_Test_Report.pdf",
                "TC-EMC-091 radiated emissions sweep 150 kHz�2.5 GHz; peaks remained 6.4 dB below the Class 4 limit. Verdict: PASS.",
            )],
        )
        self.assertEqual(brownout.status, "SUPPORTED")
        self.assertEqual(emissions.status, "SUPPORTED")

    def test_matrix_excerpt_requires_qualified_referenced_document(self):
        contract = contract_for("REQ-AUT-011")
        candidates = [
            chunk(
                "20_Master_Compliance_Verification_Matrix.xlsx",
                "Requirement ID: REQ-AUT-011; peak current 680 A; gates disabled in 1.1 µs; Verdict: PASS.; "
                "Evidence Document Ref: 08_BMS_Functional_Safety_Validation_Report.pdf",
                "Compliance matrix",
            ),
            chunk(
                "08_BMS_Functional_Safety_Validation_Report.pdf",
                "TC-INV-011 Phase Overcurrent: short circuit reached 680 A; captured PDF line is truncated.",
            ),
        ]
        self.assertEqual(rule_based_multi_condition_verification(contract, candidates).status, "SUPPORTED")

        matrix_only = rule_based_multi_condition_verification(contract, candidates[:1])
        self.assertNotEqual(matrix_only.status, "SUPPORTED")

    def test_thousands_separator_is_not_parsed_as_zero_cycles(self):
        contract = contract_for("REQ-AUT-069")
        candidates = [chunk(
            "13_Thermal_Runaway_Venting_Validation_Report.pdf",
            "NVRAM endurance calculation model estimates 125,000 cycles from a cell fatigue curve.",
        )]
        claims = extract_all_evidence_claims(candidates, contract)
        self.assertTrue(any(c.value == 125000.0 for c in claims))
        self.assertEqual(rule_based_multi_condition_verification(contract, candidates).status, "UNKNOWN")

    def test_generic_water_protection_pass_does_not_prove_pump_dry_run(self):
        contract = contract_for("REQ-AUT-050")
        candidates = [
            chunk("02_System_Architecture_Interface_Spec.pdf", "Water pump dry-run fault detection algorithm is defined in architecture."),
            chunk("10_Mechanical_Vibration_Shock_Report.pdf", "Housing water ingress protection test. Verdict: PASS."),
        ]
        self.assertEqual(rule_based_multi_condition_verification(contract, candidates).status, "UNKNOWN")

    def test_run4_llm_proven_timing_conditions_survive_semantic_validation(self):
        cases = {
            "REQ-AUT-001": (
                "Overvoltage injected at 4.255 V; persistent for 100 ms triggered contactor trip in 14.2 ms.",
                "08_BMS_Functional_Safety_Validation_Report.pdf",
                "TC-BMS-001 Overvoltage injected at 4.255 V; persistent for 100 ms triggered contactor trip in 14.2 ms. Verdict: PASS.",
            ),
            "REQ-AUT-011": (
                "Short-circuit test reached 680 A and all gates disabled in 1100 ns (1.1 µs).",
                "08_BMS_Functional_Safety_Validation_Report.pdf",
                "TC-INV-011 Phase Overcurrent: short-circuit test reached 680 A; captured PDF line is truncated.",
            ),
            "REQ-AUT-022": (
                "Hipot test applied 3000 V AC rms for 60 s; leakage current was 0.38 mA.",
                "18_DC_DC_Converter_Efficiency_Test_Report.pdf",
                "TC-DCDC-022 Hipot test applied 3000 V AC rms for 60 s; leakage current was 0.38 mA. Verdict: PASS.",
            ),
        }

        for code, (quote, report_name, report_text) in cases.items():
            with self.subTest(code=code):
                contract = contract_for(code)
                candidates = [
                    chunk(
                        "20_Master_Compliance_Verification_Matrix.xlsx",
                        f"Requirement ID: {code}; {quote} Verdict: PASS.; Evidence Document Ref: {report_name}",
                        "Compliance matrix",
                    ),
                    chunk(report_name, report_text),
                ]
                qualifications = qualify_evidence_chunks(contract, candidates)
                provisional = VerificationAnalysisResult(
                    status="SUPPORTED",
                    confidence=92,
                    condition_results=[
                        ConditionVerificationResult(
                            condition_id=condition.condition_id,
                            status="PROVEN",
                            evidence_ids=["E1", "E2"],
                            quote=quote,
                            reason="Semantic reasoner mapped the cited measurement to this condition.",
                        )
                        for condition in contract.atomic_conditions
                    ],
                    reason="All atomic conditions are proven.",
                )
                result = finalize_verdict(
                    contract,
                    provisional,
                    qualifications,
                    qualified_contents={
                        "E1": candidates[0]["content"],
                        "E2": candidates[1]["content"],
                    },
                )
                self.assertEqual(result.status, "SUPPORTED")
                self.assertTrue(all(item.status == "PROVEN" for item in result.condition_results))

    def test_time_parameter_extraction_uses_phrase_context(self):
        persistence = extract_parameters_from_text("fault remained persistent for 100 ms")
        duration = extract_parameters_from_text("hipot voltage was applied for 60 s")
        latency = extract_parameters_from_text("all gates were disabled in 1100 ns")

        self.assertIn("persistence_time", persistence)
        self.assertNotIn("latency", persistence)
        self.assertIn("persistence_time", duration)
        self.assertNotIn("latency", duration)
        self.assertIn("latency", latency)
        self.assertNotIn("persistence_time", latency)

    def test_llm_quote_not_present_in_cited_evidence_is_rejected(self):
        contract = contract_for("REQ-AUT-011")
        content = "TC-INV-011 measured phase current at 680 A. Verdict: PASS."
        q = qualify_evidence(contract, "E1", "08_BMS_Functional_Safety_Validation_Report.pdf", content)
        provisional = VerificationAnalysisResult(
            status="SUPPORTED",
            confidence=90,
            condition_results=[ConditionVerificationResult(
                condition_id="C-011-2",
                status="PROVEN",
                evidence_ids=["E1"],
                quote="All gates disabled in 1.1 µs.",
            )],
            reason="Latency is proven.",
        )
        result = finalize_verdict(
            contract,
            provisional,
            [q],
            qualified_contents={"E1": content},
        )
        self.assertNotEqual(result.status, "SUPPORTED")
        self.assertEqual(result.condition_results[0].status, "INCONCLUSIVE")

    def test_run5_heuristic_qualification_flags_without_overriding_semantics(self):
        cases = {
            "REQ-AUT-009": (
                "13_Thermal_Runaway_Venting_Validation_Report.pdf",
                "Acoustic sensor baseline signal characterized in bench chamber, but vehicle enclosure discrimination cannot be confirmed.",
            ),
            "REQ-AUT-089": (
                "08_BMS_Functional_Safety_Validation_Report.pdf",
                "TARA analysis evaluates 42 threat vectors and assigns theoretical CAL ratings.",
            ),
        }
        for code, (doc_name, analytical_text) in cases.items():
            with self.subTest(code=code):
                contract = contract_for(code)
                candidates = [
                    chunk(
                        "20_Master_Compliance_Verification_Matrix.xlsx",
                        f"Requirement ID: {code}; review required; {analytical_text}",
                        "Compliance matrix",
                    ),
                    chunk(doc_name, analytical_text),
                ]
                qualifications = qualify_evidence_chunks(contract, candidates)
                provisional = VerificationAnalysisResult(
                    status="PARTIAL",
                    confidence=88,
                    condition_results=[ConditionVerificationResult(
                        condition_id=contract.atomic_conditions[0].condition_id,
                        status="PENDING",
                        evidence_ids=["E1", "E2"],
                        quote=analytical_text,
                    )],
                    reason="Partial verification claimed.",
                )
                result = finalize_verdict(
                    contract,
                    provisional,
                    qualifications,
                    qualified_contents={
                        "E1": candidates[0]["content"],
                        "E2": candidates[1]["content"],
                    },
                )
                self.assertEqual(result.status, "PARTIAL")
                self.assertEqual(result.condition_results[0].status, "PENDING")
                self.assertEqual(result.condition_results[0].validation_state, "UNRESOLVED")

    def test_req_041_nominal_tolerance_and_load_are_composite_numeric_proof(self):
        result = rule_based_multi_condition_verification(
            contract_for("REQ-AUT-041"),
            [chunk(
                "13_Thermal_Runaway_Venting_Validation_Report.pdf",
                "TC-THM-041: Thermal bench test: inlet coolant temperature stabilized at 25.4°C "
                "under 5.2 kW steady thermal dissipation. Verdict: PASS.",
            )],
        )
        self.assertEqual(result.status, "SUPPORTED")
        self.assertTrue(all(item.status == "PROVEN" for item in result.condition_results))

    def test_req_042_missing_llm_citation_is_repaired_from_traceable_numeric_claim(self):
        contract = contract_for("REQ-AUT-042")
        candidates = [chunk(
            "13_Thermal_Runaway_Venting_Validation_Report.pdf",
            "TC-THM-042: Compressor speed sweep from 1000 rpm to 8500 rpm verified; "
            "maximum observed speed error was 28 rpm. Verdict: PASS.",
        )]
        qualifications = qualify_evidence_chunks(contract, candidates)
        provisional = VerificationAnalysisResult(
            status="SUPPORTED",
            confidence=92,
            condition_results=[
                ConditionVerificationResult(
                    condition_id=condition.condition_id,
                    status="PROVEN",
                    evidence_ids=[] if condition.condition_id == "C-042-2" else ["E1"],
                    quote=None if condition.condition_id == "C-042-2" else candidates[0]["content"],
                )
                for condition in contract.atomic_conditions
            ],
            reason="All conditions proven.",
        )
        repaired = _reconcile_llm_conditions_with_deterministic_facts(
            contract, provisional, candidates, qualifications
        )
        maximum = next(item for item in repaired.condition_results if item.condition_id == "C-042-2")
        self.assertEqual(maximum.evidence_ids, ["E1"])
        self.assertIn("8500 rpm", maximum.quote)

    def test_req_082_paraphrased_llm_quote_is_replaced_by_exact_numeric_evidence(self):
        contract = contract_for("REQ-AUT-082")
        candidates = [chunk(
            "17_Cybersecurity_HSM_SecOC_Validation_Report.pdf",
            "TC-SEC-082: measured AES-128 CMAC generation time was 28.5 µs per message. Verdict: PASS.",
        )]
        qualifications = qualify_evidence_chunks(contract, candidates)
        provisional = VerificationAnalysisResult(
            status="SUPPORTED",
            confidence=94,
            condition_results=[ConditionVerificationResult(
                condition_id="C-082-1",
                status="PROVEN",
                evidence_ids=["E1"],
                quote="The measured CMAC duration satisfied the required timing limit.",
                reason="The timing condition was met.",
            )],
            reason="CMAC timing was verified.",
        )

        repaired = _reconcile_llm_conditions_with_deterministic_facts(
            contract, provisional, candidates, qualifications
        )
        self.assertEqual(repaired.condition_results[0].status, "PROVEN")
        self.assertEqual(repaired.condition_results[0].evidence_ids, ["E1"])
        self.assertIn("28.5 µs", repaired.condition_results[0].quote)

        final = finalize_verdict(
            contract,
            repaired,
            qualifications,
            qualified_contents={"E1": candidates[0]["content"]},
            has_relevant_evidence=True,
        )
        self.assertEqual(final.status, "SUPPORTED")

    def test_matrix_mirror_cannot_cross_wire_report_evidence_id_and_quote(self):
        contract = contract_for("REQ-AUT-082")
        candidates = [
            chunk(
                "20_Master_Compliance_Verification_Matrix.xlsx",
                "Requirement ID: REQ-AUT-082; Verification Status: COMPLETE; "
                "Observed Value: measured AES-128 CMAC generation time was 28.5 µs per message; "
                "Evidence Document Ref: 17_Cybersecurity_HSM_SecOC_Validation_Report.pdf; Verdict: PASS.",
                "Compliance matrix",
            ),
            chunk(
                "17_Cybersecurity_HSM_SecOC_Validation_Report.pdf",
                "TC-SEC-082: measured AES-128 CMAC generation time was 28.5 µs per message. Verdict: PASS.",
            ),
        ]
        qualifications = qualify_evidence_chunks(contract, candidates)
        claims = extract_all_evidence_claims(candidates, contract)
        deterministic = condition_results_from_claims(contract, claims, qualifications)[0]
        evidence_contents = {
            "E1": candidates[0]["content"],
            "E2": candidates[1]["content"],
        }

        self.assertEqual(deterministic.status, "PROVEN")
        self.assertEqual(deterministic.evidence_ids, ["E2"])
        self.assertIn("TC-SEC-082", deterministic.quote)
        self.assertTrue(condition_attribution_is_traceable(deterministic, evidence_contents))

        provisional = VerificationAnalysisResult(
            status="SUPPORTED",
            confidence=95,
            condition_results=[ConditionVerificationResult(
                condition_id="C-082-1",
                status="PROVEN",
                evidence_ids=["E2"],
                quote="TC-SEC-082: measured AES-128 CMAC generation time was 28.5 µs per message.",
            )],
            reason="Supported by the empirical report.",
        )
        repaired = _reconcile_llm_conditions_with_deterministic_facts(
            contract, provisional, candidates, qualifications
        )
        self.assertEqual(repaired.condition_results[0].evidence_ids, ["E2"])
        self.assertIn("TC-SEC-082", repaired.condition_results[0].quote)

    def test_req_070_architecture_and_negative_wording_cannot_bypass_qualification(self):
        contract = contract_for("REQ-AUT-070")
        candidates = [
            chunk(
                "20_Master_Compliance_Verification_Matrix.xlsx",
                "Requirement ID: REQ-AUT-070; Section 9.2 specifies negative response code handling; Verdict: REVIEW REQUIRED.",
                "Compliance matrix",
            ),
            chunk(
                "02_System_Architecture_Interface_Spec.pdf",
                "Section 9.2 specifies NRC 0x12 subFunctionNotSupported handling tables.",
            ),
        ]
        assessment = assess_requirement_coverage(
            req_code=contract.req_code,
            title=contract.title,
            description=contract.description,
            category=contract.category,
            candidate_chunks=candidates,
            conditions=[item.model_dump() for item in contract.atomic_conditions],
        )
        self.assertEqual(assessment.coverage_status, "Unknown")
        self.assertTrue(
            all(result.status == "INCONCLUSIVE" for result in assessment.condition_results)
        )

    def test_numeric_parser_miss_does_not_override_semantic_condition(self):
        contract = contract_for("REQ-AUT-030")
        candidates = [chunk(
            "13_Thermal_Runaway_Venting_Validation_Report.pdf",
            "PTC heater soft-start took 32.4 seconds to reach 5.4 kW. Verdict: PASS.",
        )]
        qualifications = qualify_evidence_chunks(contract, candidates)
        provisional = VerificationAnalysisResult(
            status="SUPPORTED",
            confidence=92,
            condition_results=[ConditionVerificationResult(
                condition_id="C-030-1",
                status="PROVEN",
                evidence_ids=["E1"],
                quote=candidates[0]["content"],
                reason="The model confused a 5.4 kW heater value with a 5 A inrush limit.",
            )],
            reason="Inrush was claimed as supported.",
        )

        reconciled = _reconcile_llm_conditions_with_deterministic_facts(
            contract,
            provisional,
            candidates,
            qualifications,
        )

        result = reconciled.condition_results[0]
        self.assertEqual(result.status, "PROVEN")
        self.assertEqual(result.semantic_status, "PROVEN")
        self.assertEqual(result.validation_state, "UNRESOLVED")
        self.assertIn("parser could not independently confirm", result.validation_notes[0])

    def test_pending_target_is_not_extracted_as_an_observed_numeric_claim(self):
        contract = contract_for("REQ-AUT-014")
        claims = extract_claims_from_chunk(
            chunk(
                "motor_speed_validation.pdf",
                "Resolver rotor angle tracking operation was tested to 15,000 rpm; "
                "extension to 20,000 rpm is pending.",
            ),
            contract,
        )

        observed_points = [
            value
            for claim in claims
            for value in (
                claim.discrete_points
                or ([claim.value] if isinstance(claim.value, (int, float)) and not isinstance(claim.value, bool) else [])
            )
        ]
        self.assertIn(15000.0, observed_points)
        self.assertNotIn(20000.0, observed_points)

    def test_structured_observation_flags_cross_parameter_unit_confusion(self):
        contract = contract_for("REQ-AUT-030")
        candidates = [chunk(
            "heater_validation.pdf",
            "PTC heater soft-start reached 5.4 kW. Verdict: PASS.",
        )]
        result = VerificationAnalysisResult(
            status="SUPPORTED",
            confidence=90,
            condition_results=[ConditionVerificationResult(
                condition_id="C-030-1",
                status="PROVEN",
                observed_parameter="heater_power",
                observed_value="5.4",
                observed_unit="kW",
                evidence_value_role="OBSERVED",
                relationship="SATISFIES",
                evidence_ids=["E1"],
                quote=candidates[0]["content"],
            )],
            reason="Claimed inrush compliance.",
        )

        reconciled = _reconcile_llm_conditions_with_deterministic_facts(
            contract,
            result,
            candidates,
            qualify_evidence_chunks(contract, candidates),
        )

        condition = reconciled.condition_results[0]
        self.assertEqual(condition.status, "PROVEN")
        self.assertEqual(condition.validation_state, "CONTRADICTED")
        self.assertIn("incompatible", " ".join(condition.validation_notes))

    def test_focused_semantic_repair_not_python_chooses_replacement_status(self):
        contract = contract_for("REQ-AUT-030")
        candidates = [chunk(
            "heater_validation.pdf",
            "PTC heater soft-start reached 5.4 kW. Verdict: PASS.",
        )]
        qualifications = qualify_evidence_chunks(contract, candidates)
        analysis = VerificationAnalysisResult(
            status="SUPPORTED",
            confidence=90,
            condition_results=[ConditionVerificationResult(
                condition_id="C-030-1",
                status="PROVEN",
                semantic_status="PROVEN",
                validation_state="CONTRADICTED",
                validation_notes=["Observed unit 'kW' is incompatible with required unit 'A'."],
                evidence_ids=["E1"],
                quote=candidates[0]["content"],
            )],
            reason="Initial semantic decision.",
        )
        repaired_response = VerificationAnalysisResult(
            status="UNKNOWN",
            confidence=94,
            condition_results=[ConditionVerificationResult(
                condition_id="C-030-1",
                status="INCONCLUSIVE",
                evidence_ids=["E1"],
                quote=candidates[0]["content"],
                evidence_value_role="OBSERVED",
                relationship="NOT_ADDRESSED",
                reason="Heater power does not establish the inrush-current limit.",
            )],
            reason="Focused review corrected the semantic mapping.",
        )

        with patch(
            "app.services.verification_reasoner.generate_structured",
            new=AsyncMock(return_value=repaired_response),
        ):
            repaired = asyncio.run(_repair_contradicted_conditions(
                contract,
                analysis,
                candidates,
                qualifications,
                model="test-model",
                thinking_level="low",
            ))

        self.assertEqual(repaired.condition_results[0].status, "INCONCLUSIVE")
        self.assertTrue(repaired._diagnostics["semantic_repair_performed"])
        self.assertEqual(
            repaired._diagnostics["condition_transitions"][0]["stage"],
            "semantic_repair",
        )

    def test_req_092_jump_start_simulation_phrase_is_a_physical_test(self):
        text = (
            "TC-PWR-092: Jump start simulation: 26.0 V DC applied for 60.0 seconds; "
            "no thermal runaway or parametric drift observed. Verdict: PASS."
        )
        self.assertEqual(
            classify_source_authority("12_Electrical_Transient_Overvoltage_Report.pdf", text),
            "EMPIRICAL_TEST",
        )
        result = rule_based_multi_condition_verification(
            contract_for("REQ-AUT-092"),
            [chunk("12_Electrical_Transient_Overvoltage_Report.pdf", text)],
        )
        self.assertEqual(result.status, "SUPPORTED")

    def test_req_095_water_failure_dominates_earlier_dust_pass(self):
        contract = contract_for("REQ-AUT-095")
        candidates = [chunk(
            "10_Mechanical_Vibration_Shock_Report.pdf",
            "TC-MECH-095: Housing passed IP6X dust test, but water ingress of 4.2 mL was observed "
            "during 1 m submersion due to gasket leakage. Rated as IP65 only. Verdict: FAIL.",
        )]
        claims = extract_all_evidence_claims(candidates, contract)
        verdicts = [claim.test_result for claim in claims if claim.claim_type == "test_verdict"]
        self.assertEqual(verdicts, ["FAIL"])
        self.assertEqual(rule_based_multi_condition_verification(contract, candidates).status, "CONFLICT")


if __name__ == "__main__":
    unittest.main()
