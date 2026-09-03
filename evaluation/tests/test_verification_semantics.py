"""Unit tests for TraceAudit AI Verification Semantics and Edge Cases.

Covers the 17 core semantic test cases:
1. Simulation evidence -> UNKNOWN for physical requirement
2. Simulation evidence -> accepted when requirement explicitly specifies simulation
3. SPICE simulation with 'pending hardware test' -> UNKNOWN (NOT PARTIAL)
4. Architecture specification -> UNKNOWN
5. Inconclusive/uncalibrated bench test -> UNKNOWN
6. System test PASS overrides component datasheet limit (Entity Scope)
7. Component datasheet limit creates CONFLICT when requirement is scoped to that component
8. Tested range enveloping required range -> SUPPORTED ([380, 820] V for [400, 800] V)
9. Tested range narrower than required -> PARTIAL ([-20, +70] °C for [-40, +85] °C)
10. Tested range completely outside required -> CONFLICT
11. Multi-condition with all conditions proven -> SUPPORTED
12. Multi-condition with one condition pending -> PARTIAL
13. Multi-condition with one condition failed -> CONFLICT
14. PASS verdict on one subclause does NOT override untested conditions
15. Empty evidence -> MISSING
16. Compliance matrix 'NOT STARTED' -> MISSING
17. Compliance matrix 'IN PROGRESS' -> PARTIAL
"""

import unittest
from app.schemas.contract import RequirementContract, AtomicConditionContract, parse_requirement_contract
from app.schemas.claim import EvidenceClaim, classify_source_authority
from app.schemas.verification_result import ConditionVerificationResult
from app.services.verification_reasoner import (
    rule_based_multi_condition_verification,
    aggregate_condition_statuses,
    _semantic_retry_condition_ids,
)
from app.services.validators.numeric_range import validate_numeric_range
from app.services.validators.threshold import validate_threshold
from app.services.contradiction import detect_contract_contradiction
from app.services.retrieval import (
    RetrievedChunk,
    _condition_aware_rerank,
    _expand_structural_context,
    retrieve_candidate_evidence,
)


def retrieved(chunk_id: str, score: float, *, page: int = 1, block_type: str = "text") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id="DOC",
        document_name="report.pdf",
        doc_type="Test report",
        page_number=page,
        content=chunk_id,
        score=score,
        matched_terms=[],
        metadata={"block_type": block_type},
    )


