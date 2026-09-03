"""Regression tests for the FMVSS 305 public-document benchmark definition."""

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from evaluation.run_fmvss305_benchmark import (
    BENCHMARK_DIR,
    CONDITION_INPUT_FIELDS,
    SAMPLE_DIR,
    _clean_condition,
    _atomic_metrics,
    _match_extracted_requirement,
    _oracle_evidence,
    _retrieval_metrics,
    validate_dataset,
)
from app.schemas.contract import parse_requirement_contract
from app.schemas.verification_result import ConditionVerificationResult
from app.services.document_classifier import _heuristic_profile
from app.services.evidence_qualification import qualify_evidence
from app.services.verification_reasoner import (
    _focused_logic_retry_prompt,
    _logic_retry_condition_ids,
)


class Fmvss305BenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with (BENCHMARK_DIR / "ground_truth.json").open("r", encoding="utf-8") as handle:
            cls.dataset = json.load(handle)
        cls.requirements = cls.dataset["requirements"]

    def test_dataset_and_public_documents_validate(self) -> None:
        result = validate_dataset(self.dataset)
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(result["requirements"], 11)
        self.assertEqual(result["atomic_conditions"], 30)

    def test_source_files_exist(self) -> None:
        for document in self.dataset["documents"].values():
            self.assertTrue((SAMPLE_DIR / document["filename"]).is_file())

    def test_ids_are_unique_and_every_condition_is_labelled(self) -> None:
        requirement_ids = [item["requirement_id"] for item in self.requirements]
        self.assertEqual(len(requirement_ids), len(set(requirement_ids)))
        condition_ids = [
            condition["condition_id"]
            for requirement in self.requirements
            for condition in requirement["conditions"]
        ]
        self.assertEqual(len(condition_ids), len(set(condition_ids)))
        self.assertTrue(all(
            condition.get("expected_status")
            for requirement in self.requirements
            for condition in requirement["conditions"]
        ))

    def test_regulatory_alternative_is_not_flattened_in_ground_truth(self) -> None:
        requirement = next(
            item for item in self.requirements if item["requirement_id"] == "FMVSS-305-S5.3"
        )
        self.assertEqual(requirement["logic"]["operator"], "ANY_OF")
        self.assertEqual(requirement["expected_status"], "SUPPORTED")
        statuses = {item["expected_status"] for item in requirement["conditions"]}
        self.assertEqual(statuses, {"PROVEN", "NOT_APPLICABLE"})

    def test_conditional_logic_is_explicit(self) -> None:
        conditional = [
            item for item in self.requirements if item["logic"]["operator"] == "IF_THEN"
        ]
        self.assertEqual(len(conditional), 3)
        for requirement in conditional:
            self.assertIn("if_condition_id", requirement["logic"])
            self.assertTrue(requirement["logic"]["then_condition_ids"])

    def test_any_of_unresolved_branch_requests_focused_retry(self) -> None:
        requirement = next(
            item for item in self.requirements if item["requirement_id"] == "FMVSS-305-S5.3"
        )
        contract = parse_requirement_contract(
            req_code=requirement["requirement_id"],
            title=requirement["title"],
            description=requirement["requirement_text"],
            structured_conditions=[_clean_condition(item) for item in requirement["conditions"]],
            logic=requirement["logic"],
        )
        results = [
            ConditionVerificationResult(condition_id=condition.condition_id, status="INCONCLUSIVE")
            for condition in contract.atomic_conditions
        ]
        targets = _logic_retry_condition_ids(contract, results)
        self.assertEqual(targets, contract.logic.condition_ids)
        prompt = _focused_logic_retry_prompt("BASE", contract, results, targets)
        self.assertIn("mark genuinely unused alternatives NOT_APPLICABLE", prompt)

    def test_if_then_retry_targets_unresolved_consequent(self) -> None:
        requirement = next(
            item for item in self.requirements
            if item["logic"]["operator"] == "IF_THEN"
        )
        contract = parse_requirement_contract(
            req_code=requirement["requirement_id"],
            title=requirement["title"],
            description=requirement["requirement_text"],
            structured_conditions=[_clean_condition(item) for item in requirement["conditions"]],
            logic=requirement["logic"],
        )
        results = []
        for condition in contract.atomic_conditions:
            status = "PROVEN"
            if condition.condition_id == contract.logic.then_condition_ids[-1]:
                status = "INCONCLUSIVE"
            results.append(ConditionVerificationResult(condition_id=condition.condition_id, status=status))
        self.assertEqual(
            _logic_retry_condition_ids(contract, results),
            [contract.logic.then_condition_ids[-1]],
        )

    def test_oracle_contract_preserves_regulatory_logic(self) -> None:
        requirement = next(
            item for item in self.requirements if item["requirement_id"] == "FMVSS-305-S5.3"
        )
        contract = parse_requirement_contract(
            req_code=requirement["requirement_id"],
            title=requirement["title"],
            description=requirement["requirement_text"],
            structured_conditions=[_clean_condition(item) for item in requirement["conditions"]],
            logic=requirement["logic"],
        )
        self.assertEqual(contract.logic.operator, "ANY_OF")
        self.assertEqual(contract.logic.condition_ids, requirement["logic"]["condition_ids"])

    def test_oracle_evidence_selects_best_passage_not_every_page_chunk(self) -> None:
        requirement = {
            "conditions": [{"evidence": [{"page": 4, "quote": "measured isolation 1200 ohms per volt"}]}],
        }
        chunks = [
            {"id": "noise", "page_number": 4, "content": "unrelated footer", "metadata": {}},
            {"id": "match", "page_number": 4, "content": "Measured isolation 1200 Ohms per Volt", "metadata": {}},
        ]
        selected = _oracle_evidence(requirement, chunks)
        self.assertEqual([item["id"] for item in selected], ["match"])

    def test_retrieval_recall_measures_all_expected_pages(self) -> None:
        requirement = {
            "requirement_id": "R1",
            "conditions": [{"evidence": [{"page": 1}, {"page": 2}]}],
        }
        metrics = _retrieval_metrics([requirement], {"R1": [
            {"page_number": 1},
            {"page_number": 9},
            {"page_number": 8},
        ]})
        self.assertEqual(metrics["requirement_any_hit_at_3"], 100.0)
        self.assertEqual(metrics["requirement_full_coverage_at_3"], 0.0)
        self.assertEqual(metrics["recall_at_3"], 50.0)

    def test_visual_only_case_requires_human_review(self) -> None:
        requirement = next(
            item for item in self.requirements
            if item["requirement_id"] == "FMVSS-305-S5.4.1.1"
        )
        self.assertTrue(requirement["requires_visual_evidence"])
        self.assertEqual(requirement["expected_status"], "UNKNOWN")
        self.assertEqual(requirement["expected_review_state"], "Needs review")

    def test_evaluator_labels_are_removed_from_pipeline_conditions(self) -> None:
        condition = self.requirements[0]["conditions"][0]
        cleaned = _clean_condition(condition)
        self.assertLessEqual(set(cleaned), CONDITION_INPUT_FIELDS)
        self.assertNotIn("expected_status", cleaned)
        self.assertNotIn("evidence", cleaned)
        self.assertNotIn("expected_source_authority", cleaned)

    def test_condition_alignment_normalizes_punctuation(self) -> None:
        requirement = {
            "requirement_id": "R1",
            "conditions": [{
                "condition_id": "S5.1-C1",
                "description": "Electrolyte spillage is at most five liters.",
                "expected_status": "PROVEN",
            }],
        }
        metrics = _atomic_metrics({"R1": requirement}.values(), {
            "R1": [ConditionVerificationResult(condition_id="S5.1_C1", status="PROVEN")],
        })
        self.assertEqual(metrics["aligned_accuracy"], 100.0)
        self.assertEqual(metrics["alignment_coverage"], 100.0)
        self.assertEqual(metrics["alignment_methods"], {"canonical_id": 1})

    def test_unaligned_condition_is_not_credited_as_untested(self) -> None:
        requirement = {
            "requirement_id": "R1",
            "conditions": [{
                "condition_id": "C1",
                "description": "An intentionally absent extracted condition.",
                "expected_status": "UNTESTED",
            }],
        }
        metrics = _atomic_metrics([requirement], {"R1": []})
        self.assertEqual(metrics["correct"], 0)
        self.assertEqual(metrics["alignment_coverage"], 0.0)
        self.assertEqual(
            metrics["mismatches"][0]["predicted"],
            "NOT_EXTRACTED_OR_UNALIGNED",
        )

    def test_explicit_regulatory_clause_mismatch_is_not_semantically_matched(self) -> None:
        truth = {
            "clause": "S7.1(c)",
            "requirement_text": "An onboard-only recharge alternative applies.",
        }
        wrong = SimpleNamespace(
            req_code="S5.4.6.2",
            description="An onboard charging warning applies.",
            title="Charging warning",
        )
        index, score = _match_extracted_requirement(truth, [wrong], set())
        self.assertIsNone(index)
        self.assertEqual(score, 0.0)

    def test_opaque_filename_is_profiled_from_test_report_content(self) -> None:
        profile = _heuristic_profile("x9-8842.bin.pdf", [{
            "id": "p1",
            "page_number": 1,
            "content": (
                "FINAL TEST REPORT\nSummary of Test Results\nTest Vehicle: Example EV\n"
                "Test Date: 2024-11-27\nMeasured value 0.0 V. Passed."
            ),
        }])
        self.assertEqual(profile.primary_role, "TEST_REPORT")
        self.assertEqual(profile.verification_basis, "physical_test")
        self.assertTrue(profile.classification_evidence)

    def test_profiled_test_report_keeps_calculation_and_authority_separate(self) -> None:
        profile = _heuristic_profile("opaque.pdf", [{
            "id": "p1",
            "page_number": 1,
            "content": (
                "FINAL TEST REPORT\nSummary of Test Results\n"
                "Electrical isolation was measured after the rollover impact test. "
                "Ri/Vb was calculated from measured voltages and passed 500 ohms/volt."
            ),
        }])
        contract = parse_requirement_contract(
            req_code="S5.3",
            title="Electrical isolation after crash",
            description="Electrical isolation shall be at least 500 ohms/volt after the rollover impact test.",
            structured_conditions=[{
                "condition_id": "S5.3-C1",
                "description": "Electrical isolation is at least 500 ohms/volt.",
                "parameter": "isolation",
                "operator": ">=",
                "threshold": 500,
                "unit": "ohms/volt",
            }],
        )
        qualification = qualify_evidence(
            contract=contract,
            evidence_id="E1",
            document_name="opaque.pdf",
            doc_type="Unclassified",
            content=(
                "Electrical isolation was measured after the rollover impact test. "
                "Ri/Vb was calculated from measured voltages and passed 500 ohms/volt."
            ),
            document_profile=profile.model_dump(),
        )
        self.assertEqual(qualification.source_authority, "EMPIRICAL_TEST")
        self.assertEqual(qualification.passage_modality, "derived_test_calculation")
        self.assertEqual(qualification.verification_method, "physical_test")
        self.assertEqual(qualification.qualification_status, "QUALIFIED")


if __name__ == "__main__":
    unittest.main()
