"""Structural and provenance validation for the multimodal benchmark."""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent
sys.path.insert(0, str(REPO / "backend"))

from app.services.ingestion import parse_document_with_metadata


FINAL = {"SUPPORTED", "PARTIAL", "CONFLICT", "MISSING", "UNKNOWN"}
ATOMIC = {"PROVEN", "FAILED", "PENDING", "UNTESTED", "INCONCLUSIVE", "NOT_APPLICABLE"}


def tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(value or "").lower().replace("µ", "u"))


def coverage(quote: str, content: str) -> float:
    expected = tokens(quote)
    actual = set(tokens(content))
    return sum(item in actual for item in expected) / len(expected) if expected else 0.0


def validate() -> dict[str, Any]:
    dataset = json.loads((ROOT / "ground_truth.json").read_text(encoding="utf-8"))
    errors: list[str] = []
    warnings: list[str] = []
    documents = [dataset["documents"]["requirements"], *dataset["documents"]["evidence"]]
    parsed: dict[str, Any] = {}
    page_content: dict[tuple[str, int], str] = {}
    page_block_types: dict[tuple[str, int], set[str]] = defaultdict(set)

    for document in documents:
        filename = document["filename"]
        path = ROOT / "documents" / filename
        if not path.exists():
            errors.append(f"Missing document: {filename}")
            continue
        result = parse_document_with_metadata(str(path))
        parsed[filename] = result
        grouped: dict[int, list[str]] = defaultdict(list)
        for chunk in result.chunks:
            if chunk.page_number is None:
                continue
            grouped[chunk.page_number].append(chunk.content)
            page_block_types[(filename, chunk.page_number)].add(str(chunk.metadata.get("block_type") or ""))
        for page, contents in grouped.items():
            page_content[(filename, page)] = "\n".join(contents)

    requirements = dataset.get("requirements", [])
    ids = [item.get("requirement_id") for item in requirements]
    if len(ids) != len(set(ids)):
        errors.append("Requirement IDs are not unique")
    condition_ids: list[str] = []
    quote_checks: list[dict[str, Any]] = []
    requirements_file = dataset["documents"]["requirements"]["filename"]

    for requirement in requirements:
        req_id = requirement.get("requirement_id", "<missing>")
        if requirement.get("expected_status") not in FINAL:
            errors.append(f"{req_id}: invalid final status")
        if (requirements_file, int(requirement.get("requirement_page") or 0)) not in page_content:
            errors.append(f"{req_id}: requirement page does not exist")
        logic = requirement.get("logic") or {}
        actual_ids = {item.get("condition_id") for item in requirement.get("conditions", [])}
        if logic.get("operator") == "IF_THEN":
            declared = {logic.get("if_condition_id"), *(logic.get("then_condition_ids") or [])}
        else:
            declared = set(logic.get("condition_ids") or [])
        if actual_ids != declared:
            errors.append(f"{req_id}: logic does not reference exactly its condition IDs")
        for condition in requirement.get("conditions", []):
            cid = condition.get("condition_id")
            condition_ids.append(cid)
            if condition.get("expected_status") not in ATOMIC:
                errors.append(f"{req_id}/{cid}: invalid atomic status")
            if not condition.get("evidence"):
                errors.append(f"{req_id}/{cid}: no provenance annotation")
            for evidence in condition.get("evidence", []):
                key = (evidence.get("document"), int(evidence.get("page") or 0))
                if key not in page_content:
                    errors.append(f"{req_id}/{cid}: nonexistent evidence location {key}")
                    continue
                score = coverage(evidence.get("quote", ""), page_content[key])
                quote_checks.append({
                    "requirement_id": req_id,
                    "condition_id": cid,
                    "document": key[0],
                    "page": key[1],
                    "annotated_block_type": evidence.get("block_type"),
                    "parsed_block_types": sorted(page_block_types[key]),
                    "token_coverage": round(score, 3),
                })
                # Figure labels can be raster-only, but their nearby caption must still anchor the page.
                threshold = 0.45 if evidence.get("block_type") == "figure" else 0.60
                if score < threshold:
                    warnings.append(f"{req_id}/{cid} {key[0]} p{key[1]} quote coverage {score:.2f}")

    if len(condition_ids) != len(set(condition_ids)):
        errors.append("Atomic condition IDs are not globally unique")

    diagnostics = {
        filename: {
            "page_count": result.page_count,
            "chunk_count": len(result.chunks),
            "parser_backend": result.diagnostics.get("parser_backend"),
            "tables_detected": result.diagnostics.get("tables_detected", 0),
            "figures_detected": result.diagnostics.get("figures_detected", 0),
            "table_structure_coverage": result.diagnostics.get("table_structure_coverage", 0.0),
        }
        for filename, result in parsed.items()
    }
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "documents": len(documents),
            "requirements": len(requirements),
            "atomic_conditions": len(condition_ids),
            "tables_detected": sum(item["tables_detected"] for item in diagnostics.values()),
            "figures_detected": sum(item["figures_detected"] for item in diagnostics.values()),
        },
        "final_distribution": dict(Counter(item["expected_status"] for item in requirements)),
        "atomic_distribution": dict(Counter(condition["expected_status"] for item in requirements for condition in item["conditions"])),
        "document_diagnostics": diagnostics,
        "quote_checks": quote_checks,
    }


def main() -> int:
    result = validate()
    output = ROOT / "validation_report.json"
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("valid", "errors", "warnings", "counts", "final_distribution", "atomic_distribution", "document_diagnostics")}, indent=2))
    print(f"Full report: {output}")
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
