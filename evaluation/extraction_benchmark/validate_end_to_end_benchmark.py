"""Validate the complete Nova requirement-and-evidence benchmark corpus."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pymupdf

try:
    from .validate_extraction_benchmark import validate as validate_extraction
except ImportError:  # Direct script execution from this directory.
    from validate_extraction_benchmark import validate as validate_extraction


ROOT = Path(__file__).resolve().parent
EXPECTED = {
    "requirement_documents": 8,
    "evidence_documents": 4,
    "documents": 12,
    "pages": 52,
    "requirements": 48,
    "atomic_conditions": 147,
    "authored_tables": 28,
    "authored_figures": 16,
}
ATOMIC_STATUSES = {"PROVEN", "FAILED", "PENDING", "UNTESTED", "INCONCLUSIVE"}
FINAL_STATUSES = {"SUPPORTED", "PARTIAL", "CONFLICT", "MISSING", "UNKNOWN"}


def quote_visible(quote: str, text: str) -> bool:
    expected = re.findall(r"[a-z0-9]+", quote.lower())
    visible = set(re.findall(r"[a-z0-9]+", text.lower()))
    return bool(expected) and sum(token in visible for token in expected) / len(expected) >= 0.92


def validate() -> dict:
    base = validate_extraction()
    errors = list(base["errors"])
    warnings = list(base["warnings"])
    path = ROOT / "ground_truth.json"
    if not path.exists():
        return {"valid": False, "errors": ["ground_truth.json is missing"], "warnings": [], "counts": {}}
    dataset = json.loads(path.read_text(encoding="utf-8"))
    requirement_documents = dataset.get("documents", [])
    evidence_documents = dataset.get("evidence_documents", [])
    requirements = dataset.get("requirements", [])
    evidence_names = {item["filename"] for item in evidence_documents}
    pages = sum(int(item.get("expected_pages", 4)) for item in requirement_documents)

    native_text: dict[str, str] = {}
    for document in evidence_documents:
        pdf_path = ROOT / "documents" / document["filename"]
        if not pdf_path.exists():
            errors.append(f"Missing evidence PDF: {document['filename']}")
            continue
        reader = pymupdf.open(str(pdf_path))
        pages += reader.page_count
        if reader.page_count != document.get("expected_pages"):
            errors.append(f"{document['filename']}: expected {document.get('expected_pages')} pages, found {reader.page_count}")
        native_text[document["filename"]] = "\n".join(page.get_text() or "" for page in reader)
        if not any(page.get_images(full=True) for page in reader):
            errors.append(f"{document['filename']}: expected an embedded visual evidence record")

    condition_ids: list[str] = []
    for requirement in requirements:
        if requirement.get("expected_status") not in FINAL_STATUSES:
            errors.append(f"{requirement.get('requirement_id')}: missing or invalid final status")
        if requirement.get("expected_review_state") not in {"Reviewed", "Needs review"}:
            errors.append(f"{requirement.get('requirement_id')}: invalid review state")
        for condition in requirement.get("conditions", []):
            condition_ids.append(str(condition.get("condition_id")))
            status = condition.get("expected_status")
            if status not in ATOMIC_STATUSES:
                errors.append(f"{condition.get('condition_id')}: missing or invalid atomic status")
            evidence = condition.get("evidence") or []
            if not evidence:
                errors.append(f"{condition.get('condition_id')}: no evidence annotation")
            for annotation in evidence:
                if annotation.get("document") not in evidence_names:
                    errors.append(f"{condition.get('condition_id')}: unknown evidence document")
                if annotation.get("page") not in {2, 3, 4, 5}:
                    errors.append(f"{condition.get('condition_id')}: invalid evidence page")
                if annotation.get("block_type") == "table":
                    quote = str(annotation.get("quote") or "")
                    if not quote_visible(quote, native_text.get(annotation.get("document"), "")):
                        warnings.append(f"{condition.get('condition_id')}: table quote was wrapped or normalized by PDF extraction")

    counts = {
        "requirement_documents": len(requirement_documents),
        "evidence_documents": len(evidence_documents),
        "documents": len(requirement_documents) + len(evidence_documents),
        "pages": pages,
        "requirements": len(requirements),
        "atomic_conditions": len(condition_ids),
        "authored_tables": sum(int(item.get("expected_tables", 0)) for item in requirement_documents + evidence_documents),
        "authored_figures": sum(int(item.get("expected_figures", 0)) for item in requirement_documents + evidence_documents),
    }
    for key, expected in EXPECTED.items():
        if counts.get(key) != expected:
            errors.append(f"Expected {expected} {key}, found {counts.get(key)}")

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "counts": counts,
        "final_distribution": dict(Counter(item.get("expected_status") for item in requirements)),
        "atomic_distribution": dict(Counter(condition.get("expected_status") for item in requirements for condition in item.get("conditions", []))),
        "logic_distribution": dict(Counter(item.get("logic", {}).get("operator") for item in requirements)),
    }


def main() -> int:
    result = validate()
    (ROOT / "end_to_end_validation_report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
