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
import copy
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from collections import Counter
from dataclasses import fields, is_dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Optional


REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
BENCHMARK_DIR = REPO_ROOT / "evaluation" / "fmvss305_benchmark"
SAMPLE_DIR = REPO_ROOT / "sample_documents"
RESULTS_DIR = REPO_ROOT / "evaluation" / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(BACKEND_DIR))

from evaluation.atomic_evaluation import POLICY, decomposition, verification_metrics

from app.config import settings
from app.schemas.contract import parse_requirement_contract
from app.services.classification import batch_assess_requirements
from app.services.evidence_qualification import qualify_evidence
from app.services.document_classifier import profile_documents
from app.services.extraction import extract_requirements_from_text
from app.services.ingestion import parse_document
from app.services.visual_analysis import describe_figure_candidates
from app.services.retrieval import (
    build_requirement_search_queries,
    precompute_chunk_embeddings,
    retrieve_candidate_evidence_hybrid,
)
from app.services.databricks_document_ai import enrich_targeted_evidence_items


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
    "condition_role",
    "description",
    "parameter",
    "operator",
    "threshold",
    "min_value",
    "max_value",
    "unit",
    "scope",
    "mandatory",
    "requires_visual_evidence",
    "verification_method",
}


def _percent(numerator: int | float, denominator: int | float) -> float:
    return round(100.0 * numerator / denominator, 2) if denominator else 0.0


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _json_ready(value: Any) -> Any:
    """Preserve dataclass and Pydantic diagnostics in immutable run output."""
    if hasattr(value, "model_dump"):
        return _json_ready(value.model_dump())
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _json_ready(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_ready(item) for item in value]
    return value


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
    status = str(value or "INVALID_STATUS").strip().upper().replace(" ", "_")
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
    return status if status in ATOMIC_CLASSES else "INVALID_STATUS"


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


