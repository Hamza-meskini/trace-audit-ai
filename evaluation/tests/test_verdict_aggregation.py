"""Unit tests for the Python-owned verdict aggregation architecture.

Covers the 16 required behaviors from the verification-architecture spec:
aggregation precedence, LLM-override safety, deterministic PASS mapping,
evidence qualification (method / scope / parameter), and numeric envelope
semantics. Tests verify FINAL statuses, not intermediate values.
"""

import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "backend"))

from app.schemas.contract import RequirementContract, AtomicConditionContract
from app.schemas.claim import EvidenceClaim
from app.schemas.verification_result import (
    ConditionVerificationResult,
    VerificationAnalysisResult,
)
from app.services.verdict_aggregator import (
    aggregate_condition_statuses,
    condition_results_from_claims,
    finalize_verdict,
    merge_qualification_into_conditions,
)
from app.services.evidence_qualification import qualify_evidence


def make_contract(
    req_code="REQ-TEST-001",
    title="Test requirement",
    conditions=None,
    verification_method="physical_test",
    scope="System",
    min_value=None,
    max_value=None,
    unit=None,
    parameter=None,
):
    return RequirementContract(
        requirement_id=req_code,
        req_code=req_code,
        title=title,
        raw_text=f"{title}.",
        verification_method=verification_method,
        scope=scope,
        atomic_conditions=conditions or [],
        min_value=min_value,
        max_value=max_value,
        unit=unit,
        parameter=parameter,
    )


def cond(cid, parameter=None, operator=None, threshold=None, unit=None, minv=None, maxv=None):
    return AtomicConditionContract(
        condition_id=cid,
        description=f"Condition {cid}",
        parameter=parameter,
        operator=operator,
        threshold=threshold,
        unit=unit,
        min_value=minv,
        max_value=maxv,
    )


def cr(cid, status, evidence_ids=None, quote=None):
    return ConditionVerificationResult(
        condition_id=cid, status=status,
        evidence_ids=evidence_ids or [], quote=quote,
    )


def qual(eid, doc, status, scope_compatible=None, parameter_compatible=None, parameters_found=None):
    from app.schemas.evidence_qualification import EvidenceQualification
    return EvidenceQualification(
        evidence_id=eid, document_name=doc, source_authority="EMPIRICAL_TEST",
        entity_scope="System", parameters_found=parameters_found or [],
        verification_method="physical_test", required_verification_method="physical_test",
        method_compatible=True, scope_compatible=scope_compatible,
        parameter_compatible=parameter_compatible, is_authoritative=True,
        qualification_status=status, reason="test",
    )


def claim(doc, quote, claim_type="semantic", **kw):
    return EvidenceClaim(document_name=doc, quote=quote, claim_type=claim_type, **kw)


class TestAggregationPrecedence(unittest.TestCase):
    """Spec Phase 2: formal aggregation rules 1-5."""

    def setUp(self):
        self.contract = make_contract(conditions=[cond("C1"), cond("C2"), cond("C3")])

    def test_1_all_proven_supported(self):
        status, _, _ = aggregate_condition_statuses(
            self.contract, [cr("C1", "PROVEN"), cr("C2", "PROVEN"), cr("C3", "PROVEN")])
        self.assertEqual(status, "SUPPORTED")

    def test_2_one_failed_conflict(self):
        status, _, _ = aggregate_condition_statuses(
            self.contract, [cr("C1", "PROVEN"), cr("C2", "PROVEN"), cr("C3", "FAILED")])
        self.assertEqual(status, "CONFLICT")

    def test_3_one_pending_partial(self):
        status, _, _ = aggregate_condition_statuses(
            self.contract, [cr("C1", "PROVEN"), cr("C2", "PROVEN"), cr("C3", "UNTESTED")])
        self.assertEqual(status, "PARTIAL")

    def test_4_all_untested_with_evidence_unknown(self):
        status, _, _ = aggregate_condition_statuses(
            self.contract, [cr("C1", "UNTESTED"), cr("C2", "UNTESTED"), cr("C3", "UNTESTED")],
            has_relevant_evidence=True)
        self.assertEqual(status, "UNKNOWN")

    def test_5_no_evidence_missing(self):
        status, _, _ = aggregate_condition_statuses(
            self.contract, [cr("C1", "UNTESTED"), cr("C2", "UNTESTED"), cr("C3", "UNTESTED")],
            has_relevant_evidence=False)
        self.assertEqual(status, "MISSING")

    def test_evidence_absent_record_missing(self):
        status, _, _ = aggregate_condition_statuses(
            self.contract, [cr("C1", "UNTESTED"), cr("C2", "UNTESTED")],
            has_relevant_evidence=True, evidence_absent=True)
        self.assertEqual(status, "MISSING")

    def test_all_pending_partial(self):
        # Narrower tested range / in-progress: PENDING, none proven
        status, _, _ = aggregate_condition_statuses(
            self.contract, [cr("C1", "PENDING"), cr("C2", "PENDING")],
            has_relevant_evidence=True)
        self.assertEqual(status, "PARTIAL")

    def test_failed_beats_supported(self):
        # FAILED has highest precedence even when everything else is PROVEN
        status, _, _ = aggregate_condition_statuses(
            self.contract, [cr("C1", "PROVEN"), cr("C2", "FAILED"), cr("C3", "PROVEN")])
        self.assertEqual(status, "CONFLICT")


