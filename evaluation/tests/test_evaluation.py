"""Unit tests for the TraceAudit benchmarking and evaluation framework.

Tests logic for:
  - Ground truth schema loading and consistency
  - Status normalization and code normalization
  - Extraction metrics calculation (Precision, Recall, F1)
  - Retrieval Recall@K and MRR computation
  - Verification Confusion Matrix and multi-class metrics
  - Conflict & Missing detection F1 metrics
  - Unsupported Claim (hallucination) rate calculation
  - Failure categorization logic
  - Markdown report generation
"""

import unittest
import json
import tempfile
from pathlib import Path

import openpyxl

from evaluation.metrics import (
    normalize_status,
    normalize_code,
    evaluate_extraction,
    evaluate_retrieval,
    evaluate_verification,
    evaluate_specialty_metrics,
    classify_failure,
    _is_evidence_hit,
)
from evaluation.report import generate_markdown_report
from evaluation.run_evaluation import load_ground_truth
from evaluation.export_excel_trace import export_benchmark_audit_trace_excel
from evaluation.run_complex_benchmark import calculate_review_gate_safety_metrics


class TestEvaluationFramework(unittest.TestCase):

    def test_excel_trace_exposes_pipeline_contract_and_all_source_authorities(self):
        ground_truth = [{
            "requirement_id": "REQ-001",
            "title": "Ground truth title",
            "requirement_text": "Ground truth requirement text",
            "category": "Electrical",
            "expected_status": "SUPPORTED",
            "conditions": [{
                "condition_id": "GT-C1",
                "parameter": "voltage",
                "operator": "<=",
                "threshold": 12.0,
                "unit": "V",
            }],
        }]
        pipeline = [{
            "req_code": "REQ-001",
            "title": "Extracted title",
            "description": "Actual extracted requirement text",
            "conditions": [{
                "condition_id": "EXT-C1",
                "parameter": "voltage",
                "operator": "<=",
                "threshold": 12.0,
                "unit": "V",
            }],
        }]
        retrieved = {"REQ-001": [
            {
                "document_name": "Compliance_Matrix.xlsx",
                "doc_type": "Compliance matrix",
                "content": "Requirement REQ-001: COMPLETE",
            },
            {
                "document_name": "Lab_Validation_Report.pdf",
                "doc_type": "Test report",
                "content": "Test Case REQ-001 measured voltage at 11.8 V. Verdict: PASS.",
            },
        ]}
        predictions = {"REQ-001": {
            "expected": "SUPPORTED",
            "predicted": "SUPPORTED",
            "confidence": 95,
            "review_state": "Reviewed",
            "review_required": False,
            "auto_close_eligible": True,
            "review_gate_reasons": [],
        }}

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "benchmark_audit_trace.xlsx"
            export_benchmark_audit_trace_excel(
                results={
                    "evaluation_mode": "end-to-end",
                    "condition_metrics": {
                        "condition_accuracy": 100.0,
                        "raw_llm": {"condition_accuracy": 0.0},
                        "condition_records": [{
                            "requirement_id": "REQ-001",
                            "condition_id": "GT-C1",
                            "parameter": "voltage",
                            "expected_status": "PROVEN",
                            "ground_truth_source": "explicit",
                            "llm_status": "INCONCLUSIVE",
                            "post_reconciliation_status": "PROVEN",
                            "pre_qualification_status": "PROVEN",
                            "final_status": "PROVEN",
                            "correct": True,
                            "is_numeric_condition": True,
                        }],
                    },
                    "stage_diagnostics": {
                        "aggregation_oracle": {"accuracy": 100.0},
                        "audit_defensible_accuracy": 100.0,
                    },
                    "review_gate_safety_metrics": {
                        "false_supported_predictions": 0,
                        "false_supported_routed_to_review": 0,
                        "false_automatic_closures": 0,
                        "review_gate_capture_rate": None,
                        "automatic_closure_precision": 100.0,
                        "supported_review_rate": 0.0,
                    },
                    "extraction_metrics": {
                        "contract_mismatches": [{
                            "requirement_id": "REQ-001",
                            "condition_id": "GT-C1",
                            "failed_fields": ["parameter"],
                            "expected": {"parameter": "voltage"},
                            "extracted": {"parameter": "pack_voltage"},
                        }],
                    },
                },
                ground_truth_reqs=ground_truth,
                links_by_id={},
                retrieved_by_req=retrieved,
                assessments={},
                predictions=predictions,
                failures=[],
                excel_path=output,
                pipeline_requirements=pipeline,
            )
            workbook = openpyxl.load_workbook(output, read_only=True)
            try:
                sheet = workbook["Full_Pipeline_Trace_100_Reqs"]
                self.assertEqual(sheet["E1"].value, "Pipeline Requirement Text")
                self.assertEqual(sheet["E2"].value, "Actual extracted requirement text")
                self.assertIn("EXT-C1", sheet["G2"].value)
                self.assertEqual(sheet["N2"].value, "COMPLIANCE_MATRIX + EMPIRICAL_TEST")
                self.assertEqual(workbook["Atomic_Condition_Diagnostics"]["L2"].value, "CORRECTED")
                self.assertEqual(workbook["Contract_Field_Diagnostics"]["C2"].value, "parameter")
                self.assertEqual(workbook["Executive_Summary"]["G17"].value, 0.0)
                self.assertEqual(workbook["Executive_Summary"]["F27"].value, "False automatic closures")
                self.assertEqual(workbook["Executive_Summary"]["G27"].value, 0)
                self.assertEqual(sheet["R1"].value, "Review State")
                self.assertEqual(sheet["R2"].value, "Reviewed")
                self.assertEqual(sheet["T1"].value, "Auto-Close Eligible?")
                self.assertEqual(sheet["T2"].value, "YES")
            finally:
                workbook.close()

    def test_review_gate_safety_metrics_keep_classification_and_closure_risk_separate(self):
        predictions = {
            "REQ-001": {
                "expected": "SUPPORTED",
                "predicted": "SUPPORTED",
                "review_required": False,
                "auto_close_eligible": True,
            },
            "REQ-002": {
                "expected": "UNKNOWN",
                "predicted": "SUPPORTED",
                "review_required": True,
                "auto_close_eligible": False,
            },
            "REQ-003": {
                "expected": "PARTIAL",
                "predicted": "SUPPORTED",
                "review_required": False,
                "auto_close_eligible": True,
            },
            "REQ-004": {
                "expected": "PARTIAL",
                "predicted": "PARTIAL",
                "review_required": True,
                "auto_close_eligible": False,
            },
        }

        metrics = calculate_review_gate_safety_metrics(predictions)

        self.assertEqual(metrics["supported_predictions"], 3)
        self.assertEqual(metrics["false_supported_predictions"], 2)
        self.assertEqual(metrics["false_supported_routed_to_review"], 1)
        self.assertEqual(metrics["false_automatic_closures"], 1)
        self.assertEqual(metrics["review_gate_capture_rate"], 50.0)
        self.assertEqual(metrics["automatic_closure_precision"], 50.0)
        self.assertEqual(metrics["supported_review_rate"], 33.33)
        self.assertEqual(metrics["false_automatic_closure_requirement_ids"], ["REQ-003"])

    def test_excel_summary_uses_actual_mismatch_count(self):
        failures = [
            {
                "requirement_id": f"REQ-{idx:03d}",
                "expected_status": "SUPPORTED",
                "predicted_status": "PARTIAL",
                "failure_category": "NUMERIC_REASONING_FAILURE",
            }
            for idx in range(1, 4)
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "benchmark_audit_trace.xlsx"
            export_benchmark_audit_trace_excel(
                results={},
                ground_truth_reqs=[],
                links_by_id={},
                retrieved_by_req={},
                assessments={},
                predictions={},
                failures=failures,
                excel_path=output,
            )
            workbook = openpyxl.load_workbook(output, read_only=True)
            try:
                self.assertEqual(
                    workbook["Executive_Summary"]["B15"].value,
                    "Diagnostic Failure Root Causes (3 Mismatches)",
                )
                self.assertEqual(
                    workbook["Mismatches_Deep_Dive"].max_row - 1,
                    3,
                )
            finally:
                workbook.close()

    def test_status_and_code_normalization(self):
        """Test that various casing, spacing, and aliases normalize to standard forms."""
        self.assertEqual(normalize_status("supported"), "Supported")
        self.assertEqual(normalize_status("Verified"), "Supported")
        self.assertEqual(normalize_status("PASS"), "Supported")
        self.assertEqual(normalize_status("partial"), "Partial")
        self.assertEqual(normalize_status("missing"), "Missing")
        self.assertEqual(normalize_status("potential conflict"), "Conflict")
        self.assertEqual(normalize_status("contradiction"), "Conflict")
        self.assertEqual(normalize_status(None), "Missing")

        self.assertEqual(normalize_code("REQ_BCU_001"), "REQ-BCU-001")
        self.assertEqual(normalize_code("req-bcu-001"), "REQ-BCU-001")
        self.assertEqual(normalize_code("  REQ BCU 002  "), "REQ-BCU-002")

    def test_ground_truth_loading_and_integrity(self):
        """Test that the benchmark ground truth loads and satisfies schema constraints."""
        reqs, links, findings = load_ground_truth()

        self.assertGreaterEqual(len(reqs), 25, "Benchmark must contain at least 25 requirements")
        self.assertGreater(len(links), 0, "Benchmark must contain ground truth evidence links")

        # Verify all requirements have required fields
        for r in reqs:
            self.assertIn("requirement_id", r)
            self.assertIn("title", r)
            self.assertIn("description", r)
            self.assertIn("category", r)
            self.assertIn("severity", r)
            self.assertIn("expected_status", r)
            self.assertIn(r["expected_status"], ["Supported", "Partial", "Missing", "Conflict"])

        # Check coverage scenarios exist
        statuses = {r["expected_status"] for r in reqs}
        self.assertTrue({"Supported", "Partial", "Missing", "Conflict"}.issubset(statuses))

    def test_extraction_metrics_calculation(self):
        """Test precision, recall, and F1 calculations for extraction."""
        gt_reqs = [
            {"req_code": "REQ-001", "title": "Voltage range"},
            {"req_code": "REQ-002", "title": "Current rating"},
            {"req_code": "REQ-003", "title": "Isolation"},
            {"req_code": "REQ-004", "title": "Temperature limit"},
        ]
        # Scenario: 3 extracted, 2 match, 1 hallucinated
        extracted_mock = [
            {"req_code": "REQ-001", "title": "Voltage range"},
            {"req_code": "REQ-002", "title": "Current rating"},
            {"req_code": "REQ-999", "title": "Fake requirement"},
        ]

        metrics = evaluate_extraction(gt_reqs, extracted_mock)
        self.assertEqual(metrics.total_ground_truth, 4)
        self.assertEqual(metrics.total_extracted, 3)
        self.assertEqual(metrics.true_positives, 2)
        self.assertEqual(metrics.false_positives, 1)
        self.assertEqual(metrics.false_negatives, 2)
        # Precision = 2/3 = 66.67%, Recall = 2/4 = 50.0%, F1 = 2*(2/3*1/2)/(2/3+1/2) = 57.14%
        self.assertAlmostEqual(metrics.precision, 66.67, places=1)
        self.assertAlmostEqual(metrics.recall, 50.0, places=1)
        self.assertAlmostEqual(metrics.f1, 57.14, places=1)

    def test_evidence_matching_logic(self):
        """Test _is_evidence_hit document, page, and quote matching."""
        expected = [{"document": "Test_Report_001.pdf", "page": 5, "quote": "Tested at 24V DC with full compliance"}]

        # Match by doc & page
        hit_1 = {"document_name": "Test_Report_001.pdf", "page_number": 5, "content": "Different text"}
        self.assertTrue(_is_evidence_hit(hit_1, expected))

        # Mismatch by page
        miss_page = {"document_name": "Test_Report_001.pdf", "page_number": 12, "content": "Unrelated"}
        self.assertFalse(_is_evidence_hit(miss_page, expected))

        # Match by quote overlap even if document name varies
        hit_quote = {"document_name": "Other_Doc.pdf", "page_number": 1, "content": "Tested at 24V DC with full compliance and zero faults"}
        self.assertTrue(_is_evidence_hit(hit_quote, expected))

    def test_retrieval_metrics_recall_at_k_and_mrr(self):
        """Test Recall@1, Recall@3, Recall@5, and MRR calculations."""
        gt_links = [
            {"req_code": "REQ-001", "document": "Report_A.pdf", "page": 1, "quote": ""},
            {"req_code": "REQ-002", "document": "Report_B.pdf", "page": 2, "quote": ""},
            {"req_code": "REQ-003", "document": "Report_C.pdf", "page": 3, "quote": ""},
        ]
        retrieved_by_req = {
            # Hit at rank 1
            "REQ-001": [
                {"document_name": "Report_A.pdf", "page_number": 1, "content": ""},
                {"document_name": "Other.pdf", "page_number": 1, "content": ""},
            ],
            # Hit at rank 3
            "REQ-002": [
                {"document_name": "Other1.pdf", "page_number": 1, "content": ""},
                {"document_name": "Other2.pdf", "page_number": 1, "content": ""},
                {"document_name": "Report_B.pdf", "page_number": 2, "content": ""},
            ],
            # No hit
            "REQ-003": [
                {"document_name": "Other1.pdf", "page_number": 1, "content": ""},
                {"document_name": "Other2.pdf", "page_number": 1, "content": ""},
            ],
        }

        metrics = evaluate_retrieval(gt_links, retrieved_by_req)
        self.assertEqual(metrics.total_queries, 3)
        # Recall@1 = 1/3 = 33.33%
        self.assertAlmostEqual(metrics.recall_at_1, 33.33, places=1)
        # Recall@3 = 2/3 = 66.67%
        self.assertAlmostEqual(metrics.recall_at_3, 66.67, places=1)
        # MRR = (1/1 + 1/3 + 0) / 3 = (1.3333)/3 = 0.4444
        self.assertAlmostEqual(metrics.mean_reciprocal_rank, 0.4444, places=2)

    def test_verification_confusion_matrix_and_metrics(self):
        """Test accuracy and confusion matrix calculation."""
        gt_reqs = [
            {"req_code": "REQ-001", "expected_status": "Supported"},
            {"req_code": "REQ-002", "expected_status": "Supported"},
            {"req_code": "REQ-003", "expected_status": "Partial"},
            {"req_code": "REQ-004", "expected_status": "Missing"},
        ]
        actual_preds = {
            "REQ-001": "Supported",  # TP Supported
            "REQ-002": "Partial",    # Error: Expected Supported, Actual Partial
            "REQ-003": "Partial",    # TP Partial
            "REQ-004": "Missing",    # TP Missing
        }

        metrics = evaluate_verification(gt_reqs, actual_preds)
        self.assertEqual(metrics.total_evaluated, 4)
        # Accuracy: 3/4 = 75.0%
        self.assertEqual(metrics.accuracy, 75.0)
        self.assertEqual(metrics.confusion_matrix["Supported"]["Supported"], 1)
        self.assertEqual(metrics.confusion_matrix["Supported"]["Partial"], 1)
        self.assertEqual(metrics.confusion_matrix["Partial"]["Partial"], 1)
        self.assertEqual(metrics.confusion_matrix["Missing"]["Missing"], 1)

    def test_unsupported_claim_hallucination_rate(self):
        """Test unsupported claim rate calculation when evidence is missing."""
        gt_reqs = [
            {"req_code": "REQ-001", "expected_status": "Supported"},
            {"req_code": "REQ-002", "expected_status": "Missing"},
            {"req_code": "REQ-003", "expected_status": "Missing"},
        ]
        # 1 correct missing, 1 falsely claimed as Supported (hallucination)
        actual_preds = {
            "REQ-001": "Supported",
            "REQ-002": "Missing",
            "REQ-003": "Supported",  # Hallucinated verification
        }

        spec_metrics = evaluate_specialty_metrics(gt_reqs, actual_preds)
        self.assertEqual(spec_metrics.total_missing_in_gt, 2)
        self.assertEqual(spec_metrics.unsupported_claims_count, 1)
        # Unsupported claim rate = 1/2 = 50.0%
        self.assertEqual(spec_metrics.unsupported_claim_rate, 50.0)

    def test_failure_classification(self):
        """Test failure categorization into unsupported_claim, conflict_error, retrieval_error, etc."""
        gt_missing = {"req_code": "REQ-M01", "title": "Missing check", "expected_status": "Missing"}
        fail_unsupported = classify_failure(gt_missing, "Supported", [], [])
        self.assertIsNotNone(fail_unsupported)
        self.assertEqual(fail_unsupported.error_type, "unsupported_claim")

        gt_conflict = {"req_code": "REQ-C01", "title": "Voltage conflict", "expected_status": "Conflict"}
        fail_conflict = classify_failure(gt_conflict, "Supported", [], [])
        self.assertIsNotNone(fail_conflict)
        self.assertEqual(fail_conflict.error_type, "conflict_detection_error")

        gt_retrieval = {"req_code": "REQ-R01", "title": "Tested check", "expected_status": "Supported"}
        expected_ev = [{"document": "Target.pdf", "page": 3, "quote": ""}]
        retrieved_wrong = [{"document_name": "Wrong.pdf", "page_number": 1, "content": ""}]
        fail_retrieval = classify_failure(gt_retrieval, "Missing", retrieved_wrong, expected_ev)
        self.assertIsNotNone(fail_retrieval)
        self.assertEqual(fail_retrieval.error_type, "retrieval_error")

    def test_markdown_report_formatting(self):
        """Test Markdown report generator produces valid table structure."""
        sample_data = {
            "metadata": {"timestamp": "2026-08-21T12:00:00Z", "model": "test-model"},
            "dataset": {"requirements_count": 30, "documents_count": 7, "total_chunks_indexed": 45},
            "extraction": {"precision": 100.0, "recall": 100.0, "f1": 100.0},
            "retrieval": {"recall_at_1": 90.0, "recall_at_3": 100.0, "recall_at_5": 100.0, "mean_reciprocal_rank": 0.95},
            "verification": {"accuracy": 93.3, "macro_f1": 92.5, "confusion_matrix": {
                "Supported": {"Supported": 10, "Partial": 0, "Missing": 0, "Conflict": 0},
                "Partial": {"Supported": 0, "Partial": 6, "Missing": 0, "Conflict": 0},
                "Missing": {"Supported": 0, "Partial": 0, "Missing": 7, "Conflict": 0},
                "Conflict": {"Supported": 1, "Partial": 0, "Missing": 0, "Conflict": 4},
            }},
            "specialty": {"conflict_f1": 88.8, "missing_f1": 100.0, "unsupported_claim_rate": 0.0},
            "failures": [],
        }
        md = generate_markdown_report(sample_data)
        self.assertIn("# TRACEAUDIT BENCHMARK REPORT", md)
        self.assertIn("Executive Metrics Summary", md)
        self.assertIn("Verification Confusion Matrix", md)


if __name__ == "__main__":
    unittest.main()
