"""Lossless, answer-free extraction payload shared by production and evaluation."""

from typing import Any


def extraction_contract_payload(requirement: Any) -> dict[str, Any]:
    return requirement.model_dump(include={
        "conditions", "semantic_clauses", "clause_coverage", "unmapped_obligations",
        "contract_complete", "logic", "logic_tree", "decomposition_confidence",
        "ambiguities", "validation_issues", "decomposition_method",
        "contract_schema_version", "contract_source_sha256",
    })
