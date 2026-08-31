"""Public-document FMVSS 305 benchmark for the TraceAudit pipeline.

This benchmark deliberately separates four questions that are easy to conflate:

1. Did extraction preserve the selected regulatory obligations?
2. Did retrieval find the annotated pages in the public test report?
3. Did source qualification recognize a laboratory test report as authoritative?
4. Did condition reasoning, regulatory logic, review gating, and final aggregation
   produce the manually labelled outcome?

The labels are used only by this evaluator.  They are never included in prompts or
passed to the production assessment pipeline.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Optional


REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
BENCHMARK_DIR = REPO_ROOT / "evaluation" / "fmvss305_benchmark"
SAMPLE_DIR = REPO_ROOT / "sample_documents"
RESULTS_DIR = REPO_ROOT / "evaluation" / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings
from app.schemas.contract import parse_requirement_contract
from app.services.classification import batch_assess_requirements
from app.services.evidence_qualification import qualify_evidence
from app.services.document_classifier import profile_documents
from app.services.extraction import extract_requirements_from_text
from app.services.ingestion import parse_document
from app.services.retrieval import (
    precompute_chunk_embeddings,
    retrieve_candidate_evidence_hybrid,
)


FINAL_CLASSES = ["SUPPORTED", "PARTIAL", "CONFLICT", "MISSING", "UNKNOWN"]
ATOMIC_CLASSES = [
    "PROVEN",
    "FAILED",
    "PENDING",
    "UNTESTED",
    "INCONCLUSIVE",
    "NOT_APPLICABLE",
]
MODES = {
    "oracle-contracts": (True, False),
    "oracle-contracts-evidence": (True, True),
    "end-to-end": (False, False),
    "oracle-evidence": (False, True),
}
CONDITION_INPUT_FIELDS = {
    "condition_id",
    "description",
    "parameter",
    "operator",
    "threshold",
    "min_value",
    "max_value",
    "unit",
    "scope",
    "mandatory",
    "verification_method",
}


def _percent(numerator: int | float, denominator: int | float) -> float:
    return round(100.0 * numerator / denominator, 2) if denominator else 0.0


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _normal_final(value: Any) -> str:
    status = str(value or "UNKNOWN").strip().upper().replace(" ", "_")
    aliases = {
        "PASS": "SUPPORTED",
        "PASSED": "SUPPORTED",
        "VERIFIED": "SUPPORTED",
        "PARTIALLY_SUPPORTED": "PARTIAL",
        "FAILED": "CONFLICT",
        "FAIL": "CONFLICT",
        "NO_EVIDENCE": "MISSING",
        "INCONCLUSIVE": "UNKNOWN",
    }
    status = aliases.get(status, status)
    return status if status in FINAL_CLASSES else "UNKNOWN"


def _normal_atomic(value: Any) -> str:
    status = str(value or "UNTESTED").strip().upper().replace(" ", "_")
    aliases = {
        "PASS": "PROVEN",
        "PASSED": "PROVEN",
        "VERIFIED": "PROVEN",
        "MISSING": "UNTESTED",
        "UNKNOWN": "INCONCLUSIVE",
        "N/A": "NOT_APPLICABLE",
        "NA": "NOT_APPLICABLE",
    }
    status = aliases.get(status, status)
    return status if status in ATOMIC_CLASSES else "UNTESTED"


def _tokens(value: str) -> list[str]:
    normalized = str(value or "").lower().replace("μ", "µ").replace("–", "-")
    return re.findall(r"[a-z0-9µ]+", normalized)


def _token_f1(left: str, right: str) -> float:
    a, b = set(_tokens(left)), set(_tokens(right))
    if not a or not b:
        return 0.0
    overlap = len(a & b)
    precision = overlap / len(a)
    recall = overlap / len(b)
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _quote_coverage(quote: str, content: str) -> float:
    expected = _tokens(quote)
    actual = set(_tokens(content))
    if not expected:
        return 0.0
    return sum(token in actual for token in expected) / len(expected)


def _normalize_clause(value: str) -> str:
    match = re.search(r"\bS\s*(\d+(?:\.\d+)*(?:\([a-z0-9]+\))?)", str(value), re.I)
    source = f"S{match.group(1)}" if match else str(value)
    return re.sub(r"[^a-z0-9]", "", source.lower())


def _normalize_condition_id(value: str) -> str:
    """Normalize harmless extractor ID variations without changing semantics."""
    normalized = re.sub(r"[^a-z0-9]", "", str(value or "").lower())
    # Extractors alternate between `...-C1` and `...-1` for ordinal IDs.
    return re.sub(r"c(?=\d+$)", "", normalized)


def _clean_condition(condition: dict[str, Any]) -> dict[str, Any]:
    """Remove evaluator-only labels before data reaches the production pipeline."""
    return {
        key: value
        for key, value in condition.items()
        if key in CONDITION_INPUT_FIELDS and value is not None
    }


def _load_dataset() -> dict[str, Any]:
    with (BENCHMARK_DIR / "ground_truth.json").open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _document_paths(dataset: dict[str, Any]) -> tuple[Path, Path]:
    requirements = SAMPLE_DIR / dataset["documents"]["requirements"]["filename"]
    evidence = SAMPLE_DIR / dataset["documents"]["evidence"]["filename"]
    return requirements, evidence


def validate_dataset(dataset: dict[str, Any], inspect_quotes: bool = True) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    requirements = dataset.get("requirements", [])
    requirements_path, evidence_path = _document_paths(dataset)

    if not requirements_path.exists():
        errors.append(f"Missing requirements document: {requirements_path}")
    if not evidence_path.exists():
        errors.append(f"Missing evidence document: {evidence_path}")
    if not requirements:
        errors.append("The benchmark has no labelled requirements.")

    requirement_ids = [item.get("requirement_id") for item in requirements]
    if len(requirement_ids) != len(set(requirement_ids)):
        errors.append("Requirement IDs must be unique.")

    condition_ids: list[str] = []
    for requirement in requirements:
        req_id = requirement.get("requirement_id", "<missing>")
        if _normal_final(requirement.get("expected_status")) != requirement.get("expected_status"):
            errors.append(f"{req_id}: invalid expected_status.")
        logic = requirement.get("logic", {})
        if logic.get("operator") not in {"ALL_OF", "ANY_OF", "IF_THEN"}:
            errors.append(f"{req_id}: invalid or missing regulatory logic operator.")
        conditions = requirement.get("conditions", [])
        if not conditions:
            errors.append(f"{req_id}: at least one atomic condition is required.")
        if logic.get("operator") == "IF_THEN":
            declared = {
                logic.get("if_condition_id"),
                *logic.get("then_condition_ids", []),
            }
            declared.discard(None)
        else:
            declared = set(logic.get("condition_ids", []))
        actual = {condition.get("condition_id") for condition in conditions}
        if declared != actual:
            errors.append(f"{req_id}: logic.condition_ids must equal the atomic condition IDs.")
        for condition in conditions:
            condition_id = condition.get("condition_id")
            condition_ids.append(condition_id)
            if _normal_atomic(condition.get("expected_status")) != condition.get("expected_status"):
                errors.append(f"{req_id}/{condition_id}: invalid expected_status.")

    if len(condition_ids) != len(set(condition_ids)):
        errors.append("Atomic condition IDs must be globally unique.")

    quote_checks: list[dict[str, Any]] = []
    if inspect_quotes and not errors:
        evidence_pages: dict[int, list[str]] = {}
        for chunk in parse_document(str(evidence_path)):
            if chunk.page_number is not None:
                evidence_pages.setdefault(chunk.page_number, []).append(chunk.content)
        requirement_pages = {
            chunk.page_number for chunk in parse_document(str(requirements_path))
            if chunk.page_number is not None
        }
        for requirement in requirements:
            req_id = requirement["requirement_id"]
            req_page = requirement.get("requirement_page")
            if req_page not in requirement_pages:
                errors.append(f"{req_id}: requirement page {req_page} does not exist.")
            for condition in requirement.get("conditions", []):
                for evidence in condition.get("evidence", []):
                    page = evidence.get("page")
                    page_chunks = evidence_pages.get(page)
                    if page_chunks is None:
                        errors.append(f"{req_id}/{condition['condition_id']}: evidence page {page} does not exist.")
                        continue
                    content = "\n".join(page_chunks)
                    coverage = _quote_coverage(evidence.get("quote", ""), content)
                    quote_checks.append({
                        "requirement_id": req_id,
                        "condition_id": condition["condition_id"],
                        "page": page,
                        "token_coverage": round(coverage, 3),
                    })
                    if coverage < 0.60:
                        warnings.append(
                            f"{req_id}/{condition['condition_id']} page {page}: "
                            f"quote token coverage is only {coverage:.0%}."
                        )

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "requirements": len(requirements),
        "atomic_conditions": len(condition_ids),
        "quote_checks": quote_checks,
    }


def _ingest_document(path: Path, doc_type: str) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for index, chunk in enumerate(parse_document(str(path)), 1):
        chunk_id = f"{path.stem}-c{index:04d}-p{chunk.page_number or 'na'}"
        chunks.append({
            "id": chunk_id,
            "chunk_id": chunk_id,
            "document_id": path.stem,
            "document_name": path.name,
            "doc_type": doc_type,
            "page_number": chunk.page_number,
            "content": chunk.content,
            "metadata": chunk.metadata,
        })
    return chunks


def _oracle_contracts(requirements: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    items = []
    for requirement in requirements:
        conditions = [_clean_condition(item) for item in requirement["conditions"]]
        items.append({
            "req_code": requirement["requirement_id"],
            "title": requirement["title"],
            "description": requirement["requirement_text"],
            "category": requirement.get("category", "Safety"),
            "conditions": conditions,
            "clause_coverage": [{
                "clause": requirement["clause"],
                "condition_ids": [item["condition_id"] for item in conditions],
            }],
            "unmapped_obligations": [],
            "contract_complete": True,
        })
    return items, {
        "selected_clause_recall": 100.0,
        "matched_selected_clauses": len(items),
        "selected_clauses": len(items),
        "atomic_condition_recall": 100.0,
        "source": "manually labelled oracle contracts",
        "matches": {item["req_code"]: item["req_code"] for item in items},
    }


def _match_extracted_requirement(
    ground_truth: dict[str, Any],
    extracted: list[Any],
    used: set[int],
) -> tuple[Optional[int], float]:
    target_clause = _normalize_clause(ground_truth["clause"])
    for index, candidate in enumerate(extracted):
        if index not in used and _normalize_clause(candidate.req_code) == target_clause:
            return index, 1.0
    # Regulatory identifiers are authoritative. A semantically similar clause
    # with a different explicit S-number is an extraction miss, not a match.
    return None, 0.0


def _extracted_contracts(
    requirements: list[dict[str, Any]],
    extracted: list[Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    items: list[dict[str, Any]] = []
    used: set[int] = set()
    matches: dict[str, str] = {}
    atomic_expected = 0
    atomic_matched = 0

    for truth in requirements:
        atomic_expected += len(truth.get("conditions", []))
        index, score = _match_extracted_requirement(truth, extracted, used)
        if index is None:
            continue
        used.add(index)
        candidate = extracted[index]
        matches[truth["requirement_id"]] = candidate.req_code
        candidate_conditions = [item.model_dump(exclude_none=True) for item in candidate.conditions]
        for expected in truth.get("conditions", []):
            if any(
                _normalize_condition_id(expected.get("condition_id", ""))
                == _normalize_condition_id(actual.get("condition_id", ""))
                or _token_f1(expected.get("description", ""), actual.get("description", "")) >= 0.50
                for actual in candidate_conditions
            ):
                atomic_matched += 1
        items.append({
            # Preserve the benchmark key while retaining the extracted contract content.
            "req_code": truth["requirement_id"],
            "extracted_req_code": candidate.req_code,
            "extraction_match_score": round(score, 3),
            "title": candidate.title,
            "description": candidate.description or candidate.title,
            "category": candidate.category,
            "conditions": candidate_conditions,
            "clause_coverage": [item.model_dump(exclude_none=True) for item in candidate.clause_coverage],
            "unmapped_obligations": list(candidate.unmapped_obligations),
            "contract_complete": candidate.contract_complete,
        })

    return items, {
        "selected_clause_recall": _percent(len(items), len(requirements)),
        "matched_selected_clauses": len(items),
        "selected_clauses": len(requirements),
        "atomic_condition_recall": _percent(atomic_matched, atomic_expected),
        "matched_atomic_conditions": atomic_matched,
        "atomic_conditions": atomic_expected,
        "source": "LLM-extracted contracts",
        "total_extracted_requirements": len(extracted),
        "matches": matches,
        "selected_extracted_contracts": {
            item["req_code"]: {
                "source_req_code": item.get("extracted_req_code"),
                "title": item.get("title"),
                "description": item.get("description"),
                "conditions": item.get("conditions", []),
                "clause_coverage": item.get("clause_coverage", []),
                "unmapped_obligations": item.get("unmapped_obligations", []),
                "contract_complete": item.get("contract_complete"),
            }
            for item in items
        },
    }


def _condition_query(requirement_id: str, condition: dict[str, Any]) -> str:
    return " ".join(
        str(value)
        for value in (
            requirement_id,
            condition.get("description"),
            condition.get("parameter"),
            condition.get("operator"),
            condition.get("threshold"),
            condition.get("min_value"),
            condition.get("max_value"),
            condition.get("unit"),
        )
        if value not in (None, "")
    )


def _oracle_evidence(requirement: dict[str, Any], chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pages = {
        evidence["page"]
        for condition in requirement.get("conditions", [])
        for evidence in condition.get("evidence", [])
    }
    return [dict(chunk, score=1.0) for chunk in chunks if chunk.get("page_number") in pages]


def _retrieval_metrics(
    requirements: list[dict[str, Any]],
    retrieved: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    hit_counts = {1: 0, 3: 0, 5: 0}
    reciprocal_ranks: list[float] = []
    evaluated = 0
    details: dict[str, Any] = {}
    for requirement in requirements:
        expected_pages = {
            evidence["page"]
            for condition in requirement.get("conditions", [])
            for evidence in condition.get("evidence", [])
        }
        if not expected_pages:
            continue
        evaluated += 1
        ranked_pages = [item.get("page_number") for item in retrieved.get(requirement["requirement_id"], [])]
        hit_rank = next(
            (index for index, page in enumerate(ranked_pages[:5], 1) if page in expected_pages),
            None,
        )
        for k in hit_counts:
            if hit_rank is not None and hit_rank <= k:
                hit_counts[k] += 1
        reciprocal_ranks.append(1.0 / hit_rank if hit_rank else 0.0)
        details[requirement["requirement_id"]] = {
            "expected_pages": sorted(expected_pages),
            "retrieved_pages": ranked_pages,
            "first_hit_rank": hit_rank,
        }
    return {
        "evaluated_requirements": evaluated,
        "recall_at_1": _percent(hit_counts[1], evaluated),
        "recall_at_3": _percent(hit_counts[3], evaluated),
        "recall_at_5": _percent(hit_counts[5], evaluated),
        "mrr": round(sum(reciprocal_ranks) / evaluated, 4) if evaluated else 0.0,
        "details": details,
    }


def _prediction_for_condition(
    expected: dict[str, Any],
    predictions: Iterable[Any],
) -> tuple[Optional[Any], Optional[str]]:
    predictions = list(predictions or [])
    expected_id = expected.get("condition_id")
    direct = next((item for item in predictions if _field(item, "condition_id") == expected_id), None)
    if direct is not None:
        return direct, "exact_id"
    normalized_id = _normalize_condition_id(expected_id)
    canonical = next(
        (
            item for item in predictions
            if _normalize_condition_id(_field(item, "condition_id", "")) == normalized_id
        ),
        None,
    )
    if canonical is not None:
        return canonical, "canonical_id"
    scored = [
        (
            _token_f1(expected.get("description", ""), _field(item, "description", "") or ""),
            item,
        )
        for item in predictions
    ]
    if not scored:
        return None, None
    score, item = max(scored, key=lambda pair: pair[0])
    return (item, "semantic_description") if score >= 0.50 else (None, None)


def _atomic_metrics(
    requirements: list[dict[str, Any]],
    results_by_requirement: dict[str, Iterable[Any]],
) -> dict[str, Any]:
    correct = 0
    total_expected = 0
    aligned = 0
    unaligned = 0
    alignment_methods: Counter[str] = Counter()
    details: list[dict[str, Any]] = []
    for requirement in requirements:
        predictions = list(results_by_requirement.get(requirement["requirement_id"], []) or [])
        expected_conditions = requirement.get("conditions", [])
        used_prediction_ids: set[int] = set()
        for index, expected in enumerate(expected_conditions):
            total_expected += 1
            available = [item for item in predictions if id(item) not in used_prediction_ids]
            prediction, alignment_method = _prediction_for_condition(expected, available)
            if (
                prediction is None
                and len(predictions) == len(expected_conditions)
                and index < len(predictions)
                and id(predictions[index]) not in used_prediction_ids
            ):
                prediction = predictions[index]
                alignment_method = "ordinal_equal_cardinality"
            if prediction is not None:
                used_prediction_ids.add(id(prediction))
                aligned += 1
                alignment_methods[alignment_method or "unknown"] += 1
            else:
                unaligned += 1
            predicted_status = (
                _normal_atomic(_field(prediction, "status"))
                if prediction is not None
                else "NOT_EXTRACTED_OR_UNALIGNED"
            )
            expected_status = expected["expected_status"]
            matches = predicted_status == expected_status
            correct += int(matches)
            if not matches:
                details.append({
                    "requirement_id": requirement["requirement_id"],
                    "condition_id": expected["condition_id"],
                    "expected": expected_status,
                    "predicted": predicted_status,
                    "prediction_missing": prediction is None,
                    "alignment_method": alignment_method,
                })
    return {
        # End-to-end accuracy treats an unextracted/unaligned condition as a
        # failure, but never relabels it as UNTESTED.
        "accuracy": _percent(correct, total_expected),
        "end_to_end_accuracy": _percent(correct, total_expected),
        "aligned_accuracy": _percent(correct, aligned),
        "alignment_coverage": _percent(aligned, total_expected),
        "correct": correct,
        "total": total_expected,
        "aligned": aligned,
        "missing_or_unaligned_predictions": unaligned,
        "alignment_methods": dict(alignment_methods),
        "mismatches": details,
    }


def _macro_f1(matrix: dict[str, dict[str, int]]) -> float:
    scores: list[float] = []
    for label in FINAL_CLASSES:
        support = sum(matrix[label].values())
        if support == 0:
            continue
        tp = matrix[label][label]
        fp = sum(matrix[other][label] for other in FINAL_CLASSES if other != label)
        fn = sum(matrix[label][other] for other in FINAL_CLASSES if other != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        scores.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return round(100.0 * sum(scores) / len(scores), 2) if scores else 0.0


def _authority_metrics(
    requirements: list[dict[str, Any]],
    evidence_chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    by_page = {item.get("page_number"): item for item in evidence_chunks}
    checks: list[dict[str, Any]] = []
    for requirement in requirements:
        conditions = [_clean_condition(item) for item in requirement.get("conditions", [])]
        contract = parse_requirement_contract(
            req_code=requirement["requirement_id"],
            title=requirement["title"],
            description=requirement["requirement_text"],
            category=requirement.get("category", "Safety"),
            structured_conditions=conditions,
            clause_coverage=[{
                "clause": requirement["clause"],
                "condition_ids": [item["condition_id"] for item in conditions],
            }],
            unmapped_obligations=[],
            contract_complete=True,
        )
        seen: set[tuple[int, str]] = set()
        for condition in requirement.get("conditions", []):
            for evidence in condition.get("evidence", []):
                key = (evidence["page"], evidence.get("expected_source_authority", ""))
                if key in seen:
                    continue
                seen.add(key)
                chunk = by_page.get(evidence["page"])
                if not chunk:
                    continue
                qualification = qualify_evidence(
                    contract=contract,
                    evidence_id=chunk["id"],
                    document_name=chunk["document_name"],
                    content=chunk["content"],
                    doc_type=chunk["doc_type"],
                    source_chunk_id=chunk["id"],
                    document_profile=chunk.get("document_profile"),
                )
                expected_authority = evidence.get("expected_source_authority", "EMPIRICAL_TEST")
                checks.append({
                    "requirement_id": requirement["requirement_id"],
                    "page": evidence["page"],
                    "expected_authority": expected_authority,
                    "actual_authority": qualification.source_authority,
                    "authority_correct": qualification.source_authority == expected_authority,
                    "document_role": qualification.document_role,
                    "document_profile_confidence": qualification.document_profile_confidence,
                    "passage_modality": qualification.passage_modality,
                    "relevance_status": qualification.relevance_status,
                    "qualification_status": qualification.qualification_status,
                    "is_authoritative": qualification.is_authoritative,
                    "reason": qualification.reason,
                })
    return {
        "authority_accuracy": _percent(sum(item["authority_correct"] for item in checks), len(checks)),
        "authoritative_qualification_rate": _percent(sum(item["is_authoritative"] for item in checks), len(checks)),
        "checks": len(checks),
        "details": checks,
    }


def _report_markdown(results: dict[str, Any]) -> str:
    final = results["metrics"]["final_verdict"]
    extraction = results["metrics"]["extraction"]
    retrieval = results["metrics"]["retrieval"]
    authority = results["metrics"]["source_authority"]
    raw_atomic = results["metrics"]["raw_llm_atomic"]
    final_atomic = results["metrics"]["final_atomic"]
    safety = results["metrics"]["review_gate"]
    logic = results["metrics"]["regulatory_logic"]
    lines = [
        "# TraceAudit FMVSS 305 public-document benchmark",
        "",
        f"- Mode: `{results['mode']}`",
        f"- Model: `{results['model']}`",
        f"- Runtime: {results['runtime_seconds']} seconds",
        f"- Labelled scope: {final['total']} selected requirements; this is not a legal compliance determination.",
        "",
        "## Diagnostic scorecard",
        "",
        "| Stage | Metric | Result |",
        "|---|---|---:|",
        f"| Extraction | Selected-clause recall | {extraction['selected_clause_recall']:.2f}% |",
        f"| Extraction | Atomic-condition recall | {extraction['atomic_condition_recall']:.2f}% |",
        f"| Retrieval | Evidence page Recall@3 | {retrieval['recall_at_3']:.2f}% |",
        f"| Source qualification | Authority accuracy | {authority['authority_accuracy']:.2f}% |",
        f"| Source qualification | Authoritative qualification rate | {authority['authoritative_qualification_rate']:.2f}% |",
        f"| LLM reasoning | Raw atomic aligned accuracy | {raw_atomic['aligned_accuracy']:.2f}% |",
        f"| LLM reasoning | Raw condition alignment coverage | {raw_atomic['alignment_coverage']:.2f}% |",
        f"| LLM reasoning | Raw atomic end-to-end accuracy | {raw_atomic['end_to_end_accuracy']:.2f}% |",
        f"| Pipeline output | Final atomic aligned accuracy | {final_atomic['aligned_accuracy']:.2f}% |",
        f"| Pipeline output | Final condition alignment coverage | {final_atomic['alignment_coverage']:.2f}% |",
        f"| Pipeline output | Final atomic end-to-end accuracy | {final_atomic['end_to_end_accuracy']:.2f}% |",
        f"| Regulatory logic | ANY_OF / IF_THEN accuracy | {logic['accuracy']:.2f}% |",
        f"| Final verdict | Requirement accuracy | {final['accuracy']:.2f}% |",
        f"| Final verdict | Macro F1 | {final['macro_f1']:.2f}% |",
        f"| Safety | Review-state accuracy | {safety['review_state_accuracy']:.2f}% |",
        f"| Safety | Unsafe false auto-closes | {safety['unsafe_false_auto_closes']} |",
        "",
        "## Requirement outcomes",
        "",
        "| Requirement | Logic | Expected | Predicted | Review | Correct |",
        "|---|---|---|---|---|---|",
    ]
    for row in results["requirements"]:
        lines.append(
            f"| {row['requirement_id']} | {row['logic_operator']} | {row['expected_status']} | "
            f"{row['predicted_status']} | {row['predicted_review_state']} | "
            f"{'yes' if row['correct'] else 'no'} |"
        )
    lines.extend([
        "",
        "## Interpretation",
        "",
        "The oracle-contract modes isolate retrieval and verification from requirement extraction. "
        "The oracle-evidence modes additionally inject only the manually annotated test-report pages, "
        "isolating source qualification, condition reasoning, regulatory logic, and aggregation. "
        "Use `end-to-end` for the product-level result and compare it with the other modes to locate regressions.",
        "",
    ])
    return "\n".join(lines)


async def run_benchmark(
    mode: str,
    model: Optional[str],
    thinking_level: Optional[str],
    batch_size: int,
) -> dict[str, Any]:
    if mode not in MODES:
        raise ValueError(f"Unsupported mode {mode!r}; choose one of {', '.join(MODES)}")
    oracle_contracts, oracle_evidence = MODES[mode]
    dataset = _load_dataset()
    validation = validate_dataset(dataset)
    if not validation["valid"]:
        raise ValueError("Invalid FMVSS benchmark:\n- " + "\n- ".join(validation["errors"]))

    requirements = dataset["requirements"]
    truth_by_id = {item["requirement_id"]: item for item in requirements}
    requirements_path, evidence_path = _document_paths(dataset)
    active_model = model or settings.LLM_MODEL
    active_thinking = thinking_level or settings.GEMINI_THINKING_LEVEL
    started = time.time()

    print("=" * 74)
    print("TRACEAUDIT FMVSS 305 PUBLIC-DOCUMENT BENCHMARK")
    print(f"Mode: {mode} | Model: {active_model}")
    print("=" * 74)

    print("[1/5] Ingesting the regulation and laboratory report...")
    requirement_chunks = _ingest_document(requirements_path, "Regulatory specification")
    evidence_chunks = _ingest_document(evidence_path, "Test report")
    print(f"  Indexed {len(requirement_chunks)} regulation pages and {len(evidence_chunks)} report pages.")

    profiles = await profile_documents(
        documents=[
            SimpleNamespace(id=requirements_path.stem, original_filename=requirements_path.name),
            SimpleNamespace(id=evidence_path.stem, original_filename=evidence_path.name),
        ],
        all_chunks=[*requirement_chunks, *evidence_chunks],
        model=active_model,
        thinking_level=active_thinking,
    )
    requirements_profile = profiles[requirements_path.stem]
    evidence_profile = profiles[evidence_path.stem]
    requirements_profile_data = requirements_profile.model_dump()
    profile_data = evidence_profile.model_dump()
    for chunk in requirement_chunks:
        chunk["document_profile"] = requirements_profile_data
    for chunk in evidence_chunks:
        chunk["document_profile"] = profile_data
    print(
        f"  Profiled documents as {requirements_profile.primary_role} and {evidence_profile.primary_role}; "
        f"evidence confidence={evidence_profile.confidence:.1f}%, "
        f"source={evidence_profile.classification_source}."
    )

    print("[2/5] Preparing requirement contracts...")
    if oracle_contracts:
        contract_items, extraction_metrics = _oracle_contracts(requirements)
    else:
        extracted = await extract_requirements_from_text(
            text="\n\n".join(item["content"] for item in requirement_chunks),
            doc_name=requirements_path.name,
            model=active_model,
            thinking_level=active_thinking,
        )
        contract_items, extraction_metrics = _extracted_contracts(requirements, extracted)
    print(
        f"  Selected-clause recall {extraction_metrics['selected_clause_recall']:.2f}% "
        f"({extraction_metrics['matched_selected_clauses']}/{extraction_metrics['selected_clauses']})."
    )

    print("[3/5] Retrieving evidence pages...")
    embeddings = await precompute_chunk_embeddings(evidence_chunks)
    retrieved_by_id: dict[str, list[dict[str, Any]]] = {}
    for item in contract_items:
        requirement_id = item["req_code"]
        query = f"{item['title']} {item['description']}"
        retrieved = await retrieve_candidate_evidence_hybrid(
            requirement_text=query,
            chunks=evidence_chunks,
            chunk_embeddings=embeddings,
            top_k=5,
            min_score=0.3,
            condition_queries=[
                _condition_query(requirement_id, condition)
                for condition in item.get("conditions", [])
            ],
        )
        retrieved_by_id[requirement_id] = [{
            "id": value.chunk_id,
            "chunk_id": value.chunk_id,
            "document_id": value.document_id,
            "document_name": value.document_name,
            "doc_type": value.doc_type,
            "page_number": value.page_number,
            "content": value.content,
            "score": value.score,
            "document_profile": value.document_profile,
        } for value in retrieved]
    retrieval_metrics = _retrieval_metrics(requirements, retrieved_by_id)
    print(f"  Evidence page Recall@3: {retrieval_metrics['recall_at_3']:.2f}%.")

    print("[4/5] Measuring source authority and running condition verification...")
    authority_metrics = _authority_metrics(requirements, evidence_chunks)
    req_items = []
    for item in contract_items:
        candidate_chunks = (
            _oracle_evidence(truth_by_id[item["req_code"]], evidence_chunks)
            if oracle_evidence
            else retrieved_by_id.get(item["req_code"], [])
        )
        req_items.append({**item, "candidate_chunks": candidate_chunks})
    assessments = await batch_assess_requirements(
        req_items=req_items,
        model=active_model,
        thinking_level=active_thinking,
        batch_size=batch_size,
        spec_doc_names={requirements_path.name},
    )

    print("[5/5] Scoring stage-by-stage outputs...")
    matrix = {expected: {predicted: 0 for predicted in FINAL_CLASSES} for expected in FINAL_CLASSES}
    final_condition_results: dict[str, Iterable[Any]] = {}
    raw_condition_results: dict[str, Iterable[Any]] = {}
    rows: list[dict[str, Any]] = []
    review_correct = 0
    false_supported = 0
    unsafe_auto_close = 0
    logic_total = 0
    logic_correct = 0

    for truth in requirements:
        req_id = truth["requirement_id"]
        assessment = assessments.get(req_id)
        expected = truth["expected_status"]
        predicted = _normal_final(_field(assessment, "coverage_status")) if assessment else "UNKNOWN"
        matrix[expected][predicted] += 1
        review_state = _field(assessment, "review_state", "Needs review") if assessment else "Needs review"
        review_matches = review_state.lower() == truth["expected_review_state"].lower()
        review_correct += int(review_matches)
        is_false_supported = predicted == "SUPPORTED" and expected != "SUPPORTED"
        false_supported += int(is_false_supported)
        unsafe_auto_close += int(is_false_supported and review_state == "Reviewed")

        diagnostics = _field(assessment, "pipeline_diagnostics", {}) or {}
        raw_results = diagnostics.get("llm_condition_results", [])
        raw_condition_results[req_id] = raw_results
        final_results = list(_field(assessment, "condition_results", []) or [])
        final_condition_results[req_id] = final_results

        operator = truth["logic"]["operator"]
        if operator in {"ANY_OF", "IF_THEN"}:
            logic_total += 1
            logic_correct += int(predicted == expected)

        rows.append({
            "requirement_id": req_id,
            "clause": truth["clause"],
            "logic_operator": operator,
            "expected_status": expected,
            "predicted_status": predicted,
            "correct": predicted == expected,
            "expected_review_state": truth["expected_review_state"],
            "predicted_review_state": review_state,
            "review_correct": review_matches,
            "confidence": _field(assessment, "confidence", 0.0) if assessment else 0.0,
            "ai_analysis": _field(assessment, "ai_analysis", "Extraction did not produce this selected clause.") if assessment else "Extraction did not produce this selected clause.",
            "retrieved_pages": [item.get("page_number") for item in retrieved_by_id.get(req_id, [])],
            "raw_llm_condition_results": raw_results,
            "final_condition_results": [
                item.model_dump() if hasattr(item, "model_dump") else item for item in final_results
            ],
            "pipeline_diagnostics": diagnostics,
        })

    correct = sum(matrix[label][label] for label in FINAL_CLASSES)
    final_metrics = {
        "accuracy": _percent(correct, len(requirements)),
        "macro_f1": _macro_f1(matrix),
        "correct": correct,
        "total": len(requirements),
        "confusion_matrix": matrix,
        "expected_distribution": dict(Counter(item["expected_status"] for item in requirements)),
        "predicted_distribution": dict(Counter(item["predicted_status"] for item in rows)),
    }
    metrics = {
        "extraction": extraction_metrics,
        "retrieval": retrieval_metrics,
        "source_authority": authority_metrics,
        "raw_llm_atomic": _atomic_metrics(requirements, raw_condition_results),
        "final_atomic": _atomic_metrics(requirements, final_condition_results),
        "regulatory_logic": {
            "accuracy": _percent(logic_correct, logic_total),
            "correct": logic_correct,
            "total": logic_total,
        },
        "final_verdict": final_metrics,
        "review_gate": {
            "review_state_accuracy": _percent(review_correct, len(requirements)),
            "false_supported": false_supported,
            "unsafe_false_auto_closes": unsafe_auto_close,
        },
    }
    results = {
        "benchmark_id": dataset["benchmark_id"],
        "benchmark_version": dataset["version"],
        "mode": mode,
        "model": active_model,
        "thinking_level": active_thinking,
        "runtime_seconds": round(time.time() - started, 2),
        "validation": validation,
        "documents": dataset["documents"],
        "document_profiles": {
            requirements_path.name: requirements_profile_data,
            evidence_path.name: profile_data,
        },
        "metrics": metrics,
        "requirements": rows,
    }

    json_path = RESULTS_DIR / f"fmvss305_{mode}_results.json"
    report_path = RESULTS_DIR / f"fmvss305_{mode}_report.md"
    json_path.write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    report_path.write_text(_report_markdown(results), encoding="utf-8")

    print("\nBenchmark complete")
    print(f"  Final accuracy: {final_metrics['accuracy']:.2f}%")
    print(f"  Raw LLM atomic accuracy: {metrics['raw_llm_atomic']['accuracy']:.2f}%")
    print(f"  Final atomic accuracy: {metrics['final_atomic']['accuracy']:.2f}%")
    print(f"  Authority accuracy: {authority_metrics['authority_accuracy']:.2f}%")
    print(f"  Unsafe false auto-closes: {unsafe_auto_close}")
    print(f"  JSON: {json_path}")
    print(f"  Report: {report_path}")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=sorted(MODES), default="oracle-contracts")
    parser.add_argument("--model", default=None, help="LLM model override; defaults to backend settings.")
    parser.add_argument("--thinking-level", default=None, help="Reasoning/thinking override.")
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate labels, source files, page references, and quotes without calling an LLM.",
    )
    args = parser.parse_args()

    if args.validate_only:
        validation = validate_dataset(_load_dataset())
        print(json.dumps(validation, indent=2, ensure_ascii=False))
        raise SystemExit(0 if validation["valid"] else 1)

    asyncio.run(run_benchmark(
        mode=args.mode,
        model=args.model,
        thinking_level=args.thinking_level,
        batch_size=max(1, args.batch_size),
    ))


if __name__ == "__main__":
    main()
