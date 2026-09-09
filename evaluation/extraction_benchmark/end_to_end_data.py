"""Evaluator-only annotations for the Nova end-to-end benchmark.

The requirement PDFs remain free of atomic IDs and verdict labels.  This module
adds a synthetic evidence corpus and frozen expected outcomes to the existing
48-requirement extraction benchmark.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from benchmark_data import materialize_ground_truth


EVIDENCE_DOCUMENTS = [
    {
        "filename": "09_Nova_Electrical_Thermal_Verification_Report.pdf",
        "title": "Nova Electrical and Thermal Verification Report",
        "domains": ["Electrical safety", "Thermal control"],
    },
    {
        "filename": "10_Nova_Network_Environmental_Qualification_Report.pdf",
        "title": "Nova Network and Environmental Qualification Report",
        "domains": ["Communications", "Environmental qualification"],
    },
    {
        "filename": "11_Nova_Safety_Cybersecurity_Assurance_Report.pdf",
        "title": "Nova Safety and Cybersecurity Assurance Report",
        "domains": ["Functional safety", "Cybersecurity"],
    },
    {
        "filename": "12_Nova_Charging_Manufacturing_Acceptance_Report.pdf",
        "title": "Nova Charging and Manufacturing Acceptance Report",
        "domains": ["Charging", "Manufacturing quality"],
    },
]

FINAL_CYCLE = ("SUPPORTED", "PARTIAL", "CONFLICT", "MISSING", "UNKNOWN")


def condition_statuses(final_status: str, count: int, logic_operator: str) -> list[str]:
    if final_status == "SUPPORTED":
        return ["PROVEN"] * count
    if final_status == "PARTIAL":
        if logic_operator == "IF_THEN":
            return ["PROVEN", "PENDING", *(["UNTESTED"] * (count - 2))]
        if logic_operator == "ANY_OF":
            return ["PENDING", *(["UNTESTED"] * (count - 1))]
        return ["PROVEN", *(["UNTESTED"] * (count - 1))]
    if final_status == "CONFLICT":
        return [*(["PROVEN"] * (count - 1)), "FAILED"]
    if final_status == "MISSING":
        return ["UNTESTED"] * count
    return ["INCONCLUSIVE"] * count


def evidence_statement(status: str, requirement_id: str, description: str) -> str:
    if status == "PROVEN":
        return f"PASS - Executed for {requirement_id}; the recorded result confirms: {description}."
    if status == "FAILED":
        return f"FAIL - Executed for {requirement_id}; the observed result does not satisfy: {description}."
    if status == "UNTESTED":
        return f"NOT TESTED - No test or inspection was performed for {requirement_id}: {description}."
    if status == "PENDING":
        return f"IN PROGRESS - Partial execution exists for {requirement_id}, but verification is not complete: {description}."
    return f"INCONCLUSIVE - Work was attempted for {requirement_id}, but incomplete scope or calibration prevents deciding: {description}."


def materialize_end_to_end_ground_truth() -> dict[str, Any]:
    dataset = deepcopy(materialize_ground_truth())
    dataset.update({
        "benchmark_id": "traceaudit-nova-end-to-end-v1",
        "version": "2.0.0",
        "purpose": (
            "Measure ingestion, multimodal requirement extraction, atomic decomposition, "
            "document profiling, evidence retrieval, condition verification, aggregation, "
            "citation grounding, and review-gate safety."
        ),
        "evidence_documents": [],
    })

    document_by_domain: dict[str, dict[str, Any]] = {}
    for spec in EVIDENCE_DOCUMENTS:
        row = {
            **spec,
            "doc_type": "Test report",
            "expected_role": "TEST_REPORT",
            "expected_verification_basis": "physical_test",
            "expected_pages": 5,
            "expected_tables": 3,
            "expected_figures": 1,
        }
        dataset["evidence_documents"].append(row)
        for domain in spec["domains"]:
            document_by_domain[domain] = row

    domain_nonvisual_positions: dict[str, int] = {}
    for index, requirement in enumerate(dataset["requirements"]):
        requirement["clause"] = requirement["requirement_id"]
        expected_final = FINAL_CYCLE[index % len(FINAL_CYCLE)]
        requirement["expected_status"] = expected_final
        requirement["expected_review_state"] = (
            "Reviewed" if expected_final in {"SUPPORTED", "CONFLICT"} else "Needs review"
        )
        evidence_document = document_by_domain[requirement["category"]]
        is_visual = requirement["source_modality"] == "figure"
        if is_visual:
            evidence_page = 5
            modality = "figure"
        else:
            position = domain_nonvisual_positions.get(evidence_document["filename"], 0)
            domain_nonvisual_positions[evidence_document["filename"]] = position + 1
            evidence_page = 2 + min(position // 4, 2)
            modality = "table"

        statuses = condition_statuses(
            expected_final,
            len(requirement["conditions"]),
            requirement["logic"]["operator"],
        )
        for condition, status in zip(requirement["conditions"], statuses):
            quote = evidence_statement(status, requirement["requirement_id"], condition["description"])
            condition["expected_status"] = status
            condition["evidence"] = [{
                "document": evidence_document["filename"],
                "page": evidence_page,
                "block_type": modality,
                "role": "supporting" if status == "PROVEN" else "adverse_or_unresolved",
                "quote": quote,
            }]

    return dataset


def requirements_for_evidence_document(dataset: dict[str, Any], filename: str) -> list[dict[str, Any]]:
    return [
        requirement for requirement in dataset["requirements"]
        if any(
            evidence.get("document") == filename
            for condition in requirement["conditions"]
            for evidence in condition.get("evidence", [])
        )
    ]


def evidence_asset_name(filename: str) -> str:
    return f"{Path(filename).stem}_visual_record.png"