class TestLLMFinalVerdictOverride(unittest.TestCase):
    """Spec Phase 1: the LLM top-level status must never be trusted."""

    def setUp(self):
        self.contract = make_contract(conditions=[cond("C1"), cond("C2")])
        self.qualifications = [qual("E1", "lab_report.pdf", "QUALIFIED", True, True)]

    def test_6_llm_wrong_top_level_status_overridden(self):
        # LLM claims SUPPORTED but its own condition results say C2 UNTESTED
        llm = VerificationAnalysisResult(
            status="SUPPORTED", confidence=95,
            condition_results=[cr("C1", "PROVEN", ["E1"]), cr("C2", "UNTESTED")],
            reason="LLM provisional assessment.",
        )
        final = finalize_verdict(self.contract, llm, self.qualifications,
                                 qualified_contents={"E1": "measured value"})
        self.assertEqual(final.status, "PARTIAL")
        self.assertIn("overrode provisional status 'SUPPORTED'", final.reason)

    def test_llm_supported_with_all_conditions_proven_stands(self):
        llm = VerificationAnalysisResult(
            status="SUPPORTED", confidence=95,
            condition_results=[cr("C1", "PROVEN", ["E1"]), cr("C2", "PROVEN", ["E1"])],
            reason="All conditions demonstrated.",
        )
        final = finalize_verdict(self.contract, llm, self.qualifications,
                                 qualified_contents={"E1": "measured value"})
        self.assertEqual(final.status, "SUPPORTED")

    def test_llm_conflict_downgraded_to_supported(self):
        # LLM hallucinates CONFLICT; conditions all PROVEN with qualified evidence
        llm = VerificationAnalysisResult(
            status="CONFLICT", confidence=90,
            condition_results=[cr("C1", "PROVEN", ["E1"]), cr("C2", "PROVEN", ["E1"])],
            reason="Provisional conflict claim.",
        )
        final = finalize_verdict(self.contract, llm, self.qualifications,
                                 qualified_contents={"E1": "measured value"})
        self.assertEqual(final.status, "SUPPORTED")

    def test_llm_failure_never_supported(self):
        # LLM returns nothing usable -> must not become SUPPORTED
        llm = VerificationAnalysisResult(
            status="SUPPORTED", confidence=95,
            condition_results=[], reason="Empty analysis.",
        )
        final = finalize_verdict(self.contract, llm, self.qualifications,
                                 has_relevant_evidence=True)
        self.assertIn(final.status, ("UNKNOWN", "MISSING", "PARTIAL"))
        self.assertNotEqual(final.status, "SUPPORTED")