class TestVerificationSemantics(unittest.TestCase):

    def test_condition_rerank_reserves_bounded_atomic_coverage(self):
        pool = [retrieved("global", 10.0), retrieved("c1", 5.0), retrieved("c2", 4.0)]

        selected = _condition_aware_rerank(pool, [["DOC:c1"], ["DOC:c2"]], top_k=3)

        self.assertEqual([item.chunk_id for item in selected], ["global", "c1", "c2"])

    def test_structural_context_attaches_same_page_neighbors_only(self):
        selected = [retrieved("table", 5.0, page=2, block_type="table")]
        chunks = [
            {"id": "header", "document_id": "DOC", "document_name": "report.pdf", "doc_type": "Test report", "page_number": 2, "content": "header", "metadata": {"block_type": "text"}},
            {"id": "table", "document_id": "DOC", "document_name": "report.pdf", "doc_type": "Test report", "page_number": 2, "content": "table", "metadata": {"block_type": "table"}},
            {"id": "result", "document_id": "DOC", "document_name": "report.pdf", "doc_type": "Test report", "page_number": 2, "content": "result", "metadata": {"block_type": "text"}},
            {"id": "other-page", "document_id": "DOC", "document_name": "report.pdf", "doc_type": "Test report", "page_number": 3, "content": "other", "metadata": {"block_type": "text"}},
        ]

        expanded = _expand_structural_context(selected, chunks)

        self.assertEqual([item.chunk_id for item in expanded], ["table", "header", "result"])
        self.assertTrue(all(item.metadata.get("context_only") for item in expanded[1:]))

    def test_semantic_retry_detects_assumption_and_taxonomy_drift(self):
        contract = RequirementContract(
            requirement_id="S6.3",
            req_code="S6.3",
            title="Barrier impact",
            raw_text="The vehicle shall be impacted by a conforming barrier.",
            atomic_conditions=[
                AtomicConditionContract(condition_id="S6.3-C1", description="barrier conformity"),
                AtomicConditionContract(condition_id="S6.3-C2", description="dummy installation"),
            ],
        )
        results = [
            ConditionVerificationResult(condition_id="S6.3-C1", status="PROVEN", reason="The test setup implies conformity."),
            ConditionVerificationResult(condition_id="S6.3-C2", status="INCONCLUSIVE", reason="No evidence was provided."),
        ]

        self.assertEqual(
            _semantic_retry_condition_ids(contract, results, []),
            ["S6.3-C1", "S6.3-C2"],
        )

    def test_semantic_retry_detects_not_executed_failure_and_visual_scope(self):
        contract = RequirementContract(
            requirement_id="REQ-SCOPE",
            req_code="REQ-SCOPE",
            title="Universal visual marking",
            raw_text="Every cover shall carry the warning marking.",
            atomic_conditions=[
                AtomicConditionContract(
                    condition_id="C1",
                    description="cold endpoint verification",
                ),
                AtomicConditionContract(
                    condition_id="C2",
                    description="marking is present on every cover",
                    requires_visual_evidence=True,
                ),
            ],
        )
        results = [
            ConditionVerificationResult(
                condition_id="C1",
                status="FAILED",
                quote="The cold endpoint was not measured.",
            ),
            ConditionVerificationResult(
                condition_id="C2",
                status="PROVEN",
                subject_identity="UNCONFIRMED",
                coverage_scope="SINGLE_ITEM",
            ),
        ]
        evidence = [{"content": "VISUAL DESCRIPTION: one cover is shown without a serial identifier"}]

        self.assertEqual(
            _semantic_retry_condition_ids(contract, results, evidence),
            ["C1", "C2"],
        )

    def test_semantic_retry_rechecks_inconclusive_hard_violation(self):
        contract = RequirementContract(
            requirement_id="REQ-LEAK",
            req_code="REQ-LEAK",
            title="No leakage",
            atomic_conditions=[AtomicConditionContract(condition_id="C1", description="no leakage")],
        )
        results = [ConditionVerificationResult(condition_id="C1", status="INCONCLUSIVE")]
        evidence = [{"content": "Inspection result: VISIBLE LEAKAGE at the quick disconnect."}]

        self.assertEqual(_semantic_retry_condition_ids(contract, results, evidence), ["C1"])

    # Case 1: Simulation evidence -> UNKNOWN for physical requirement
    def test_simulation_evidence_returns_unknown_for_physical_requirement(self):
        contract = parse_requirement_contract(
            req_code="REQ-PHYS-001",
            title="Coolant Flow Rate",
            description="The thermal management pump shall deliver at least 15.0 L/min under maximum thermal load.",
            category="Thermal",
        )
        chunks = [
            {
                "document_name": "14_Thermal_SPICE_Simulation_Model.pdf",
                "content": "CFD simulation model predicts coolant flow rate of 16.2 L/min at 45C ambient.",
            }
        ]
        res = rule_based_multi_condition_verification(contract, chunks)
        self.assertEqual(res.status, "UNKNOWN")

    # Case 2: Simulation evidence -> SUPPORTED when requirement explicitly specifies simulation
    def test_simulation_evidence_accepted_when_requirement_specifies_simulation(self):
        contract = parse_requirement_contract(
            req_code="REQ-SIM-001",
            title="Thermal Model Core Temperature Prediction",
            description="The thermal model shall simulate cell core temperatures within 2.0 °C of pack thermistor readings, verified by simulation.",
            category="Thermal",
        )
        chunks = [
            {
                "document_name": "14_Thermal_SPICE_Simulation_Model.pdf",
                "content": "CFD simulation model results: maximum deviation between modeled core temperature and thermistor is 1.1 °C. Verdict: PASS.",
            }
        ]
        res = rule_based_multi_condition_verification(contract, chunks)
        self.assertEqual(res.status, "SUPPORTED")

    # Case 3: SPICE simulation with 'pending hardware test' -> UNKNOWN (NOT PARTIAL)
    def test_spice_simulation_pending_hardware_test_returns_unknown(self):
        contract = parse_requirement_contract(
            req_code="REQ-PHYS-002",
            title="Overcurrent Detection Response Time",
            description="The gate driver shall shut down power switches within 1.5 µs during short-circuit.",
            category="Inverter",
        )
        chunks = [
            {
                "document_name": "12_Gate_Driver_SPICE_Simulation_Analysis.pdf",
                "content": "SPICE simulation demonstrates shutdown in 1.1 µs; full bench qualification test pending chamber arrival.",
            }
        ]
        res = rule_based_multi_condition_verification(contract, chunks)
        self.assertEqual(res.status, "UNKNOWN")

    # Case 4: Architecture specification -> UNKNOWN
    def test_architecture_specification_returns_unknown(self):
        contract = parse_requirement_contract(
            req_code="REQ-ARCH-001",
            title="Hardware Watchdog Timeout",
            description="The safety controller shall service the hardware watchdog every 20.0 ms.",
            category="Functional Safety",
        )
        chunks = [
            {
                "document_name": "02_System_Architecture_Interface_Spec.docx",
                "content": "Architecture Specification: The safety MCU is allocated a 20.0 ms window for watchdog refresh servicing.",
            }
        ]
        res = rule_based_multi_condition_verification(contract, chunks)
        self.assertEqual(res.status, "UNKNOWN")

    # Case 5: Inconclusive/uncalibrated bench test -> UNKNOWN
    def test_inconclusive_bench_test_returns_unknown(self):
        contract = parse_requirement_contract(
            req_code="REQ-BENCH-001",
            title="Pack Enclosure Acoustic Noise",
            description="Acoustic noise from the coolant pump and inverter shall not exceed 45.0 dBA at 1 meter.",
            category="Acoustics",
        )
        chunks = [
            {
                "document_name": "18_Acoustic_Bench_Test_Notes.txt",
                "content": "Preliminary bench test characterization measured 42 dBA in open air, but full vehicle enclosure acoustic calibration remains unproven.",
            }
        ]
        res = rule_based_multi_condition_verification(contract, chunks)
        self.assertEqual(res.status, "UNKNOWN")

    # Case 6: System test PASS overrides component datasheet limit (Entity Scope)
    def test_system_test_pass_overrides_component_datasheet_limit(self):
        contract = parse_requirement_contract(
            req_code="REQ-AUT-002",
            title="BCU Pack Operational Voltage Range",
            description="The BCU shall maintain continuous monitoring and communication across the high-voltage pack operating range of 400.0 V DC to 800.0 V DC.",
            category="BMS",
        )
        claims = [
            EvidenceClaim(
                claim_type="numeric_range",
                parameter="operating_voltage",
                min_value=380.0,
                max_value=820.0,
                unit="V",
                document_name="08_BMS_Functional_Safety_Validation_Report.pdf",
                quote="High-voltage pack monitoring validated continuously from 380.0 V DC to 820.0 V DC across all thermal conditions. Result: PASS.",
                source_authority="VALIDATION_REPORT",
                entity_scope="BCU",
            ),
            EvidenceClaim(
                claim_type="threshold",
                parameter="cell_standoff_voltage",
                max_value=750.0,
                unit="V",
                document_name="03_BMS_Cell_Supervisory_ASIC_Datasheet.pdf",
                quote="Maximum continuous common-mode voltage rating is limited to 750.0 V DC per ASIC channel.",
                source_authority="DATASHEET",
                entity_scope="ASIC",
            ),
        ]
        finding = detect_contract_contradiction(contract, claims)
        self.assertIsNone(finding)

    # Case 7: Component datasheet limit creates CONFLICT when requirement is scoped to that component
    def test_component_datasheet_limit_creates_conflict_when_scoped_to_component(self):
        contract = parse_requirement_contract(
            req_code="REQ-AUT-005",
            title="BMS Cell Supervisory ASIC Standoff Voltage",
            description="The BMS cell supervisory ASIC shall provide continuous common-mode voltage standoff capability of at least 1000.0 V DC across all series cell channels.",
            category="BMS",
        )
        claims = [
            EvidenceClaim(
                claim_type="threshold",
                parameter="cell_standoff_voltage",
                max_value=750.0,
                unit="V",
                document_name="03_BMS_Cell_Supervisory_ASIC_Datasheet.pdf",
                quote="Maximum continuous common-mode voltage rating is limited to 750.0 V DC per ASIC channel.",
                source_authority="DATASHEET",
                entity_scope="ASIC",
            ),
        ]
        finding = detect_contract_contradiction(contract, claims)
        self.assertIsNotNone(finding)
        self.assertTrue(finding.has_conflict)

    # Case 8: Tested range enveloping required range -> SUPPORTED
    def test_tested_range_enveloping_required_range_is_supported(self):
        contract = parse_requirement_contract(
            req_code="REQ-AUT-002",
            title="High-Voltage Operating Window",
            description="The system shall operate within 400.0 V to 800.0 V DC.",
            category="HV",
        )
        claims = [
            EvidenceClaim(
                claim_type="numeric_range",
                min_value=380.0,
                max_value=820.0,
                unit="V",
                document_name="08_BMS_Functional_Safety_Validation_Report.pdf",
                quote="Operating sweep executed from 380.0 V to 820.0 V without errors.",
                source_authority="VALIDATION_REPORT",
            )
        ]
        outcome = validate_numeric_range(contract, claims)
        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.status, "SUPPORTED")

    # Case 9: Tested range narrower than required -> PARTIAL
    def test_tested_range_narrower_than_required_is_partial(self):
        contract = parse_requirement_contract(
            req_code="REQ-ENV-001",
            title="Extended Operating Temperature",
            description="The ECU shall operate continuously across the temperature range of -40.0 °C to +85.0 °C.",
            category="Environmental",
        )
        claims = [
            EvidenceClaim(
                claim_type="numeric_range",
                min_value=-20.0,
                max_value=70.0,
                unit="°C",
                document_name="09_Environmental_Thermal_Chamber_Report.pdf",
                quote="Thermal testing successfully conducted from -20.0 °C to +70.0 °C; full qualification pending.",
                source_authority="QUALIFICATION_TEST",
            )
        ]
        outcome = validate_numeric_range(contract, claims)
        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.status, "PARTIAL")

    # Case 10: Tested range completely outside required -> CONFLICT
    def test_tested_range_outside_required_is_conflict(self):
        contract = parse_requirement_contract(
            req_code="REQ-PWR-001",
            title="Auxiliary Supply Operating Range",
            description="The DC-DC converter auxiliary input shall support 18.0 V to 32.0 V DC.",
            category="Power",
        )
        claims = [
            EvidenceClaim(
                claim_type="numeric_range",
                min_value=10.0,
                max_value=15.0,
                unit="V",
                document_name="15_Aux_Supply_Test_Report.pdf",
                quote="Auxiliary bus evaluated strictly in 10.0 V to 15.0 V range.",
                source_authority="EMPIRICAL_TEST",
            )
        ]
        outcome = validate_numeric_range(contract, claims)
        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.status, "CONFLICT")

    # Case 11: Multi-condition all proven -> SUPPORTED
    def test_multi_condition_all_proven_is_supported(self):
        conditions = [
            AtomicConditionContract(condition_id="C-1", parameter="voltage", operator="<=", threshold=4.25, unit="V"),
            AtomicConditionContract(condition_id="C-2", parameter="delay", operator="<=", threshold=10.0, unit="ms"),
        ]
        condition_results = [
            ConditionVerificationResult(condition_id="C-1", status="PROVEN", finding="Overvoltage verified at 4.25V"),
            ConditionVerificationResult(condition_id="C-2", status="PROVEN", finding="Trip delay verified at 8.2ms"),
        ]
        status, conf, reason = aggregate_condition_statuses(conditions, condition_results)
        self.assertEqual(status, "SUPPORTED")

    # Case 12: Multi-condition with one pending -> PARTIAL
    def test_multi_condition_one_pending_is_partial(self):
        conditions = [
            AtomicConditionContract(condition_id="C-1", parameter="voltage", operator="<=", threshold=4.25, unit="V"),
            AtomicConditionContract(condition_id="C-2", parameter="delay", operator="<=", threshold=10.0, unit="ms"),
        ]
        condition_results = [
            ConditionVerificationResult(condition_id="C-1", status="PROVEN", finding="Overvoltage verified at 4.25V"),
            ConditionVerificationResult(condition_id="C-2", status="PENDING", finding="Trip delay test pending"),
        ]
        status, conf, reason = aggregate_condition_statuses(conditions, condition_results)
        self.assertEqual(status, "PARTIAL")

    # Case 13: Multi-condition with one failed -> CONFLICT
    def test_multi_condition_one_failed_is_conflict(self):
        conditions = [
            AtomicConditionContract(condition_id="C-1", parameter="voltage", operator="<=", threshold=4.25, unit="V"),
            AtomicConditionContract(condition_id="C-2", parameter="delay", operator="<=", threshold=10.0, unit="ms"),
        ]
        condition_results = [
            ConditionVerificationResult(condition_id="C-1", status="PROVEN", finding="Overvoltage verified at 4.25V"),
            ConditionVerificationResult(condition_id="C-2", status="FAILED", finding="Trip delay was 14.5ms exceeding 10.0ms limit"),
        ]
        status, conf, reason = aggregate_condition_statuses(conditions, condition_results)
        self.assertEqual(status, "CONFLICT")

    # Case 14: PASS verdict on subclause does NOT override untested condition
    def test_pass_verdict_on_subclause_does_not_override_untested_condition(self):
        conditions = [
            AtomicConditionContract(condition_id="C-1", parameter="overvoltage", operator="<=", threshold=4.25, unit="V"),
            AtomicConditionContract(condition_id="C-2", parameter="undervoltage", operator=">=", threshold=2.50, unit="V"),
        ]
        # Overvoltage tested and passed, but undervoltage untested
        condition_results = [
            ConditionVerificationResult(condition_id="C-1", status="PROVEN", finding="Result: PASS for overvoltage threshold"),
            ConditionVerificationResult(condition_id="C-2", status="UNTESTED", finding="Undervoltage test not conducted"),
        ]
        status, conf, reason = aggregate_condition_statuses(conditions, condition_results)
        self.assertEqual(status, "PARTIAL")

    # Case 15: Empty evidence -> MISSING
    def test_empty_evidence_returns_missing(self):
        contract = parse_requirement_contract(
            req_code="REQ-EMPTY-001",
            title="Unimplemented Feature",
            description="The system shall provide wireless inductive charging handshake.",
            category="Charging",
        )
        chunks = []
        res = rule_based_multi_condition_verification(contract, chunks)
        self.assertEqual(res.status, "MISSING")

    # Case 16: Compliance matrix 'NOT STARTED' -> MISSING
    def test_compliance_matrix_not_started_returns_missing(self):
        contract = parse_requirement_contract(
            req_code="REQ-MAT-001",
            title="CAN-FD Bus Sleep Mode",
            description="The transceiver shall enter ultra-low-power sleep within 50.0 ms.",
            category="Networking",
        )
        chunks = [
            {
                "document_name": "19_Verification_Compliance_Matrix.xlsx",
                "content": "REQ-MAT-001 CAN-FD Sleep Mode | Status: Not Started | Owner: Networking Team | Evidence: Missing",
            }
        ]
        res = rule_based_multi_condition_verification(contract, chunks)
        self.assertEqual(res.status, "MISSING")

    # Case 17: Compliance matrix 'IN PROGRESS' -> PARTIAL
    def test_compliance_matrix_in_progress_returns_partial(self):
        contract = parse_requirement_contract(
            req_code="REQ-MAT-002",
            title="Ethernet PHY Compliance",
            description="The 1000BASE-T1 Ethernet PHY shall pass IEEE 802.3bp physical layer compliance.",
            category="Networking",
        )
        chunks = [
            {
                "document_name": "19_Verification_Compliance_Matrix.xlsx",
                "content": "REQ-MAT-002 Ethernet PHY | Status: In Progress | Phase 1 bench test done, environmental pending",
            }
        ]
        res = rule_based_multi_condition_verification(contract, chunks)
        self.assertEqual(res.status, "PARTIAL")


class TestConditionAwareRetrieval(unittest.TestCase):
    def test_atomic_queries_reserve_passages_for_distinct_conditions(self):
        chunks = [
            {
                "id": "matrix",
                "document_name": "compliance_matrix.xlsx",
                "content": "REQ-X-001 overall verification tracking record.",
            },
            {
                "id": "current",
                "document_name": "current_test.pdf",
                "content": "REQ-X-001 measured continuous current reached 250 A during the bench test.",
            },
            {
                "id": "temperature",
                "document_name": "thermal_test.pdf",
                "content": "REQ-X-001 ambient temperature operation was verified at 65 C in the chamber.",
            },
            {
                "id": "noise",
                "document_name": "unrelated.pdf",
                "content": "REQ-X-001 generic system discussion without measured condition evidence.",
            },
        ]

        results = retrieve_candidate_evidence(
            "REQ-X-001 converter operating requirements",
            chunks,
            top_k=3,
            condition_queries=[
                "REQ-X-001 continuous current >= 250 A",
                "REQ-X-001 ambient temperature == 65 C",
            ],
        )

        selected = {item.chunk_id for item in results}
        self.assertIn("current", selected)
        self.assertIn("temperature", selected)


if __name__ == "__main__":
    unittest.main()