def _condition_ordinal(value: str) -> Optional[int]:
    """Return the terminal C-number without depending on requirement prefixes."""
    match = re.search(r"(?:^|[-_])c?(\d+)$", str(value or ""), re.IGNORECASE)
    return int(match.group(1)) if match else None


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
            "logic": requirement["logic"],
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
                or (
                    _condition_ordinal(expected.get("condition_id", "")) is not None
                    and _condition_ordinal(expected.get("condition_id", ""))
                    == _condition_ordinal(actual.get("condition_id", ""))
                )
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
            "logic": candidate.logic.model_dump(),
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
    query = " ".join(
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
    if condition.get("requires_visual_evidence"):
        query = f"{query} figure image visual marking label photograph diagram"
    return query


def _oracle_evidence(requirement: dict[str, Any], chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select the labelled passages, not every unrelated block on a labelled page."""
    selected_ids: set[str] = set()
    selected: list[dict[str, Any]] = []
    index_by_id = {chunk["id"]: index for index, chunk in enumerate(chunks)}

    def add(chunk: dict[str, Any]) -> None:
        if chunk["id"] not in selected_ids:
            selected_ids.add(chunk["id"])
            selected.append(dict(chunk, score=1.0))

    for condition in requirement.get("conditions", []):
        for annotation in condition.get("evidence", []):
            page_chunks = [
                chunk for chunk in chunks
                if chunk.get("page_number") == annotation["page"]
            ]
            if not page_chunks:
                continue
            best = max(
                page_chunks,
                key=lambda chunk: _quote_coverage(annotation.get("quote", ""), chunk.get("content", "")),
            )
            add(best)
            # Keep an immediately adjacent heading/caption with a structured
            # table or figure so its identity is not lost at a chunk boundary.
            block_type = str((best.get("metadata") or {}).get("block_type") or "").lower()
            if block_type in {"table", "figure", "formula", "checkbox"}:
                position = index_by_id[best["id"]]
                for neighbor_position in (position - 1, position + 1):
                    if 0 <= neighbor_position < len(chunks):
                        neighbor = chunks[neighbor_position]
                        if neighbor.get("page_number") == best.get("page_number"):
                            add(neighbor)
    return selected


def _retrieval_metrics(
    requirements: list[dict[str, Any]],
    retrieved: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    any_hit_counts = {1: 0, 3: 0, 5: 0}
    full_coverage_counts = {1: 0, 3: 0, 5: 0}
    page_hit_counts = {1: 0, 3: 0, 5: 0}
    expected_page_total = 0
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
        expected_page_total += len(expected_pages)
        ranked_pages = [item.get("page_number") for item in retrieved.get(requirement["requirement_id"], [])]
        hit_rank = next(
            (index for index, page in enumerate(ranked_pages[:5], 1) if page in expected_pages),
            None,
        )
        for k in any_hit_counts:
            retrieved_at_k = set(ranked_pages[:k])
            hits = expected_pages & retrieved_at_k
            page_hit_counts[k] += len(hits)
            if hits:
                any_hit_counts[k] += 1
            if expected_pages.issubset(retrieved_at_k):
                full_coverage_counts[k] += 1
        reciprocal_ranks.append(1.0 / hit_rank if hit_rank else 0.0)
        details[requirement["requirement_id"]] = {
            "expected_pages": sorted(expected_pages),
            "retrieved_pages": ranked_pages,
            "first_hit_rank": hit_rank,
            "page_coverage_at_3": _percent(len(expected_pages & set(ranked_pages[:3])), len(expected_pages)),
        }
    return {
        "evaluated_requirements": evaluated,
        "expected_pages": expected_page_total,
        "recall_at_1": _percent(page_hit_counts[1], expected_page_total),
        "recall_at_3": _percent(page_hit_counts[3], expected_page_total),
        "recall_at_5": _percent(page_hit_counts[5], expected_page_total),
        "requirement_any_hit_at_1": _percent(any_hit_counts[1], evaluated),
        "requirement_any_hit_at_3": _percent(any_hit_counts[3], evaluated),
        "requirement_any_hit_at_5": _percent(any_hit_counts[5], evaluated),
        "requirement_full_coverage_at_1": _percent(full_coverage_counts[1], evaluated),
        "requirement_full_coverage_at_3": _percent(full_coverage_counts[3], evaluated),
        "requirement_full_coverage_at_5": _percent(full_coverage_counts[5], evaluated),
        "mrr": round(sum(reciprocal_ranks) / evaluated, 4) if evaluated else 0.0,
        "details": details,
    }


def _prediction_for_condition(
    expected: dict[str, Any],
    predictions: Iterable[Any],
    *, strict: bool = False,
) -> tuple[Optional[Any], Optional[str]]:
    predictions = list(predictions or [])
    if strict:
        # Lexical overlap alone must not erase a changed bound or negation.
        def signature(text: str) -> tuple:
            text = str(text or "").lower()
            return (tuple(sorted(re.findall(r"\d+(?:\.\d+)?", text))),
                    tuple(sorted(set(re.findall(r"\b(?:not|no|never|without)\b|[<>]=?", text)))))
        expected_signature = signature(expected.get("description", ""))
        predictions = [item for item in predictions
                       if signature(_field(item, "description", "")) == expected_signature]
    expected_id = expected.get("condition_id")
    direct = next((item for item in predictions if _field(item, "condition_id") == expected_id), None)
    if direct is not None and not strict:
        return direct, "exact_id"
    normalized_id = _normalize_condition_id(expected_id)
    canonical = next(
        (
            item for item in predictions
            if _normalize_condition_id(_field(item, "condition_id", "")) == normalized_id
        ),
        None,
    )
    if canonical is not None and not strict:
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
    if score >= 0.50:
        return item, "semantic_description"
    expected_ordinal = _condition_ordinal(expected_id)
    if expected_ordinal is not None and not strict:
        ordinal = next(
            (
                item for item in predictions
                if _condition_ordinal(_field(item, "condition_id", "")) == expected_ordinal
            ),
            None,
        )
        if ordinal is not None:
            return ordinal, "condition_ordinal"
    return None, None


def _atomic_metrics(
    requirements: list[dict[str, Any]],
    results_by_requirement: dict[str, Iterable[Any]],
    *, strict: bool = False,
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
            prediction, alignment_method = _prediction_for_condition(expected, available, strict=strict)
            if (
                prediction is None
                and not strict
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
            logic=requirement["logic"],
        )
        for condition in requirement.get("conditions", []):
            for evidence in condition.get("evidence", []):
                page_chunks = [
                    item for item in evidence_chunks
                    if item.get("page_number") == evidence["page"]
                ]
                if not page_chunks:
                    continue
                chunk = max(
                    page_chunks,
                    key=lambda item: _quote_coverage(evidence.get("quote", ""), item.get("content", "")),
                )
                quote_coverage = _quote_coverage(evidence.get("quote", ""), chunk.get("content", ""))
                qualification = qualify_evidence(
                    contract=contract,
                    evidence_id=chunk["id"],
                    document_name=chunk["document_name"],
                    content=chunk["content"],
                    doc_type=chunk["doc_type"],
                    source_chunk_id=chunk["id"],
                    document_profile=chunk.get("document_profile"),
                    metadata=chunk.get("metadata"),
                )
                expected_authority = evidence.get("expected_source_authority", "EMPIRICAL_TEST")
                checks.append({
                    "requirement_id": requirement["requirement_id"],
                    "condition_id": condition["condition_id"],
                    "page": evidence["page"],
                    "selected_chunk_id": chunk["id"],
                    "quote_coverage": round(quote_coverage, 4),
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
    visual = results["metrics"].get("visual_evidence", {})
    targeted = results["metrics"].get("targeted_extraction", {})
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
        f"| Retrieval | Requirement any-hit@3 | {retrieval['requirement_any_hit_at_3']:.2f}% |",
        f"| Retrieval | Requirement full-page-coverage@3 | {retrieval['requirement_full_coverage_at_3']:.2f}% |",
        f"| Source qualification | Authority accuracy | {authority['authority_accuracy']:.2f}% |",
        f"| Source qualification | Authoritative qualification rate | {authority['authoritative_qualification_rate']:.2f}% |",
        f"| Visual evidence | Retrieved figures analyzed | {visual.get('vision_analyzed', 0)} |",
        f"| Visual evidence | Retrieved figures unavailable | {visual.get('vision_unavailable', 0)} |",
        f"| Targeted extraction | Successful enrichments | {targeted.get('succeeded', 0)}/{targeted.get('attempted', 0)} |",
        f"| LLM reasoning | Raw atomic aligned accuracy | {raw_atomic['aligned_accuracy']}% |",
        f"| LLM reasoning | Raw condition alignment coverage | {raw_atomic['alignment_coverage']:.2f}% |",
        f"| LLM reasoning | Raw atomic end-to-end accuracy | {raw_atomic['end_to_end_accuracy']:.2f}% |",
        f"| Pipeline output | Final atomic aligned accuracy | {final_atomic['aligned_accuracy']}% |",
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
        "The oracle-evidence modes additionally inject the manually annotated test-report passages plus only their immediate structural context, "
        "isolating source qualification, condition reasoning, regulatory logic, and aggregation. "
        "End-to-end verifies all extracted clauses; scores cover only the annotated subset. "
        "Atomic matches require structured agreement under scoring policy v3; unresolved representations require review. "
        "Combined atomic accuracy includes extraction failures, while aligned status accuracy covers only eligible atoms. "
        "The ANY_OF/IF_THEN verdict metric is not proof of logic-tree equivalence.",
        "",
    ])
    return "\n".join(lines)


async def run_benchmark(
    mode: str,
    model: Optional[str],
    thinking_level: Optional[str],
    batch_size: int,
    provider: Optional[str] = None,
    allow_rule_fallback: bool = True,
    targeted_extraction: bool = False,
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
    if provider:
        settings.LLM_PROVIDER = provider
    settings.LLM_MODEL = active_model
    settings.ATOMIC_DECOMPOSITION_MODEL = active_model
    settings.ATOMIC_DECOMPOSITION_FALLBACK_MODEL = ""
    settings.DATABRICKS_TARGETED_EXTRACTION_ENABLED = targeted_extraction
    if settings.LLM_PROVIDER == "databricks":
        settings.DATABRICKS_VISION_MODEL = active_model
        settings.DATABRICKS_FALLBACK_MODELS = []
    # No separate adjudication model is included in this single-model benchmark.
    settings.SECONDARY_ADJUDICATOR_ENABLED = False
    active_thinking = thinking_level or settings.GEMINI_THINKING_LEVEL
    started = time.time()
    run_id = "fmvss305-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    archive = RESULTS_DIR / "runs" / run_id
    archive.mkdir(parents=True, exist_ok=False)

    print("=" * 74)
    print("TRACEAUDIT FMVSS 305 PUBLIC-DOCUMENT BENCHMARK")
    print(f"Mode: {mode} | Model: {active_model}")
    print("=" * 74)

    print("[1/5] Ingesting the regulation and laboratory report...")
    requirement_chunks = _ingest_document(requirements_path, "Regulatory specification")
    evidence_chunks = _ingest_document(evidence_path, "Test report")
    requirement_pages = len({item.get('page_number') for item in requirement_chunks if item.get('page_number')})
    evidence_pages = len({item.get('page_number') for item in evidence_chunks if item.get('page_number')})
    print(
        f"  Indexed {len(requirement_chunks)} regulation chunks across {requirement_pages} pages and "
        f"{len(evidence_chunks)} report chunks across {evidence_pages} pages."
    )

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
            atomic_model=active_model,
            atomic_thinking_level=active_thinking,
            atomic_fallback_model="",
            allow_rule_fallback=allow_rule_fallback,
            allow_model_fallback=False,
        )
        # All model outputs reach verification, independent of annotated clauses.
        contract_items = [{
            "req_code": c.req_code, "title": c.title,
            "description": c.description or c.title, "category": c.category,
            "conditions": [v.model_dump(exclude_none=True) for v in c.conditions],
            "clause_coverage": [v.model_dump(exclude_none=True) for v in c.clause_coverage],
            "unmapped_obligations": list(c.unmapped_obligations),
            "contract_complete": c.contract_complete, "logic": c.logic.model_dump(),
        } for c in extracted]
        _, extraction_metrics = _extracted_contracts(requirements, extracted)
    evaluated_contracts = copy.deepcopy(contract_items)
    if len({c["req_code"] for c in contract_items}) != len(contract_items):
        raise ValueError("Duplicate extracted requirement IDs would overwrite verification results")
    (archive / "contracts.json").write_text(json.dumps(evaluated_contracts, indent=2), encoding="utf-8")
    print(
        f"  Selected-clause recall {extraction_metrics['selected_clause_recall']:.2f}% "
        f"({extraction_metrics['matched_selected_clauses']}/{extraction_metrics['selected_clauses']})."
    )

    print("[3/5] Retrieving evidence pages...")
    embeddings = await precompute_chunk_embeddings(evidence_chunks)
    retrieved_by_id: dict[str, list[dict[str, Any]]] = {}
    for item in contract_items:
        requirement_id = item["req_code"]
        search_queries = build_requirement_search_queries(
            requirement_id,
            item.get("title", ""),
            item.get("description", ""),
            item.get("conditions", []),
            item.get("semantic_clauses", []),
        )
        query = search_queries[0] if search_queries else f"{item['title']} {item['description']}"
        retrieved = await retrieve_candidate_evidence_hybrid(
            requirement_text=query,
            chunks=evidence_chunks,
            chunk_embeddings=embeddings,
            top_k=8,
            min_score=0.3,
            condition_queries=search_queries[1:],
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
            "metadata": value.metadata,
        } for value in retrieved]
    retrieval_source_ids = ({r["requirement_id"]: r["requirement_id"] for r in requirements}
                            if oracle_contracts else extraction_metrics["matches"])
    retrieval_metrics = _retrieval_metrics(requirements, {rid: retrieved_by_id.get(sid, []) for rid, sid in retrieval_source_ids.items()})
    print(f"  Evidence page Recall@3: {retrieval_metrics['recall_at_3']:.2f}%.")

    print("[4/5] Measuring source authority and running condition verification...")
    authority_metrics = _authority_metrics(requirements, evidence_chunks)
    req_items = []
    for item in contract_items:
        oracle_truth = next((r for r in requirements if _normalize_clause(r["clause"]) == _normalize_clause(item["req_code"]) or r["requirement_id"] == item["req_code"]), None)
        candidate_chunks = (
            _oracle_evidence(oracle_truth, evidence_chunks)
            if oracle_evidence and oracle_truth
            else retrieved_by_id.get(item["req_code"], [])
        )
        req_items.append({**item, "candidate_chunks": candidate_chunks})
    visual_metrics = await describe_figure_candidates(
        req_items,
        {evidence_path.stem: str(evidence_path)},
        model=active_model,
    )
    print(
        "  Visual evidence: "
        f"{visual_metrics['vision_analyzed']} analyzed, "
        f"{visual_metrics['vision_cache_hits']} cached, "
        f"{visual_metrics['vision_unavailable']} unavailable."
    )
    targeted_metrics = await enrich_targeted_evidence_items(req_items)
    print(
        "  Targeted extraction: "
        f"{targeted_metrics['succeeded']}/{targeted_metrics['attempted']} succeeded, "
        f"{targeted_metrics['failed']} failed."
    )
    assessments = await batch_assess_requirements(
        req_items=req_items,
        model=active_model,
        thinking_level=active_thinking,
        batch_size=batch_size,
        spec_doc_names={requirements_path.name},
    )

    print("[5/5] Scoring stage-by-stage outputs...")
    # Reference IDs map to extracted clause IDs only inside the evaluator.
    source_ids = ({r["requirement_id"]: r["requirement_id"] for r in requirements}
                  if oracle_contracts else extraction_metrics["matches"])
    scored_contracts = [{**c, "req_code": rid} for rid, sid in source_ids.items()
                        for c in evaluated_contracts if c["req_code"] == sid]
    atomic_alignment = decomposition(requirements, scored_contracts)
    extraction_metrics["atomic_condition_recall"] = atomic_alignment["recall"]
    extraction_metrics["matched_atomic_conditions"] = atomic_alignment["correct"]
    retrieval_metrics = _retrieval_metrics(requirements, {rid: retrieved_by_id.get(sid, []) for rid, sid in source_ids.items()})
    matrix = {expected: {predicted: 0 for predicted in [*FINAL_CLASSES, "NOT_EXTRACTED"]} for expected in FINAL_CLASSES}
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
        source_id = source_ids.get(req_id)
        assessment = assessments.get(source_id) if source_id else None
        expected = truth["expected_status"]
        predicted = _normal_final(_field(assessment, "coverage_status")) if assessment else "NOT_EXTRACTED"
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
            "extracted_req_code": source_id,
            "retrieved_pages": [item.get("page_number") for item in retrieved_by_id.get(source_id, [])],
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
        "structured_decomposition": atomic_alignment,
        "integrity": {
            "scoring_version": 3, "scoring_policy": POLICY,
            "gold_contracts_supplied": oracle_contracts, "gold_evidence_selection": oracle_evidence,
            "source_rule_fallback_enabled": allow_rule_fallback,
            "model_fallback_enabled": False,
            "document_parser": settings.TRACEAUDIT_DOCUMENT_PARSER,
            "retrieval_top_k": 8,
            "targeted_extraction_enabled": targeted_extraction,
            "scorer_sha256": hashlib.sha256((REPO_ROOT / "evaluation/atomic_evaluation.py").read_bytes()).hexdigest(),
            "ground_truth_sha256": hashlib.sha256((BENCHMARK_DIR / "ground_truth.json").read_bytes()).hexdigest(),
            "scope": "Selected annotated clauses only. Other extracted clauses are verified but unlabelled, not counted as hallucinations.",
            "unlabelled_extracted_ids": [c["req_code"] for c in contract_items if c["req_code"] not in set(source_ids.values())],
        },
        "extraction": extraction_metrics,
        "retrieval": retrieval_metrics,
        "source_authority": authority_metrics,
        "visual_evidence": visual_metrics,
        "targeted_extraction": targeted_metrics,
        "raw_llm_atomic": verification_metrics(requirements, raw_condition_results, atomic_alignment),
        "final_atomic": verification_metrics(requirements, final_condition_results, atomic_alignment),
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
        "run_id": run_id,
        "provider": settings.LLM_PROVIDER,
        "atomic_model": active_model, "verification_model": active_model,
        "vision_model": settings.DATABRICKS_VISION_MODEL if settings.LLM_PROVIDER == "databricks" else None,
        "evaluated_contracts": evaluated_contracts,
        "scoring_contracts": scored_contracts,
        "extraction_predictions": [] if oracle_contracts else [c.model_dump() for c in extracted],
        "all_assessments": {key: _json_ready(value) for key, value in assessments.items()},
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
    (archive / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    (archive / "report.md").write_text(_report_markdown(results), encoding="utf-8")
    json_path.write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    report_path.write_text(_report_markdown(results), encoding="utf-8")

    print("\nBenchmark complete")
    print(f"  Final accuracy: {final_metrics['accuracy']:.2f}%")
    print(f"  Raw LLM atomic accuracy: {metrics['raw_llm_atomic']['accuracy']:.2f}%")
    print(f"  Final atomic accuracy: {metrics['final_atomic']['accuracy']:.2f}%")
    print(f"  Eligible atom status accuracy: {metrics['final_atomic']['aligned_accuracy']}% (coverage {metrics['final_atomic']['alignment_coverage']:.2f}%)")
    print(f"  Immutable run: {archive}")
    print(f"  Authority accuracy: {authority_metrics['authority_accuracy']:.2f}%")
    print(f"  Unsafe false auto-closes: {unsafe_auto_close}")
    print(f"  JSON: {json_path}")
    print(f"  Report: {report_path}")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=sorted(MODES), default="oracle-contracts")
    parser.add_argument("--model", default=None, help="LLM model override; defaults to backend settings.")
    parser.add_argument("--provider", default=None)
    parser.add_argument("--no-rule-fallback", action="store_true", help="Disable normal source-only extraction fallback (strict model-only diagnostic).")
    parser.add_argument("--thinking-level", default=None, help="Reasoning/thinking override.")
    parser.add_argument("--batch-size", type=int, default=3)
    parser.add_argument(
        "--targeted-extraction",
        action="store_true",
        help="Run Databricks ai_extract on retrieved evidence before verification.",
    )
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
        provider=args.provider,
        allow_rule_fallback=not args.no_rule_fallback,
        targeted_extraction=args.targeted_extraction,
    ))


if __name__ == "__main__":
    main()