class TestDeterministicPassFallback(unittest.TestCase):
    """Spec Phase 3: generic PASS must not become SUPPORTED without full mapping."""

    def setUp(self):
        # Phase 3 example: C1 voltage<=800V, C2 temp<=85C, C3 latency<=20ms
        self.contract = make_contract(
            req_code="REQ-PASS-001",
            title="Compound requirement",
            conditions=[
                cond("C1", parameter="voltage", operator="<=", threshold=800.0, unit="V"),
                cond("C2", parameter="temperature", operator="<=", threshold=85.0, unit="°C"),
                cond("C3", parameter="latency", operator="<=", threshold=20.0, unit="ms"),
            ],
        )
        self.doc = "lab_test_report.pdf"
        self.content = "Result: PASS. Voltage = 750 V measured at ambient 25 C."
        self.qualifications = [qual(
            "E1", self.doc, "QUALIFIED", True, True,
            parameters_found=["voltage", "temperature"],
        )]

    def test_7_pass_on_one_subcondition_not_supported(self):
        claims = [
            claim(self.doc, self.content, claim_type="test_verdict", test_result="PASS"),
            claim(self.doc, "Voltage = 750 V", claim_type="threshold", value=750.0, unit="V"),
        ]
        results = condition_results_from_claims(self.contract, claims, self.qualifications)
        status, _, _ = aggregate_condition_statuses(self.contract, results)
        self.assertEqual(status, "PARTIAL")
        # Explicitly NOT supported
        self.assertNotEqual(status, "SUPPORTED")

    def test_8_pass_on_all_mapped_conditions_supported(self):
        single = make_contract(
            req_code="REQ-PASS-002",
            title="Single condition requirement",
            conditions=[cond("C1", parameter="voltage", operator="<=", threshold=800.0, unit="V")],
        )
        doc = "qualification_report.pdf"
        quals = [qual("E1", doc, "QUALIFIED", True, True, parameters_found=["voltage"])]
        claims = [
            claim(doc, "REQ-PASS-002: Result: PASS. Measured 750 V.", claim_type="test_verdict", test_result="PASS"),
        ]
        results = condition_results_from_claims(single, claims, quals)
        status, _, _ = aggregate_condition_statuses(single, results)
        self.assertEqual(status, "SUPPORTED")


class TestEvidenceMethodQualification(unittest.TestCase):
    """Spec Phase 6: verification-method compatibility."""

    def test_9_physical_requirement_simulation_only_unknown(self):
        contract = make_contract(
            req_code="REQ-SIM-001",
            title="THD <= 5% verified by physical bench test",
            conditions=[cond("C1", parameter="thd", operator="<=", threshold=5.0, unit="%")],
            verification_method="physical_test",
        )
        sim_doc = "matlab_thd_simulation.pdf"
        sim_content = "MATLAB simulation predicts THD = 2.4% at rated load."
        q = qualify_evidence(contract, "E1", sim_doc, sim_content)

        llm = VerificationAnalysisResult(
            status="SUPPORTED", confidence=90,
            condition_results=[cr("C1", "PROVEN", ["E1"], quote=sim_content[:60])],
            reason="Simulation shows compliance.",
        )
        final = finalize_verdict(contract, llm, [q], qualified_contents={"E1": sim_content})
        self.assertEqual(q.qualification_status, "NOT_QUALIFIED")
        self.assertEqual(final.status, "UNKNOWN")

    def test_10_simulation_requirement_simulation_evidence_can_support(self):
        contract = make_contract(
            req_code="REQ-SIM-002",
            title="THD <= 5% in MATLAB simulation",
            conditions=[cond("C1", parameter="thd", operator="<=", threshold=5.0, unit="%")],
            verification_method="simulation",
        )
        sim_doc = "matlab_thd_simulation.pdf"
        sim_content = "MATLAB simulation predicts THD = 2.4% at rated load."
        q = qualify_evidence(contract, "E1", sim_doc, sim_content)

        llm = VerificationAnalysisResult(
            status="SUPPORTED", confidence=90,
            condition_results=[cr("C1", "PROVEN", ["E1"], quote=sim_content[:60])],
            reason="Simulation model satisfies the required verification.",
        )
        final = finalize_verdict(contract, llm, [q], qualified_contents={"E1": sim_content})
        self.assertEqual(q.qualification_status, "QUALIFIED")
        self.assertEqual(final.status, "SUPPORTED")


