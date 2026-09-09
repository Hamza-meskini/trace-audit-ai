"""Validate corpus integrity without grading the extraction pipeline."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pymupdf


ROOT = Path(__file__).resolve().parent
EXPECTED = {
    "documents": 8,
    "pages": 32,
    "requirements": 48,
    "atomic_conditions": 147,
    "authored_tables": 16,
    "authored_figures": 12,
}
LEAKAGE_PATTERNS = {
    "atomic condition identifier": re.compile(r"(?i)\bC\d{1,3}\b"),
    "logic operator": re.compile(r"\b(?:ALL_OF|ANY_OF|IF_THEN)\b"),
    "evaluator verdict": re.compile(r"\b(?:PROVEN|UNTESTED|INCONCLUSIVE|expected_status)\b"),
}


def validate() -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    ground_truth_path = ROOT / "ground_truth.json"
    if not ground_truth_path.exists():
        return {"valid": False, "errors": ["ground_truth.json is missing; run the generator"], "warnings": [], "counts": {}}
    dataset = json.loads(ground_truth_path.read_text(encoding="utf-8"))
    documents = dataset.get("documents", [])
    requirements = dataset.get("requirements", [])
    total_pages = 0
    native_text: dict[str, str] = {}

    for document in documents:
        path = ROOT / "documents" / document["filename"]
        if not path.exists():
            errors.append(f"Missing PDF: {document['filename']}")
            continue
        reader = pymupdf.open(str(path))
        total_pages += reader.page_count
        if reader.page_count != 4:
            errors.append(f"{document['filename']}: expected 4 pages, found {reader.page_count}")
        text = "\n".join(page.get_text() or "" for page in reader)
        native_text[document["filename"]] = text
        for label, pattern in LEAKAGE_PATTERNS.items():
            match = pattern.search(text)
            if match:
                errors.append(f"{document['filename']}: leaked {label} token {match.group(0)!r}")

    requirement_ids = [item.get("requirement_id") for item in requirements]
    if len(requirement_ids) != len(set(requirement_ids)):
        errors.append("Requirement IDs are not unique")
    condition_ids = [condition.get("condition_id") for item in requirements for condition in item.get("conditions", [])]
    if len(condition_ids) != len(set(condition_ids)):
        errors.append("Atomic condition IDs are not globally unique")

    document_names = {item["filename"] for item in documents}
    for requirement in requirements:
        req_id = requirement.get("requirement_id", "<missing>")
        if requirement.get("document") not in document_names:
            errors.append(f"{req_id}: unknown source document")
        if requirement.get("source_page") not in {2, 3, 4}:
            errors.append(f"{req_id}: invalid source page")
        conditions = requirement.get("conditions", [])
        if not 2 <= len(conditions) <= 5:
            errors.append(f"{req_id}: expected two to five atomic conditions")
        actual_ids = {item.get("condition_id") for item in conditions}
        logic = requirement.get("logic") or {}
        declared = set(logic.get("condition_ids") or [])
        if logic.get("operator") == "IF_THEN":
            declared |= {logic.get("if_condition_id"), *(logic.get("then_condition_ids") or [])}
        if actual_ids != declared:
            errors.append(f"{req_id}: logic does not reference exactly its atomic conditions")
        # Body and table requirements should exist in the native text layer.
        if requirement.get("source_modality") in {"body_text", "two_column_text", "table"}:
            if req_id not in native_text.get(requirement["document"], ""):
                errors.append(f"{req_id}: missing from native PDF text")

    counts = {
        "documents": len(documents),
        "pages": total_pages,
        "requirements": len(requirements),
        "atomic_conditions": len(condition_ids),
        "authored_tables": sum(int(item.get("expected_tables", 0)) for item in documents),
        "authored_figures": sum(int(item.get("expected_figures", 0)) for item in documents),
    }
    for key, expected in EXPECTED.items():
        if counts.get(key) != expected:
            errors.append(f"Expected {expected} {key}, found {counts.get(key)}")

    modality_counts = Counter(item.get("source_modality") for item in requirements)
    logic_counts = Counter(item.get("logic", {}).get("operator") for item in requirements)
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "counts": counts,
        "modality_distribution": dict(modality_counts),
        "logic_distribution": dict(logic_counts),
    }


def main() -> int:
    result = validate()
    (ROOT / "validation_report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