class TestEntityScopeQualification(unittest.TestCase):
    """Spec Phase 7: entity/scope compatibility — mismatch is not conflict."""

    def test_11_bcu_requirement_asic_datasheet_no_automatic_conflict(self):
        contract = make_contract(
            req_code="REQ-SCOPE-001",
            title="BCU pack operating range 400 V to 800 V",
            conditions=[
                cond("C1", parameter="voltage", operator="<=", threshold=400.0, unit="V"),
                cond("C2", parameter="voltage", operator=">=", threshold=800.0, unit="V"),
            ],
            scope="BCU",
            min_value=400.0, max_value=800.0, unit="V",
        )
        ds_doc = "03_bms_cell_supervisory_asic_datasheet.pdf"
        ds_content = "ASIC absolute maximum standoff voltage 750 V DC."
        q = qualify_evidence(contract, "E1", ds_doc, ds_content)

        # Even an LLM FAILED claim from the wrong-scope document must not
        # create a requirement conflict through aggregation.
        llm = VerificationAnalysisResult(
            status="CONFLICT", confidence=90,
            condition_results=[cr("C2", "FAILED", ["E1"], quote=ds_content[:60])],
            reason="ASIC limit below requirement.",
        )
        final = finalize_verdict(contract, llm, [q], qualified_contents={"E1": ds_content})
        self.assertEqual(q.qualification_status, "NOT_QUALIFIED")
        self.assertNotEqual(final.status, "CONFLICT")

    def test_12_asic_requirement_conflicting_asic_datasheet_conflict(self):
        contract = make_contract(
            req_code="REQ-SCOPE-002",
            title="Cell supervisory ASIC shall withstand 1000 V DC transients",
            conditions=[cond("C1", parameter="standoff_voltage", operator=">=", threshold=1000.0, unit="V")],
            scope="ASIC",
        )
        ds_doc = "03_bms_cell_supervisory_asic_datasheet.pdf"
        ds_content = "ASIC absolute maximum standoff voltage 750 V DC."
        q = qualify_evidence(contract, "E1", ds_doc, ds_content)

        claims = [claim(ds_doc, ds_content, claim_type="threshold", value=750.0, unit="V")]
        results = condition_results_from_claims(contract, claims, [q])
        status, _, _ = aggregate_condition_statuses(contract, results)
        self.assertEqual(status, "CONFLICT")

    def test_pack_evidence_qualifies_for_bcu_requirement(self):
        # Same subsystem family: pack-level test proves BCU requirement
        contract = make_contract(
            req_code="REQ-SCOPE-003",
            title="BCU shall monitor pack range 400 V to 800 V",
            conditions=[
                cond("C1", parameter="voltage", operator="<=", threshold=400.0, unit="V"),
                cond("C2", parameter="voltage", operator=">=", threshold=800.0, unit="V"),
            ],
            scope="BCU",
            min_value=400.0, max_value=800.0, unit="V",
        )
        q = qualify_evidence(contract, "E1", "hv_pack_test_report.pdf",
                             "Pack operating envelope verified from 380 V to 820 V, verdict: pass")
        self.assertEqual(q.qualification_status, "QUALIFIED")


class TestParameterQualification(unittest.TestCase):
    """Spec Phase 8: parameter compatibility."""

    def test_13_pyro_latency_vs_contactor_latency_parameter_mismatch(self):
        contract = make_contract(
            req_code="REQ-PARAM-001",
            title="Pyro-fuse trigger latency <= 5 us",
            conditions=[cond("C1", parameter="pyro_trigger_latency", operator="<=", threshold=5.0, unit="us")],
        )
        lab_doc = "lab_test_report.pdf"
        content = "Contactor transition latency = 4 ms measured on the bench."
        q = qualify_evidence(contract, "E1", lab_doc, content)
        self.assertEqual(q.qualification_status, "NOT_QUALIFIED")
        self.assertFalse(q.parameter_compatible)

        # LLM tries to prove pyro latency with contactor measurement
        llm = VerificationAnalysisResult(
            status="SUPPORTED", confidence=90,
            condition_results=[cr("C1", "PROVEN", ["E1"], quote=content[:60])],
            reason="Latency of 4 ms satisfies.",
        )
        final = finalize_verdict(contract, llm, [q], qualified_contents={"E1": content})
        self.assertNotEqual(final.status, "SUPPORTED")

    def test_temperature_kinds_are_distinct(self):
        contract = make_contract(
            req_code="REQ-PARAM-002",
            title="Ambient operating temperature -40 C to +85 C",
            conditions=[
                cond("C1", parameter="temperature", operator="<=", threshold=-40.0, unit="°C"),
                cond("C2", parameter="temperature", operator=">=", threshold=85.0, unit="°C"),
            ],
            min_value=-40.0, max_value=85.0, unit="°C",
        )
        q = qualify_evidence(contract, "E1", "thermal_analysis.pdf",
                             "ASIC junction temperature peaks at 125 C")
        self.assertEqual(q.qualification_status, "NOT_QUALIFIED")

        right = qualify_evidence(contract, "E1", "environmental_test.pdf",
                                 "Chamber ambient temperature cycled from -40 C to +85 C, verdict: pass")
        self.assertEqual(right.qualification_status, "QUALIFIED")


class TestNumericEnvelopeSemantics(unittest.TestCase):
    """Spec Phase 12 cases 14-16: envelope superset / narrower / violation."""

    def _range_contract(self):
        return make_contract(
            req_code="REQ-ENV-001",
            title="Operating voltage 400 V to 800 V",
            conditions=[
                cond("C1", parameter="voltage", operator="<=", threshold=400.0, unit="V"),
                cond("C2", parameter="voltage", operator=">=", threshold=800.0, unit="V"),
            ],
            min_value=400.0, max_value=800.0, unit="V",
        )

    def test_14_superset_envelope_supported(self):
        contract = self._range_contract()
        doc = "hv_test_report.pdf"
        content = "Tested continuously from 380 V to 820 V DC, verdict: pass."
        q = qualify_evidence(contract, "E1", doc, content)
        claims = [claim(doc, content, claim_type="numeric_range", min_value=380.0, max_value=820.0, unit="V")]
        results = condition_results_from_claims(contract, claims, [q])
        status, _, _ = aggregate_condition_statuses(contract, results)
        self.assertEqual(status, "SUPPORTED")

    def test_15_narrower_range_partial(self):
        contract = self._range_contract()
        doc = "hv_test_report.pdf"
        content = "Tested from 450 V to 700 V DC."
        q = qualify_evidence(contract, "E1", doc, content)
        claims = [claim(doc, content, claim_type="numeric_range", min_value=450.0, max_value=700.0, unit="V")]
        results = condition_results_from_claims(contract, claims, [q])
        status, _, _ = aggregate_condition_statuses(contract, results)
        self.assertEqual(status, "PARTIAL")

    def test_16_numeric_violation_conflict(self):
        contract = make_contract(
            req_code="REQ-ENV-002",
            title="Quiescent current <= 150 uA",
            conditions=[cond("C1", parameter="current", operator="<=", threshold=150.0, unit="uA")],
        )
        doc = "lab_test_report.pdf"
        content = "Measured quiescent current 162 uA at 25 C."
        q = qualify_evidence(contract, "E1", doc, content)
        claims = [claim(doc, content, claim_type="threshold", value=162.0, unit="uA")]
        results = condition_results_from_claims(contract, claims, [q])
        status, _, _ = aggregate_condition_statuses(contract, results)
        self.assertEqual(status, "CONFLICT")


class TestMergeQualificationEnforcement(unittest.TestCase):
    """Direct checks of the qualification merge on condition results."""

    def test_proven_without_evidence_reference_downgraded(self):
        contract = make_contract(conditions=[cond("C1")])
        qualifications = [qual("E1", "lab.pdf", "QUALIFIED", True, True)]
        crs = [cr("C1", "PROVEN")]  # no evidence_ids, no quote
        merged = merge_qualification_into_conditions(contract, crs, qualifications, {"E1": "x"})
        self.assertEqual(merged[0].status, "INCONCLUSIVE")

    def test_proven_with_quote_in_qualified_chunk_accepted(self):
        contract = make_contract(conditions=[cond("C1")])
        qualifications = [qual("E1", "lab.pdf", "QUALIFIED", True, True)]
        crs = [cr("C1", "PROVEN", quote="measured quiescent current 142 uA")]
        merged = merge_qualification_into_conditions(contract, crs, qualifications,
                                                     {"E1": "We measured quiescent current 142 uA on the bench"})
        self.assertEqual(merged[0].status, "PROVEN")

    def test_evidence_id_normalization(self):
        contract = make_contract(conditions=[cond("C1")])
        qualifications = [qual("E2", "lab.pdf", "QUALIFIED", True, True)]
        crs = [cr("C1", "PROVEN", evidence_ids=["Evidence 2"])]
        merged = merge_qualification_into_conditions(contract, crs, qualifications, {})
        self.assertEqual(merged[0].status, "PROVEN")


if __name__ == "__main__":
    unittest.main()
