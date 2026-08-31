"""Complex Benchmark Evaluation Runner for TraceAudit AI.

Executes the full pipeline against the 100-requirement automotive benchmark,
computes 18+ rigorous quantitative metrics across extraction, retrieval, and 5-class verification,
generates a 5x5 confusion matrix, groups failures by technical root cause, and outputs:
- evaluation/results/complex_benchmark_results.json
- evaluation/results/complex_benchmark_report.md
"""

import os
import sys
import json
import time
import asyncio
import re
import argparse
from pathlib import Path
from typing import Any, Optional
from collections import Counter

# Set up paths
REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
BENCHMARK_DIR = REPO_ROOT / "evaluation" / "complex_benchmark"
RESULTS_DIR = REPO_ROOT / "evaluation" / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(BACKEND_DIR))

from app.services.ingestion import parse_document
from app.services.extraction import extract_requirements_from_text
from app.services.retrieval import (
    retrieve_candidate_evidence_hybrid,
    precompute_chunk_embeddings,
    retrieve_candidate_evidence,
)
from app.services.classification import batch_assess_requirements
from app.schemas.contract import parse_requirement_contract
from app.schemas.evidence_qualification import normalize_parameter
from app.schemas.verification_result import ConditionVerificationResult
from app.services.units import are_units_compatible
from app.services.verdict_aggregator import aggregate_condition_statuses
from app.config import settings

# Benchmark 5 Status Classes
BENCHMARK_CLASSES = ["SUPPORTED", "PARTIAL", "CONFLICT", "MISSING", "UNKNOWN"]
CONDITION_CLASSES = ["PROVEN", "FAILED", "PENDING", "UNTESTED", "INCONCLUSIVE", "NOT_APPLICABLE"]
SRS_DOC_NAME = "01_System_Requirements_Specification_SRS.docx"

MODE_ALIASES = {
    "oracle": "oracle-contracts",
    "oracle-contracts": "oracle-contracts",
    "oracle-evidence": "oracle-evidence",
    "oracle-contracts-evidence": "oracle-contracts-evidence",
    "end-to-end": "end-to-end",
}


def _normalized_tokens(value: str) -> list[str]:
    normalized = value.lower().replace("μ", "µ").replace("–", "-")
    return re.findall(r"[a-z0-9µ%]+", normalized)


def _token_f1(left: str, right: str) -> float:
    a, b = set(_normalized_tokens(left)), set(_normalized_tokens(right))
    if not a or not b:
        return 0.0
    overlap = len(a & b)
    precision = overlap / len(a)
    recall = overlap / len(b)
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _passage_matches(expected_quote: str, candidate_content: str) -> bool:
    """Fuzzy passage match robust to PDF glyph normalization and line wrapping."""
    quote_tokens = _normalized_tokens(expected_quote)
    content_tokens = set(_normalized_tokens(candidate_content))
    if not quote_tokens:
        return False
    covered = sum(1 for token in quote_tokens if token in content_tokens)
    return covered / len(quote_tokens) >= 0.82


def normalize_status_5(status: Optional[str]) -> str:
    """Normalize status into one of 5 benchmark classes."""
    if not status:
        return "UNKNOWN"
    s = status.strip().upper()
    if s in ("SUPPORTED", "PASS", "PASSED", "VERIFIED"):
        return "SUPPORTED"
    if s in ("PARTIAL", "PARTIALLY_SUPPORTED", "PARTIAL EVIDENCE", "IN PROGRESS"):
        return "PARTIAL"
    if s in ("CONFLICT", "CONTRADICTION", "FAIL", "FAILED", "POTENTIAL CONFLICT"):
        return "CONFLICT"
    if s in ("MISSING", "MISSING EVIDENCE", "NOT STARTED", "NO EVIDENCE"):
        return "MISSING"
    return "UNKNOWN"


def _build_pipeline_requirements(
    mode: str,
    extracted_reqs: list[Any],
    ground_truth_reqs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Choose pipeline inputs without leaking ground truth into end-to-end mode."""
    normalized_mode = mode.strip().lower()
    if normalized_mode in {"oracle", "oracle-contracts", "oracle-contracts-evidence"}:
        return [
            {
                "req_code": item["requirement_id"],
                "title": item["title"],
                "description": item["requirement_text"],
                "category": item.get("category", "General"),
                "severity": item.get("severity", "Medium"),
                "conditions": list(item.get("conditions", [])),
                "clause_coverage": [
                    {
                        "clause": condition.get("description") or condition.get("condition_id", ""),
                        "condition_ids": [condition.get("condition_id", "")],
                    }
                    for condition in item.get("conditions", [])
                ],
                "unmapped_obligations": [],
                "contract_complete": True,
            }
            for item in ground_truth_reqs
        ]
    if normalized_mode not in {"end-to-end", "oracle-evidence"}:
        raise ValueError(f"unsupported benchmark mode: {mode}")

    return [
        {
            "req_code": item.req_code,
            "title": item.title,
            "description": item.description or item.title,
            "category": item.category,
            "severity": item.severity,
            "conditions": [condition.model_dump(exclude_none=True) for condition in item.conditions],
            "clause_coverage": [
                mapping.model_dump(exclude_none=True) for mapping in item.clause_coverage
            ],
            "unmapped_obligations": list(item.unmapped_obligations),
            "contract_complete": item.contract_complete,
        }
        for item in extracted_reqs
    ]


def _predicted_condition_status(
    expected_condition: dict[str, Any],
    predicted_results: list[Any],
) -> str:
    """Align evaluator-only condition labels without altering pipeline inputs."""
    expected_id = expected_condition.get("condition_id", "")
    def get_field(result: Any, field: str) -> Any:
        return result.get(field) if isinstance(result, dict) else getattr(result, field, None)

    direct = next(
        (result for result in predicted_results if get_field(result, "condition_id") == expected_id),
        None,
    )
    if direct is not None:
        return get_field(direct, "status") or "UNTESTED"

    expected_text = " ".join(
        str(value)
        for value in (
            expected_condition.get("parameter"),
            expected_condition.get("description"),
            expected_condition.get("operator"),
            expected_condition.get("threshold"),
            expected_condition.get("min_value"),
            expected_condition.get("max_value"),
            expected_condition.get("unit"),
        )
        if value is not None
    )
    scored = [
        (
            _token_f1(
                expected_text,
                " ".join(
                    str(value)
                    for value in (get_field(result, "description"), get_field(result, "reason"))
                    if value
                ),
            ),
            result,
        )
        for result in predicted_results
    ]
    if scored:
        score, best = max(scored, key=lambda item: item[0])
        if score >= 0.45:
            return get_field(best, "status") or "UNTESTED"
    return "UNTESTED"


def _predicted_condition_result(
    expected_condition: dict[str, Any],
    predicted_results: list[Any],
) -> Optional[Any]:
    """Return the aligned result object using the same policy as status scoring."""
    expected_id = expected_condition.get("condition_id", "")

    def get_field(result: Any, field: str) -> Any:
        return result.get(field) if isinstance(result, dict) else getattr(result, field, None)

    direct = next(
        (result for result in predicted_results if get_field(result, "condition_id") == expected_id),
        None,
    )
    if direct is not None:
        return direct
    expected_text = " ".join(
        str(item)
        for item in (
            expected_condition.get("parameter"),
            expected_condition.get("description"),
            expected_condition.get("operator"),
            expected_condition.get("threshold"),
            expected_condition.get("min_value"),
            expected_condition.get("max_value"),
            expected_condition.get("unit"),
        )
        if item is not None
    )
    scored = [
        (
            _token_f1(
                expected_text,
                " ".join(
                    str(item)
                    for item in (get_field(result, "description"), get_field(result, "reason"))
                    if item
                ),
            ),
            result,
        )
        for result in predicted_results
    ]
    if not scored:
        return None
    score, best = max(scored, key=lambda item: item[0])
    return best if score >= 0.45 else None


def _ground_truth_condition_status(
    requirement: dict[str, Any],
    ground_truth_link: dict[str, Any],
    condition: dict[str, Any],
) -> tuple[str, str]:
    """Resolve atomic truth and report whether it is explicit or inferred.

    Historical benchmark files only label the final requirement state and a
    list of missing condition references.  Those labels remain available for
    backward-compatible scoring, but are marked as inferred so they are not
    mistaken for independently annotated atomic ground truth.
    """
    explicit = condition.get("expected_status") or condition.get("expected_condition_status")
    condition_statuses = ground_truth_link.get("condition_statuses", {})
    condition_id = condition.get("condition_id", "")
    if not explicit and isinstance(condition_statuses, dict):
        explicit = condition_statuses.get(condition_id)
    if explicit:
        status = str(explicit).strip().upper()
        return (status if status in CONDITION_CLASSES else "INCONCLUSIVE", "explicit")

    expected_status = normalize_status_5(requirement.get("expected_status"))
    missing_refs = ground_truth_link.get("missing_conditions", [])
    parameter = condition.get("parameter")
    is_missing = any(
        condition_id in str(reference)
        or (parameter and str(parameter).lower() in str(reference).lower())
        for reference in missing_refs
    )
    if expected_status == "SUPPORTED":
        status = "PROVEN"
    elif expected_status == "MISSING":
        status = "UNTESTED"
    elif expected_status == "UNKNOWN":
        status = "INCONCLUSIVE"
    elif expected_status == "CONFLICT":
        status = "FAILED"
    elif expected_status == "PARTIAL":
        status = "PENDING" if is_missing else "PROVEN"
    else:
        status = "UNTESTED"
    return status, "inferred_from_requirement"


def _condition_similarity(expected: dict[str, Any], extracted: Any) -> float:
    """Semantic condition similarity used only by benchmark scoring."""
    extracted_data = extracted.model_dump(exclude_none=True)
    if expected.get("condition_id") and expected.get("condition_id") == extracted_data.get("condition_id"):
        return 1.0

    expected_parameter = str(expected.get("parameter") or "")
    extracted_parameter = str(extracted_data.get("parameter") or "")
    expected_description = str(expected.get("description") or "")
    extracted_description = str(extracted_data.get("description") or "")
    lexical = _token_f1(
        f"{expected_parameter} {expected_description}",
        f"{extracted_parameter} {extracted_description}",
    )
    parameter = _token_f1(expected_parameter, extracted_parameter)
    operator = 1.0 if expected.get("operator") == extracted_data.get("operator") else 0.0
    unit = _token_f1(str(expected.get("unit") or ""), str(extracted_data.get("unit") or ""))

    expected_values = {
        str(expected.get(key))
        for key in ("threshold", "min_value", "max_value")
        if expected.get(key) is not None
    }
    extracted_values = {
        str(extracted_data.get(key))
        for key in ("threshold", "min_value", "max_value")
        if extracted_data.get(key) is not None
    }
    numeric = 1.0 if expected_values and expected_values & extracted_values else 0.0
    return 0.45 * lexical + 0.25 * parameter + 0.1 * operator + 0.1 * unit + 0.1 * numeric


def _count_semantically_matched_conditions(
    ground_truth_reqs: list[dict[str, Any]],
    extracted_by_id: dict[str, Any],
) -> int:
    """One-to-one semantic matching avoids requiring benchmark-specific IDs."""
    matched = 0
    for requirement in ground_truth_reqs:
        extracted_req = extracted_by_id.get(requirement["requirement_id"])
        if extracted_req is None:
            continue
        available = list(enumerate(extracted_req.conditions))
        used_indices: set[int] = set()
        for expected in requirement.get("conditions", []):
            candidates = [
                (_condition_similarity(expected, actual), index)
                for index, actual in available
                if index not in used_indices
            ]
            if not candidates:
                continue
            score, best_index = max(candidates)
            if score >= 0.45:
                used_indices.add(best_index)
                matched += 1
    return matched


def _normalized_contract_value(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 9)
    text = str(value).strip().lower().replace("μ", "µ")
    try:
        return round(float(text), 9)
    except ValueError:
        return re.sub(r"[\s_-]+", "", text)


def _contract_value_present(value: Any) -> bool:
    """False for optional contract fields represented as None or blank text."""
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _range_bounds(contract_data: dict[str, Any]) -> Optional[tuple[float, float]]:
    """Read a BETWEEN range from min/max fields or a legacy threshold string."""
    min_value = contract_data.get("min_value")
    max_value = contract_data.get("max_value")
    if _contract_value_present(min_value) and _contract_value_present(max_value):
        try:
            return float(min_value), float(max_value)
        except (TypeError, ValueError):
            return None

    threshold = contract_data.get("threshold")
    if not isinstance(threshold, str):
        return None
    threshold_text = re.sub(r"(?<=\d)\s*[-–—]\s*(?=\d)", " to ", threshold)
    numbers = re.findall(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", threshold_text)
    if len(numbers) != 2:
        return None
    return float(numbers[0]), float(numbers[1])


def _range_representations_match(
    expected: dict[str, Any],
    actual: dict[str, Any],
) -> bool:
    if str(expected.get("operator") or "").lower() != "between":
        return False
    if str(actual.get("operator") or "").lower() != "between":
        return False
    expected_bounds = _range_bounds(expected)
    actual_bounds = _range_bounds(actual)
    if expected_bounds is None or actual_bounds is None:
        return False
    return all(abs(left - right) <= 1e-9 for left, right in zip(expected_bounds, actual_bounds))


def _condition_contract_exact(expected: dict[str, Any], extracted: Any) -> bool:
    actual = extracted.model_dump(exclude_none=True)
    expected_operator = "==" if expected.get("operator") == "=" else expected.get("operator")
    actual_operator = "==" if actual.get("operator") == "=" else actual.get("operator")
    if expected_operator != actual_operator:
        return False
    if _normalized_contract_value(expected.get("parameter")) != _normalized_contract_value(actual.get("parameter")):
        return False
    if _normalized_contract_value(expected.get("unit") or "") != _normalized_contract_value(actual.get("unit") or ""):
        return False
    if _range_representations_match(expected, actual):
        return True
    for field in ("threshold", "min_value", "max_value"):
        expected_value = expected.get(field)
        if _contract_value_present(expected_value) and _normalized_contract_value(expected_value) != _normalized_contract_value(actual.get(field)):
            return False
    return True


def _count_exactly_matched_conditions(
    ground_truth_reqs: list[dict[str, Any]],
    extracted_by_id: dict[str, Any],
) -> int:
    matched = 0
    for requirement in ground_truth_reqs:
        extracted_req = extracted_by_id.get(requirement["requirement_id"])
        if extracted_req is None:
            continue
        used_indices: set[int] = set()
        for expected in requirement.get("conditions", []):
            for index, actual in enumerate(extracted_req.conditions):
                if index not in used_indices and _condition_contract_exact(expected, actual):
                    used_indices.add(index)
                    matched += 1
                    break
    return matched


def _match_extracted_contracts(
    ground_truth_reqs: list[dict[str, Any]],
    extracted_by_id: dict[str, Any],
) -> list[dict[str, Any]]:
    """One-to-one expected/extracted pairs used by decomposed contract metrics."""
    pairs: list[dict[str, Any]] = []
    for requirement in ground_truth_reqs:
        extracted_req = extracted_by_id.get(requirement["requirement_id"])
        actual_conditions = list(extracted_req.conditions) if extracted_req is not None else []
        used_indices: set[int] = set()
        for expected in requirement.get("conditions", []):
            candidates = [
                (_condition_similarity(expected, actual), index, actual)
                for index, actual in enumerate(actual_conditions)
                if index not in used_indices
            ]
            if not candidates:
                pairs.append({"requirement_id": requirement["requirement_id"], "expected": expected, "actual": None, "similarity": 0.0})
                continue
            score, index, actual = max(candidates, key=lambda item: item[0])
            if score >= 0.45:
                used_indices.add(index)
                pairs.append({
                    "requirement_id": requirement["requirement_id"],
                    "expected": expected,
                    "actual": actual.model_dump(exclude_none=True),
                    "similarity": round(score, 4),
                })
            else:
                pairs.append({"requirement_id": requirement["requirement_id"], "expected": expected, "actual": None, "similarity": round(score, 4)})
    return pairs


def _percent(correct: int, total: int) -> Optional[float]:
    return round(correct / total * 100.0, 2) if total else None


def calculate_review_gate_safety_metrics(
    predictions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Measure operational safety separately from semantic verdict accuracy.

    A false SUPPORTED prediction is still a classification error.  It only
    becomes a business-safety failure when the review gate also allows it to
    close automatically.  Keeping both measurements prevents the workflow
    safety layer from hiding model errors.
    """
    supported_predictions = 0
    supported_review_required = 0
    supported_auto_close_eligible = 0
    correct_supported_auto_closures = 0
    false_supported_ids: list[str] = []
    caught_false_supported_ids: list[str] = []
    false_auto_close_ids: list[str] = []

    for req_id, prediction in predictions.items():
        expected = normalize_status_5(prediction.get("expected"))
        predicted = normalize_status_5(prediction.get("predicted"))
        if predicted != "SUPPORTED":
            continue

        supported_predictions += 1
        review_required = bool(prediction.get("review_required", False))
        auto_close_eligible = bool(prediction.get("auto_close_eligible", False))
        is_false_supported = expected != "SUPPORTED"

        if review_required:
            supported_review_required += 1
        if auto_close_eligible:
            supported_auto_close_eligible += 1
            if is_false_supported:
                false_auto_close_ids.append(req_id)
            else:
                correct_supported_auto_closures += 1

        if is_false_supported:
            false_supported_ids.append(req_id)
            if review_required and not auto_close_eligible:
                caught_false_supported_ids.append(req_id)

    false_supported_count = len(false_supported_ids)
    return {
        "policy": "supported_evidence_audit_v1",
        "supported_predictions": supported_predictions,
        "supported_review_required": supported_review_required,
        "supported_auto_close_eligible": supported_auto_close_eligible,
        "correct_supported_auto_closures": correct_supported_auto_closures,
        "false_supported_predictions": false_supported_count,
        "false_supported_routed_to_review": len(caught_false_supported_ids),
        "false_automatic_closures": len(false_auto_close_ids),
        "supported_review_rate": _percent(supported_review_required, supported_predictions),
        "review_gate_capture_rate": _percent(len(caught_false_supported_ids), false_supported_count),
        "automatic_closure_precision": _percent(
            correct_supported_auto_closures,
            supported_auto_close_eligible,
        ),
        "false_supported_requirement_ids": false_supported_ids,
        "review_gate_caught_requirement_ids": caught_false_supported_ids,
        "false_automatic_closure_requirement_ids": false_auto_close_ids,
        "definitions": {
            "false_supported_prediction": "Predicted SUPPORTED while benchmark ground truth is not SUPPORTED.",
            "false_automatic_closure": "False SUPPORTED prediction with auto_close_eligible=true.",
            "review_gate_capture_rate": "False SUPPORTED predictions safely routed to review / all false SUPPORTED predictions.",
            "automatic_closure_precision": "Correct SUPPORTED automatic closures / all SUPPORTED automatic closures.",
        },
    }


def _decomposed_contract_metrics(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    """Separate decomposition, naming, operator, values, and units."""
    field_totals = Counter()
    field_correct = Counter()
    mismatches: list[dict[str, Any]] = []
    matched = 0
    exact = 0
    for pair in pairs:
        expected = pair["expected"]
        actual = pair["actual"]
        if actual is not None:
            matched += 1
            exact += int(_condition_contract_exact(expected, _ContractView(actual)))

        failed_fields: list[str] = []
        range_equivalent = bool(actual and _range_representations_match(expected, actual))
        for field in ("parameter", "operator", "threshold", "min_value", "max_value", "unit"):
            if not _contract_value_present(expected.get(field)):
                continue
            field_totals[field] += 1
            actual_value = actual.get(field) if actual else None
            expected_value = expected.get(field)
            if field in ("threshold", "min_value", "max_value") and range_equivalent:
                matches = True
            elif field == "operator":
                left = "==" if expected_value == "=" else expected_value
                right = "==" if actual_value == "=" else actual_value
                matches = left == right
            else:
                matches = _normalized_contract_value(expected_value) == _normalized_contract_value(actual_value)
            if matches:
                field_correct[field] += 1
            else:
                failed_fields.append(field)

        if _contract_value_present(expected.get("parameter")):
            expected_parameter = normalize_parameter(str(expected.get("parameter") or ""))
            actual_parameter = normalize_parameter(str(actual.get("parameter") or "")) if actual else None
            if expected_parameter:
                field_totals["parameter_canonical"] += 1
                if expected_parameter == actual_parameter:
                    field_correct["parameter_canonical"] += 1
        if _contract_value_present(expected.get("unit")):
            field_totals["unit_compatible"] += 1
            if actual and are_units_compatible(expected.get("unit"), actual.get("unit")):
                field_correct["unit_compatible"] += 1

        if failed_fields:
            mismatches.append({
                "requirement_id": pair["requirement_id"],
                "condition_id": expected.get("condition_id"),
                "failed_fields": failed_fields,
                "expected": expected,
                "extracted": actual,
            })

    total = len(pairs)
    metrics = {
        "total_expected_conditions": total,
        "semantically_matched_conditions": matched,
        "decomposition_recall": _percent(matched, total),
        "full_exact_recall": _percent(exact, total),
        "parameter_exact_accuracy": _percent(field_correct["parameter"], field_totals["parameter"]),
        "parameter_canonical_accuracy": _percent(field_correct["parameter_canonical"], field_totals["parameter_canonical"]),
        "parameter_canonical_coverage": _percent(field_totals["parameter_canonical"], total),
        "operator_accuracy": _percent(field_correct["operator"], field_totals["operator"]),
        "threshold_accuracy": _percent(field_correct["threshold"], field_totals["threshold"]),
        "min_value_accuracy": _percent(field_correct["min_value"], field_totals["min_value"]),
        "max_value_accuracy": _percent(field_correct["max_value"], field_totals["max_value"]),
        "unit_exact_accuracy": _percent(field_correct["unit"], field_totals["unit"]),
        "unit_compatible_accuracy": _percent(field_correct["unit_compatible"], field_totals["unit_compatible"]),
        "field_denominators": dict(field_totals),
        "exact_recall_definition": (
            "Exact normalized parameter/operator/unit/value match; equivalent BETWEEN threshold-string and min/max representations match."
        ),
    }
    return {"metrics": metrics, "mismatches": mismatches}


class _ContractView:
    """Tiny adapter so exact-match logic can score serialized conditions."""

    def __init__(self, data: dict[str, Any]):
        self._data = data

    def model_dump(self, exclude_none: bool = True) -> dict[str, Any]:
        if not exclude_none:
            return dict(self._data)
        return {key: value for key, value in self._data.items() if value is not None}


def _oracle_evidence_chunks(
    ground_truth_link: dict[str, Any],
    all_chunks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build a verifier input from annotated evidence, bypassing retrieval only."""
    oracle_chunks: list[dict[str, Any]] = []
    for index, evidence in enumerate(ground_truth_link.get("expected_evidence", []), 1):
        document = evidence.get("document", "Oracle evidence")
        quote = evidence.get("quote", "")
        match = next(
            (
                chunk for chunk in all_chunks
                if chunk.get("document_name", "").lower() == document.lower()
                and _passage_matches(quote, chunk.get("content", ""))
            ),
            None,
        )
        if match:
            selected = dict(match)
        else:
            suffix = Path(document).suffix.lower()
            selected = {
                "id": f"oracle-{ground_truth_link.get('requirement_id', 'REQ')}-{index}",
                "document_id": Path(document).stem,
                "document_name": document,
                "doc_type": "Compliance matrix" if suffix == ".xlsx" else "Test report",
                "page_number": evidence.get("page"),
                "content": quote,
            }
        selected["score"] = 1.0
        selected["oracle_evidence"] = True
        oracle_chunks.append(selected)
    return oracle_chunks


def _aggregation_oracle_metrics(
    ground_truth_reqs: list[dict[str, Any]],
    links_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Experiment D: give the aggregator expected atomic states directly."""
    correct = 0
    records = []
    for requirement in ground_truth_reqs:
        req_id = requirement["requirement_id"]
        link = links_by_id.get(req_id, {})
        contract = parse_requirement_contract(
            req_code=req_id,
            title=requirement.get("title", ""),
            description=requirement.get("requirement_text", ""),
            category=requirement.get("category", "General"),
            structured_conditions=requirement.get("conditions", []),
        )
        conditions = [
            ConditionVerificationResult(
                condition_id=condition.get("condition_id", ""),
                description=condition.get("description"),
                status=_ground_truth_condition_status(requirement, link, condition)[0],
            )
            for condition in requirement.get("conditions", [])
        ]
        expected = normalize_status_5(requirement.get("expected_status"))
        predicted, _, reason = aggregate_condition_statuses(
            contract,
            conditions,
            has_relevant_evidence=expected != "MISSING",
            evidence_absent=expected == "MISSING",
        )
        matches = normalize_status_5(predicted) == expected
        correct += int(matches)
        if not matches:
            records.append({
                "requirement_id": req_id,
                "expected_status": expected,
                "aggregated_status": normalize_status_5(predicted),
                "reason": reason,
            })
    return {
        "total_requirements": len(ground_truth_reqs),
        "correct_requirements": correct,
        "accuracy": _percent(correct, len(ground_truth_reqs)),
        "mismatches": records,
    }


async def run_benchmark(mode: str = "end-to-end"):
    requested_mode = mode.strip().lower()
    if requested_mode not in MODE_ALIASES:
        raise ValueError(f"mode must be one of: {', '.join(MODE_ALIASES)}")
    mode = MODE_ALIASES[requested_mode]
    contract_oracle = mode in {"oracle-contracts", "oracle-contracts-evidence"}
    evidence_oracle = mode in {"oracle-evidence", "oracle-contracts-evidence"}
    print("=" * 75)
    print("     TRACEAUDIT AI - 100-REQUIREMENT COMPLEX AUTOMOTIVE BENCHMARK")
    print(f"     MODE: {mode.upper()}")
    print("=" * 75)

    start_time = time.time()

    # 1. Load Requirements & Ground Truth
    req_file = BENCHMARK_DIR / "requirements.json"
    gt_file = BENCHMARK_DIR / "ground_truth.json"
    docs_dir = BENCHMARK_DIR / "documents"

    with open(req_file, "r", encoding="utf-8") as f:
        ground_truth_reqs = json.load(f)
    with open(gt_file, "r", encoding="utf-8") as f:
        ground_truth_links = json.load(f)

    gt_by_id = {r["requirement_id"]: r for r in ground_truth_reqs}
    links_by_id = {l["requirement_id"]: l for l in ground_truth_links}

    print(f"\n[1/5] Ingesting & Chunking Document Corpus (20 Documents)...")
    all_chunks = []
    chunk_id_counter = 1

    doc_files = sorted(docs_dir.glob("*.*"))
    for df in doc_files:
        try:
            chunks = parse_document(str(df))
            for c in chunks:
                all_chunks.append({
                    "id": f"chunk-{chunk_id_counter:04d}",
                    "document_id": df.stem,
                    "document_name": df.name,
                    "doc_type": "Technical specification" if "01_" in df.name else ("Compliance matrix" if df.suffix == ".xlsx" else "Test report"),
                    "page_number": c.page_number,
                    "content": c.content,
                    "metadata": c.metadata,
                })
                chunk_id_counter += 1
        except Exception as ex:
            print(f"  [WARN] Failed parsing {df.name}: {ex}")

    print(f"  [OK] Successfully indexed {len(all_chunks)} evidence chunks across {len(doc_files)} files.")

    # 2. Precompute Semantic Embeddings
    print(f"\n[2/5] Pre-computing Semantic Embeddings with Gemini text-embedding-001...")
    chunk_embeddings = await precompute_chunk_embeddings(all_chunks)
    valid_embs = sum(1 for e in chunk_embeddings if e is not None)
    print(f"  [OK] Semantic vectors available for {valid_embs}/{len(all_chunks)} chunks.")

    # 3. Requirement Extraction Evaluation
    print(f"\n[3/5] Evaluating Requirement Extraction from Master Specification...")
    srs_chunks = [c["content"] for c in all_chunks if "01_System_Requirements" in c["document_name"]]
    srs_text = "\n".join(srs_chunks)

    # Use LLM chunked extraction with deduplication
    extracted_reqs = await extract_requirements_from_text(
        text=srs_text,
        doc_name="01_System_Requirements_Specification_SRS.docx",
        model=settings.LLM_MODEL,
    )

    extracted_dict = {r.req_code: r for r in extracted_reqs}
    id_tp_extract = sum(1 for r in ground_truth_reqs if r["requirement_id"] in extracted_dict)
    tp_extract = sum(
        1 for r in ground_truth_reqs
        if r["requirement_id"] in extracted_dict
        and _token_f1(extracted_dict[r["requirement_id"]].description or extracted_dict[r["requirement_id"]].title, r["requirement_text"]) >= 0.65
    )
    fp_extract = max(0, len(extracted_reqs) - tp_extract)
    fn_extract = len(ground_truth_reqs) - tp_extract
    ext_p = (tp_extract / len(extracted_reqs) * 100.0) if extracted_reqs else 0.0
    ext_r = (tp_extract / len(ground_truth_reqs) * 100.0) if ground_truth_reqs else 0.0
    ext_f1 = (2 * ext_p * ext_r / (ext_p + ext_r)) if (ext_p + ext_r) > 0 else 0.0

    total_gt_conditions = sum(len(r.get("conditions", [])) for r in ground_truth_reqs)
    matched_extracted_conditions = _count_semantically_matched_conditions(
        ground_truth_reqs,
        extracted_dict,
    )
    exact_matched_extracted_conditions = _count_exactly_matched_conditions(
        ground_truth_reqs,
        extracted_dict,
    )
    ext_condition_recall = (
        matched_extracted_conditions / total_gt_conditions * 100.0 if total_gt_conditions else 0.0
    )
    exact_condition_recall = (
        exact_matched_extracted_conditions / total_gt_conditions * 100.0 if total_gt_conditions else 0.0
    )
    contract_diagnostics = _decomposed_contract_metrics(
        _match_extracted_contracts(ground_truth_reqs, extracted_dict)
    )
    complete_contract_ids = [
        requirement.req_code
        for requirement in extracted_reqs
        if requirement.contract_complete
    ]
    incomplete_contract_ids = [
        requirement.req_code
        for requirement in extracted_reqs
        if not requirement.contract_complete
    ]
    unmapped_obligation_count = sum(
        len(requirement.unmapped_obligations)
        for requirement in extracted_reqs
    )
    self_reported_completeness_rate = _percent(
        len(complete_contract_ids),
        len(extracted_reqs),
    )

    print(f"  • Ground Truth Requirements : {len(ground_truth_reqs)}")
    print(f"  • Extracted Requirements    : {len(extracted_reqs)}")
    print(f"  • Extraction Precision      : {ext_p:.2f}%")
    print(f"  • Extraction Recall         : {ext_r:.2f}%")
    print(f"  • Extraction F1-Score       : {ext_f1:.2f}%")
    print(f"  • Requirement ID Recall     : {id_tp_extract / len(ground_truth_reqs) * 100.0:.2f}%")
    print(f"  • Atomic Condition Recall   : {ext_condition_recall:.2f}%")
    print(f"  • Normalized Exact Contract Recall: {exact_condition_recall:.2f}%")
    print(
        "  • Extraction Completeness Gate: "
        f"{self_reported_completeness_rate:.2f}% "
        f"({len(complete_contract_ids)}/{len(extracted_reqs)} contracts internally complete; "
        f"{unmapped_obligation_count} unmapped obligation(s))"
    )
    print(
        "  • Contract Field Accuracy  : "
        f"parameter={contract_diagnostics['metrics']['parameter_exact_accuracy']}%, "
        f"canonical-parameter={contract_diagnostics['metrics']['parameter_canonical_accuracy']}% "
        f"(coverage={contract_diagnostics['metrics']['parameter_canonical_coverage']}%), "
        f"operator={contract_diagnostics['metrics']['operator_accuracy']}%, "
        f"unit={contract_diagnostics['metrics']['unit_exact_accuracy']}%"
    )

    pipeline_requirements = _build_pipeline_requirements(
        mode,
        extracted_reqs,
        ground_truth_reqs,
    )
    pipeline_by_id = {item["req_code"]: item for item in pipeline_requirements}
    print(
        f"  • Downstream Requirement Source: "
        f"{'GROUND-TRUTH ORACLE CONTRACTS' if contract_oracle else 'EXTRACTED CONTRACTS'}"
    )

    # 4. Evidence Retrieval Evaluation (Document Recall@K vs Passage Recall@K)
    print(f"\n[4/5] Evaluating Hybrid Evidence Retrieval across 100 Requirements...")
    doc_hits = {"R@1": 0, "R@3": 0, "R@5": 0}
    passage_hits = {"R@1": 0, "R@3": 0, "R@5": 0}
    doc_reciprocal_ranks = []
    passage_reciprocal_ranks = []
    retrieved_by_req: dict[str, list[dict]] = {}

    eval_queries = [r for r in ground_truth_reqs if links_by_id.get(r["requirement_id"], {}).get("expected_evidence")]

    for source_req in pipeline_requirements:
        req_id = source_req["req_code"]
        query_text = f"{req_id} {source_req['title']} {source_req['description']}"

        # Hybrid retrieval — the SRS itself is excluded from candidates because its
        # chunks contain the requirement text verbatim and always occupy rank 1.
        condition_queries = [
            " ".join(str(value) for value in (
                req_id,
                condition.get("description", ""),
                condition.get("parameter", ""),
                condition.get("operator", ""),
                condition.get("threshold", ""),
                condition.get("min_value", ""),
                condition.get("max_value", ""),
                condition.get("unit", ""),
            ) if value not in (None, ""))
            for condition in source_req.get("conditions", [])
        ]
        retrieved = await retrieve_candidate_evidence_hybrid(
            requirement_text=query_text,
            chunks=all_chunks,
            chunk_embeddings=chunk_embeddings,
            top_k=5,
            exclude_doc_names={SRS_DOC_NAME},
            condition_queries=condition_queries,
        )

        candidate_chunks = [
            {
                "id": c.chunk_id,
                "document_id": c.document_id,
                "document_name": c.document_name,
                "doc_type": c.doc_type,
                "page_number": c.page_number,
                "content": c.content,
                "score": c.score,
            }
            for c in retrieved
        ]
        retrieved_by_req[req_id] = candidate_chunks

        # Check retrieval hit against ground truth
        gt_link = links_by_id.get(req_id, {})
        expected_ev = gt_link.get("expected_evidence", [])
        
        if expected_ev:
            exp_docs = {e["document"].lower() for e in expected_ev if e.get("document")}
            exp_quotes = [e["quote"].lower() for e in expected_ev if e.get("quote")]

            doc_hit_rank = None
            passage_hit_rank = None

            for rank, c in enumerate(candidate_chunks[:5], 1):
                doc_match = c["document_name"].lower() in exp_docs
                content_lower = c["content"].lower()
                quote_match = doc_match and any(_passage_matches(q, content_lower) for q in exp_quotes)

                if doc_match and doc_hit_rank is None:
                    doc_hit_rank = rank
                if quote_match and passage_hit_rank is None:
                    passage_hit_rank = rank

            # Document Recall stats
            if doc_hit_rank == 1:
                doc_hits["R@1"] += 1
                doc_hits["R@3"] += 1
                doc_hits["R@5"] += 1
                doc_reciprocal_ranks.append(1.0)
            elif doc_hit_rank in (2, 3):
                doc_hits["R@3"] += 1
                doc_hits["R@5"] += 1
                doc_reciprocal_ranks.append(1.0 / doc_hit_rank)
            elif doc_hit_rank in (4, 5):
                doc_hits["R@5"] += 1
                doc_reciprocal_ranks.append(1.0 / doc_hit_rank)
            else:
                doc_reciprocal_ranks.append(0.0)

            # Passage Recall stats
            if passage_hit_rank == 1:
                passage_hits["R@1"] += 1
                passage_hits["R@3"] += 1
                passage_hits["R@5"] += 1
                passage_reciprocal_ranks.append(1.0)
            elif passage_hit_rank in (2, 3):
                passage_hits["R@3"] += 1
                passage_hits["R@5"] += 1
                passage_reciprocal_ranks.append(1.0 / passage_hit_rank)
            elif passage_hit_rank in (4, 5):
                passage_hits["R@5"] += 1
                passage_reciprocal_ranks.append(1.0 / passage_hit_rank)
            else:
                passage_reciprocal_ranks.append(0.0)

    # An expected requirement that extraction omitted is a retrieval miss in a
    # true end-to-end run. Ground truth is used here only as the scoring oracle.
    retrieved_expected_ids = set(retrieved_by_req)
    for expected_req in eval_queries:
        if expected_req["requirement_id"] not in retrieved_expected_ids:
            doc_reciprocal_ranks.append(0.0)
            passage_reciprocal_ranks.append(0.0)

    total_q = len(eval_queries)
    doc_r1 = (doc_hits["R@1"] / total_q * 100.0) if total_q else 0.0
    doc_r3 = (doc_hits["R@3"] / total_q * 100.0) if total_q else 0.0
    doc_r5 = (doc_hits["R@5"] / total_q * 100.0) if total_q else 0.0
    doc_mrr = (sum(doc_reciprocal_ranks) / total_q) if total_q else 0.0

    passage_r1 = (passage_hits["R@1"] / total_q * 100.0) if total_q else 0.0
    passage_r3 = (passage_hits["R@3"] / total_q * 100.0) if total_q else 0.0
    passage_r5 = (passage_hits["R@5"] / total_q * 100.0) if total_q else 0.0
    passage_mrr = (sum(passage_reciprocal_ranks) / total_q) if total_q else 0.0

    print(f"  • Evaluated Queries         : {total_q}")
    print(f"  • Document Recall@3         : {doc_r3:.2f}% (R@1: {doc_r1:.2f}%, R@5: {doc_r5:.2f}%, MRR: {doc_mrr:.4f})")
    print(f"  • Exact Passage Recall@3    : {passage_r3:.2f}% (R@1: {passage_r1:.2f}%, R@5: {passage_r5:.2f}%, MRR: {passage_mrr:.4f})")

    # 5. Verification Assessment Evaluation (5-Class Verification)
    print(f"\n[5/5] Executing 5-Class Multi-Condition Compliance Verification...")
    verification_evidence_by_req = (
        {
            requirement["requirement_id"]: _oracle_evidence_chunks(
                links_by_id.get(requirement["requirement_id"], {}),
                all_chunks,
            )
            for requirement in ground_truth_reqs
        }
        if evidence_oracle
        else retrieved_by_req
    )
    print(
        "  • Downstream Evidence Source: "
        f"{'GROUND-TRUTH ORACLE PASSAGES' if evidence_oracle else 'RETRIEVED TOP-5 PASSAGES'}"
    )
    req_items = [
        {
            **source_req,
            "candidate_chunks": verification_evidence_by_req.get(source_req["req_code"], []),
        }
        for source_req in pipeline_requirements
    ]

    assessments = await batch_assess_requirements(
        req_items=req_items,
        model=settings.LLM_MODEL,
        thinking_level=settings.GEMINI_THINKING_LEVEL,
        batch_size=5,
        spec_doc_names={"01_System_Requirements_Specification_SRS.docx"},
    )

    # 5x5 Confusion Matrix: matrix[expected][actual]
    matrix = {exp: {act: 0 for act in BENCHMARK_CLASSES} for exp in BENCHMARK_CLASSES}
    
    correct_count = 0
    predictions = {}
    failures = []

    # True Atomic Condition Tracking across all 172 conditions
    total_atomic_conditions = 0
    correct_atomic_conditions = 0
    cond_tp = 0
    cond_fp = 0
    cond_fn = 0
    cond_tn = 0

    numerical_total = 0
    numerical_correct = 0
    condition_confusion = {
        expected: {actual: 0 for actual in CONDITION_CLASSES}
        for expected in CONDITION_CLASSES
    }
    condition_label_sources = Counter()
    condition_source_correct = Counter()
    raw_llm_total = 0
    raw_llm_correct = 0
    python_transition_effect = Counter()
    audit_defensible_count = 0
    explicit_audit_defensible_count = 0
    requirements_with_explicit_atomic_truth = 0
    condition_records: list[dict[str, Any]] = []
    unsupported_claims = 0  # Missing evidence falsely claimed as SUPPORTED
    extraction_missing_count = 0

    for r in ground_truth_reqs:
        req_id = r["requirement_id"]
        expected_status = normalize_status_5(r["expected_status"])
        assessment = assessments.get(req_id)
        actual_raw = assessment.coverage_status if assessment else "UNKNOWN"
        actual_status = normalize_status_5(actual_raw)
        gt_link = links_by_id.get(req_id, {})
        retrieved_chunks = retrieved_by_req.get(req_id, [])
        diagnostics = dict(assessment.pipeline_diagnostics if assessment else {})
        review_gate = dict(diagnostics.get("review_gate") or {})
        review_state = assessment.review_state if assessment else "Needs review"
        review_required = bool(
            review_gate.get("required", review_state == "Needs review")
        )
        auto_close_eligible = bool(
            review_gate.get(
                "auto_close_eligible",
                actual_status == "SUPPORTED" and review_state in {"Reviewed", "Approved"},
            )
        )

        predictions[req_id] = {
            "expected": expected_status,
            "predicted": actual_status,
            "confidence": assessment.confidence if assessment else 0.0,
            "review_state": review_state,
            "review_required": review_required,
            "auto_close_eligible": auto_close_eligible,
            "review_gate_reasons": list(review_gate.get("reasons") or []),
            "reason": assessment.ai_analysis if assessment else "None",
            "condition_results": [
                result.model_dump() for result in (assessment.condition_results if assessment else [])
            ],
            "pipeline_diagnostics": diagnostics,
        }

        matrix[expected_status][actual_status] += 1

        extraction_missing = not contract_oracle and req_id not in pipeline_by_id
        if extraction_missing:
            extraction_missing_count += 1
        is_correct = bool(assessment) and not extraction_missing and (expected_status == actual_status)
        if is_correct:
            correct_count += 1
        else:
            # Signal-Based Failure Categorization
            exp_docs = {e["document"].lower() for e in gt_link.get("expected_evidence", []) if e.get("document")}
            retrieved_doc_names = {c["document_name"].lower() for c in retrieved_chunks}
            retrieval_missed = bool(exp_docs and not (exp_docs & retrieved_doc_names))
            citation_traceability_failed = any(
                "could not be traced to the referenced evidence" in (result.reason or "")
                for result in (assessment.condition_results if assessment else [])
            )
            provisional_status = normalize_status_5(diagnostics.get("llm_provisional_status"))

            if extraction_missing:
                cat = "EXTRACTION_FAILURE"
            elif diagnostics.get("decision_source") == "llm" and provisional_status == expected_status:
                cat = "POST_LLM_DETERMINISTIC_REGRESSION"
            elif retrieval_missed and not evidence_oracle:
                cat = "RETRIEVAL_FAILURE"
            elif citation_traceability_failed:
                cat = "CITATION_TRACEABILITY_FAILURE"
            elif expected_status == "UNKNOWN" and actual_status in ("SUPPORTED", "PARTIAL"):
                cat = "SOURCE_AUTHORITY_FAILURE"
            elif expected_status == "PARTIAL" and actual_status == "SUPPORTED":
                cat = "PARTIAL_COMPLIANCE_FAILURE"
            elif expected_status == "CONFLICT" and actual_status != "CONFLICT":
                cat = "CONTRADICTION_FAILURE"
            elif expected_status != "CONFLICT" and actual_status == "CONFLICT":
                cat = "CONTRADICTION_FAILURE"
            elif actual_status == "UNKNOWN" and expected_status in ("SUPPORTED", "PARTIAL", "CONFLICT"):
                cat = "UNKNOWN_CLASSIFICATION_FAILURE"
            elif any(c.get("operator") in ("between", "<=", ">=") for c in r.get("conditions", [])):
                cat = "NUMERIC_REASONING_FAILURE"
            else:
                cat = "LLM_FAILURE"

            failures.append({
                "requirement_id": req_id,
                "title": r["title"],
                "category": r["category"],
                "difficulty": r.get("difficulty", "Hard"),
                "expected_status": expected_status,
                "predicted_status": actual_status,
                "review_state": review_state,
                "review_required": review_required,
                "auto_close_eligible": auto_close_eligible,
                "review_gate_reasons": list(review_gate.get("reasons") or []),
                "failure_category": cat,
                "reason": assessment.ai_analysis if assessment else "No analysis produced",
                "retrieved_evidence": [c["content"][:100] for c in retrieved_chunks[:2]],
                "verification_evidence": [
                    c.get("content", "")[:100]
                    for c in verification_evidence_by_req.get(req_id, [])[:2]
                ],
                "ground_truth_note": gt_link.get("notes", ""),
                "pipeline_diagnostics": diagnostics,
            })

        # Atomic Condition Evaluation
        conds = r.get("conditions", [])
        predicted_condition_results = list(assessment.condition_results if assessment else [])
        raw_llm_results = list(diagnostics.get("llm_condition_results", []))
        reconciled_results = list(diagnostics.get("post_reconciliation_condition_results", []))
        pre_qualification_results = list(diagnostics.get("pre_qualification_condition_results", []))
        requirement_conditions_correct = True
        requirement_explicit_conditions_correct = True
        requirement_has_explicit_atomic_truth = False
        requirement_attribution_complete = True

        for cond in conds:
            total_atomic_conditions += 1
            cid = cond.get("condition_id", "")
            gt_cond_status, label_source = _ground_truth_condition_status(r, gt_link, cond)
            condition_label_sources[label_source] += 1

            # Score the verifier's actual per-condition output. Missing condition
            # IDs are conservatively UNTESTED; no ground-truth status is used to
            # manufacture a prediction.
            pred_cond_status = _predicted_condition_status(cond, predicted_condition_results)
            if pred_cond_status not in CONDITION_CLASSES:
                pred_cond_status = "INCONCLUSIVE"
            condition_confusion[gt_cond_status][pred_cond_status] += 1
            condition_matches = pred_cond_status == gt_cond_status
            requirement_conditions_correct = requirement_conditions_correct and condition_matches
            if label_source == "explicit":
                requirement_has_explicit_atomic_truth = True
                requirement_explicit_conditions_correct = (
                    requirement_explicit_conditions_correct and condition_matches
                )

            if condition_matches:
                correct_atomic_conditions += 1
                condition_source_correct[label_source] += 1

            if gt_cond_status == "PROVEN":
                if pred_cond_status == "PROVEN":
                    cond_tp += 1
                else:
                    cond_fn += 1
            else:
                if pred_cond_status == "PROVEN":
                    cond_fp += 1
                else:
                    cond_tn += 1

            is_numeric_condition = cond.get("operator") in ("<=", "<", ">=", ">", "between", "==", "=") and any(
                isinstance(cond.get(field), (int, float))
                or (isinstance(cond.get(field), str) and any(ch.isdigit() for ch in cond[field]))
                for field in ("threshold", "min_value", "max_value")
            )
            if is_numeric_condition:
                numerical_total += 1
                if condition_matches:
                    numerical_correct += 1

            raw_cond_status = None
            if raw_llm_results:
                raw_llm_total += 1
                raw_cond_status = _predicted_condition_status(cond, raw_llm_results)
                raw_matches = raw_cond_status == gt_cond_status
                raw_llm_correct += int(raw_matches)
                if raw_matches and not condition_matches:
                    python_transition_effect["damaged"] += 1
                elif not raw_matches and condition_matches:
                    python_transition_effect["corrected"] += 1
                elif raw_matches and condition_matches:
                    python_transition_effect["unchanged_correct"] += 1
                else:
                    python_transition_effect["unchanged_wrong"] += 1

            aligned_result = _predicted_condition_result(cond, predicted_condition_results)
            if pred_cond_status in {"PROVEN", "FAILED", "PENDING"}:
                evidence_ids = (
                    aligned_result.get("evidence_ids", [])
                    if isinstance(aligned_result, dict)
                    else getattr(aligned_result, "evidence_ids", [])
                ) if aligned_result is not None else []
                quote = (
                    aligned_result.get("quote")
                    if isinstance(aligned_result, dict)
                    else getattr(aligned_result, "quote", None)
                ) if aligned_result is not None else None
                requirement_attribution_complete = requirement_attribution_complete and bool(evidence_ids and quote)

            condition_records.append({
                "requirement_id": req_id,
                "condition_id": cid,
                "parameter": cond.get("parameter"),
                "expected_status": gt_cond_status,
                "ground_truth_source": label_source,
                "llm_status": raw_cond_status,
                "post_reconciliation_status": (
                    _predicted_condition_status(cond, reconciled_results)
                    if reconciled_results else None
                ),
                "pre_qualification_status": (
                    _predicted_condition_status(cond, pre_qualification_results)
                    if pre_qualification_results else None
                ),
                "final_status": pred_cond_status,
                "correct": condition_matches,
                "is_numeric_condition": is_numeric_condition,
            })

        if is_correct and requirement_conditions_correct and requirement_attribution_complete:
            audit_defensible_count += 1
        if requirement_has_explicit_atomic_truth:
            requirements_with_explicit_atomic_truth += 1
            if is_correct and requirement_explicit_conditions_correct and requirement_attribution_complete:
                explicit_audit_defensible_count += 1

        # Unsupported claims (Hallucination on MISSING)
        if expected_status == "MISSING" and actual_status == "SUPPORTED":
            unsupported_claims += 1

    total_eval = len(ground_truth_reqs)
    acc = (correct_count / total_eval * 100.0) if total_eval else 0.0

    # Per-Class Precision, Recall, F1
    per_class_metrics = {}
    p_sum, r_sum, f1_sum = 0.0, 0.0, 0.0

    for c in BENCHMARK_CLASSES:
        tp = matrix[c][c]
        fp = sum(matrix[other][c] for other in BENCHMARK_CLASSES if other != c)
        fn = sum(matrix[c][other] for other in BENCHMARK_CLASSES if other != c)
        support = sum(matrix[c][other] for other in BENCHMARK_CLASSES)

        p = (tp / (tp + fp) * 100.0) if (tp + fp) > 0 else 0.0
        r = (tp / (tp + fn) * 100.0) if (tp + fn) > 0 else 0.0
        f1 = (2 * p * r / (p + r)) if (p + r) > 0 else 0.0

        per_class_metrics[c] = {
            "precision": round(p, 2),
            "recall": round(r, 2),
            "f1": round(f1, 2),
            "support": support,
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
        }
        p_sum += p
        r_sum += r
        f1_sum += f1

    macro_p = p_sum / len(BENCHMARK_CLASSES)
    macro_r = r_sum / len(BENCHMARK_CLASSES)
    macro_f1 = f1_sum / len(BENCHMARK_CLASSES)

    # Condition metrics
    cond_acc = (correct_atomic_conditions / total_atomic_conditions * 100.0) if total_atomic_conditions else 0.0
    cond_prec = (cond_tp / (cond_tp + cond_fp) * 100.0) if (cond_tp + cond_fp) > 0 else 0.0
    cond_rec = (cond_tp / (cond_tp + cond_fn) * 100.0) if (cond_tp + cond_fn) > 0 else 0.0
    cond_f1 = (2 * cond_prec * cond_rec / (cond_prec + cond_rec)) if (cond_prec + cond_rec) > 0 else 0.0

    num_acc = (numerical_correct / numerical_total * 100.0) if numerical_total else 0.0
    missing_requirement_count = sum(
        1 for requirement in ground_truth_reqs
        if normalize_status_5(requirement.get("expected_status")) == "MISSING"
    )
    unsupported_rate = (
        unsupported_claims / missing_requirement_count * 100.0
        if missing_requirement_count else 0.0
    )
    raw_llm_accuracy = _percent(raw_llm_correct, raw_llm_total)
    audit_defensible_accuracy = _percent(audit_defensible_count, total_eval)
    condition_accuracy_by_label_source = {
        source: {
            "total": count,
            "correct": condition_source_correct[source],
            "accuracy": _percent(condition_source_correct[source], count),
        }
        for source, count in condition_label_sources.items()
    }
    explicit_condition_metrics = condition_accuracy_by_label_source.get("explicit", {
        "total": 0,
        "correct": 0,
        "accuracy": None,
    })
    inferred_condition_metrics = condition_accuracy_by_label_source.get("inferred_from_requirement", {
        "total": 0,
        "correct": 0,
        "accuracy": None,
    })
    explicit_audit_defensible_accuracy = _percent(
        explicit_audit_defensible_count,
        requirements_with_explicit_atomic_truth,
    )
    aggregation_oracle = _aggregation_oracle_metrics(ground_truth_reqs, links_by_id)
    review_gate_metrics = calculate_review_gate_safety_metrics(predictions)
    elapsed_time = round(time.time() - start_time, 2)


    # Print summary table
    print("\n" + "=" * 75)
    print(f"               BENCHMARK RESULTS (Accuracy: {acc:.2f}%)")
    print("=" * 75)
    print(f"{'Class':<12} | {'Precision':<10} | {'Recall':<10} | {'F1-Score':<10} | {'Support':<8}")
    print("-" * 65)
    for c in BENCHMARK_CLASSES:
        m = per_class_metrics[c]
        print(f"{c:<12} | {m['precision']:>9.2f}% | {m['recall']:>9.2f}% | {m['f1']:>9.2f}% | {m['support']:>8}")
    print("-" * 65)
    print(f"{'Macro Average':<12} | {macro_p:>9.2f}% | {macro_r:>9.2f}% | {macro_f1:>9.2f}% | {total_eval:>8}")
    print("=" * 75)

    print(f"\nTargeted Specialty Metrics:")
    print(f"  • Conflict Detection F1    : {per_class_metrics['CONFLICT']['f1']:.2f}%")
    print(f"  • Missing Evidence F1      : {per_class_metrics['MISSING']['f1']:.2f}%")
    print(f"  • Partial Detection F1     : {per_class_metrics['PARTIAL']['f1']:.2f}%")
    print(f"  • UNKNOWN Detection F1     : {per_class_metrics['UNKNOWN']['f1']:.2f}%")
    print(f"  • Unsupported Claim Rate   : {unsupported_rate:.2f}% ({unsupported_claims} false verifications on missing)")
    print(f"  • Mixed-Provenance Atomic Agreement: {cond_acc:.2f}% (P: {cond_prec:.2f}%, R: {cond_rec:.2f}%, F1: {cond_f1:.2f}%)")
    if explicit_condition_metrics["accuracy"] is not None:
        print(
            "  • Explicit Atomic Accuracy : "
            f"{explicit_condition_metrics['accuracy']:.2f}% "
            f"({explicit_condition_metrics['correct']}/{explicit_condition_metrics['total']})"
        )
    if inferred_condition_metrics["accuracy"] is not None:
        print(
            "  • Inferred Atomic Agreement: "
            f"{inferred_condition_metrics['accuracy']:.2f}% "
            f"({inferred_condition_metrics['correct']}/{inferred_condition_metrics['total']}; not authoritative ground truth)"
        )
    print(f"  • Numeric-Condition Verdict Accuracy: {num_acc:.2f}%")
    if raw_llm_accuracy is not None:
        print(f"  • Raw LLM Condition Accuracy: {raw_llm_accuracy:.2f}% ({raw_llm_total} LLM-scored conditions)")
        print(
            "  • Python Transition Effect : "
            f"corrected={python_transition_effect['corrected']}, "
            f"damaged={python_transition_effect['damaged']}, "
            f"unchanged-wrong={python_transition_effect['unchanged_wrong']}"
        )
    print(f"  • Aggregation Oracle Accuracy: {aggregation_oracle['accuracy']:.2f}%")
    print(f"  • Audit-Defensible Accuracy : {audit_defensible_accuracy:.2f}%")
    if explicit_audit_defensible_accuracy is not None:
        print(
            "  • Explicit-GT Audit Defensibility: "
            f"{explicit_audit_defensible_accuracy:.2f}% "
            f"({explicit_audit_defensible_count}/{requirements_with_explicit_atomic_truth} requirements with explicit atomic truth)"
        )
    if not condition_label_sources.get("explicit"):
        print("  • Atomic Ground Truth       : INFERRED from requirement labels (no explicit condition labels present)")
    capture_rate = review_gate_metrics["review_gate_capture_rate"]
    closure_precision = review_gate_metrics["automatic_closure_precision"]
    print("\nReview-Gate Business Safety Metrics:")
    print(
        "  • False SUPPORTED Predictions: "
        f"{review_gate_metrics['false_supported_predictions']}"
    )
    print(
        "  • Routed Safely to Review    : "
        f"{review_gate_metrics['false_supported_routed_to_review']}"
    )
    print(
        "  • False Automatic Closures   : "
        f"{review_gate_metrics['false_automatic_closures']}"
    )
    print(
        "  • Review-Gate Capture Rate   : "
        f"{capture_rate:.2f}%" if capture_rate is not None else
        "  • Review-Gate Capture Rate   : N/A (no false SUPPORTED predictions)"
    )
    print(
        "  • Automatic-Closure Precision: "
        f"{closure_precision:.2f}%" if closure_precision is not None else
        "  • Automatic-Closure Precision: N/A (no automatic closures)"
    )
    print(f"  • Total Benchmark Runtime  : {elapsed_time}s")

    # Output JSON results
    benchmark_results = {
        "benchmark_name": "TraceAudit AI 100-Requirement Complex Automotive Benchmark",
        "evaluation_mode": mode,
        "downstream_requirement_source": "ground_truth_oracle_contracts" if contract_oracle else "extracted_contracts",
        "downstream_evidence_source": "ground_truth_oracle_passages" if evidence_oracle else "retrieved_top_5_passages",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_requirements": total_eval,
        "runtime_seconds": elapsed_time,
        "extraction_metrics": {
            "precision": round(ext_p, 2),
            "recall": round(ext_r, 2),
            "f1": round(ext_f1, 2),
            "total_extracted": len(extracted_reqs),
            "id_recall": round(id_tp_extract / len(ground_truth_reqs) * 100.0, 2),
            "atomic_condition_recall": round(ext_condition_recall, 2),
            "atomic_condition_exact_recall": round(exact_condition_recall, 2),
            "missing_required_ids": extraction_missing_count,
            "decomposed_contract_metrics": contract_diagnostics["metrics"],
            "contract_mismatches": contract_diagnostics["mismatches"],
            "self_reported_contract_completeness_rate": round(self_reported_completeness_rate, 2),
            "complete_contract_ids": complete_contract_ids,
            "incomplete_contract_ids": incomplete_contract_ids,
            "unmapped_obligation_count": unmapped_obligation_count,
        },
        "retrieval_metrics": {
            "document_recall_at_1": round(doc_r1, 2),
            "document_recall_at_3": round(doc_r3, 2),
            "document_recall_at_5": round(doc_r5, 2),
            "document_mrr": round(doc_mrr, 4),
            "passage_recall_at_1": round(passage_r1, 2),
            "passage_recall_at_3": round(passage_r3, 2),
            "passage_recall_at_5": round(passage_r5, 2),
            "passage_mrr": round(passage_mrr, 4),
            "recall_at_3": round(doc_r3, 2),
            "recall_at_5": round(doc_r5, 2),
            "mean_reciprocal_rank": round(doc_mrr, 4),
        },
        "verification_metrics": {
            "accuracy": round(acc, 2),
            "accuracy_definition": (
                "end-to-end: requirement must be extracted and classified correctly"
                if not contract_oracle
                else "contract oracle: classification correctness using ground-truth contracts"
            ),
            "macro_precision": round(macro_p, 2),
            "macro_recall": round(macro_r, 2),
            "macro_f1": round(macro_f1, 2),
            "per_class": per_class_metrics,
            "confusion_matrix": matrix,
        },
        "condition_metrics": {
            "total_conditions": total_atomic_conditions,
            "correct_conditions": correct_atomic_conditions,
            "condition_accuracy": round(cond_acc, 2),
            "condition_precision": round(cond_prec, 2),
            "condition_recall": round(cond_rec, 2),
            "condition_f1": round(cond_f1, 2),
            "ground_truth_note": (
                "Primary atomic accuracy is the explicit-label metric. Mixed-provenance and inferred-label "
                "agreement are retained for regression tracking only; inferred labels are derived from final "
                "requirement status and missing_conditions and are not independently annotated truth."
            ),
            "primary_accuracy_metric": "accuracy_by_ground_truth_source.explicit.accuracy",
            "ground_truth_label_sources": dict(condition_label_sources),
            "accuracy_by_ground_truth_source": condition_accuracy_by_label_source,
            "status_confusion_matrix": condition_confusion,
            "raw_llm": {
                "total_conditions": raw_llm_total,
                "correct_conditions": raw_llm_correct,
                "condition_accuracy": raw_llm_accuracy,
            },
            "python_transition_effect": dict(python_transition_effect),
            "condition_records": condition_records,
        },
        "stage_diagnostics": {
            "aggregation_oracle": aggregation_oracle,
            "audit_defensible_requirements": audit_defensible_count,
            "audit_defensible_accuracy": audit_defensible_accuracy,
            "audit_defensible_definition": (
                "Final verdict correct, every atomic verdict correct, and every decisive PROVEN/FAILED/PENDING "
                "condition contains an evidence ID and quote."
            ),
            "explicit_ground_truth_audit": {
                "requirements": requirements_with_explicit_atomic_truth,
                "audit_defensible_requirements": explicit_audit_defensible_count,
                "accuracy": explicit_audit_defensible_accuracy,
                "definition": (
                    "Among requirements with explicit atomic truth: final verdict correct, every explicitly "
                    "labeled atomic verdict correct, and every decisive predicted condition is traceable."
                ),
            },
        },
        "review_gate_safety_metrics": review_gate_metrics,
        "specialty_metrics": {
            "conflict_f1": per_class_metrics["CONFLICT"]["f1"],
            "missing_f1": per_class_metrics["MISSING"]["f1"],
            "partial_f1": per_class_metrics["PARTIAL"]["f1"],
            "unknown_f1": per_class_metrics["UNKNOWN"]["f1"],
            "unsupported_claim_rate": round(unsupported_rate, 2),
            "condition_accuracy": round(cond_acc, 2),
            "condition_f1": round(cond_f1, 2),
            "numeric_condition_verdict_accuracy": round(num_acc, 2),
            # Backward-compatible alias. This is not a pure arithmetic metric;
            # it is atomic verdict accuracy restricted to numeric contracts.
            "numerical_range_accuracy": round(num_acc, 2),
            "numerical_range_accuracy_definition": "Deprecated alias of numeric_condition_verdict_accuracy",
        },
        "failures_count": len(failures),
        "failures": failures,
        "predictions": predictions,
        "retrieval_trace": retrieved_by_req,
        "verification_evidence_trace": verification_evidence_by_req,
        "extracted_requirements": [
            requirement.model_dump(exclude_none=True)
            for requirement in extracted_reqs
        ],
    }

    json_path = RESULTS_DIR / "complex_benchmark_results.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(benchmark_results, f, indent=2)
    print(f"\n[OK] Saved structured results JSON to {json_path.name}")

    # Generate Markdown Report
    generate_markdown_report(benchmark_results, failures)

    # Generate Structured Excel Audit Trace
    excel_path = RESULTS_DIR / "benchmark_audit_trace.xlsx"
    try:
        sys.path.insert(0, str(REPO_ROOT / "evaluation"))
        from export_excel_trace import export_benchmark_audit_trace_excel
        export_benchmark_audit_trace_excel(
            results=benchmark_results,
            ground_truth_reqs=ground_truth_reqs,
            links_by_id=links_by_id,
            retrieved_by_req=retrieved_by_req,
            assessments=assessments,
            predictions=predictions,
            failures=failures,
            excel_path=excel_path,
            pipeline_requirements=pipeline_requirements,
            verification_evidence_by_req=verification_evidence_by_req,
        )
    except Exception as ex:
        print(f"  [WARN] Failed exporting Excel Audit Trace: {ex}")

    return benchmark_results


def generate_markdown_report(results: dict, failures: list[dict]):
    """Generate extensive Markdown evaluation report."""
    md_path = RESULTS_DIR / "complex_benchmark_report.md"
    vm = results["verification_metrics"]
    rm = results["retrieval_metrics"]
    em = results["extraction_metrics"]
    sm = results["specialty_metrics"]
    review_safety = results.get("review_gate_safety_metrics", {})
    cm_data = results.get("condition_metrics", {})
    stage_data = results.get("stage_diagnostics", {})
    raw_llm_data = cm_data.get("raw_llm", {})
    raw_llm_acc = raw_llm_data.get("condition_accuracy")
    audit_defensible_acc = stage_data.get("audit_defensible_accuracy")
    aggregation_oracle_acc = stage_data.get("aggregation_oracle", {}).get("accuracy")
    transition_effect = cm_data.get("python_transition_effect", {})
    label_sources = cm_data.get("ground_truth_label_sources", {})
    explicit_atomic_accuracy = (
        cm_data.get("accuracy_by_ground_truth_source", {})
        .get("explicit", {})
        .get("accuracy")
    )
    explicit_atomic_display = (
        f"{explicit_atomic_accuracy:.2f}%"
        if explicit_atomic_accuracy is not None else "N/A"
    )
    matrix = vm["confusion_matrix"]

    failure_cat_counts = Counter(f["failure_category"] for f in failures)

    # 5x5 Confusion Matrix Table
    cm_header = "| Expected \\ Predicted | SUPPORTED | PARTIAL | CONFLICT | MISSING | UNKNOWN | Total |"
    cm_sep = "|---|:---:|:---:|:---:|:---:|:---:|:---:|"
    cm_rows = []
    for exp in BENCHMARK_CLASSES:
        row_vals = [str(matrix[exp][act]) for act in BENCHMARK_CLASSES]
        total_exp = sum(matrix[exp][act] for act in BENCHMARK_CLASSES)
        cm_rows.append(f"| **{exp}** | " + " | ".join(row_vals) + f" | **{total_exp}** |")

    # Top Failure Modes Table
    fail_rows = []
    for f in failures[:15]:
        fail_rows.append(
            f"| `{f['requirement_id']}` | **{f['expected_status']}** | `{f['predicted_status']}` | `{f['failure_category']}` | {f['ground_truth_note'][:90]}... |"
        )
    fail_table = "\n".join(fail_rows) if fail_rows else "| None | - | - | - | All requirements passed! |"

    content = f"""# TraceAudit AI — 100-Requirement Complex Benchmark Report

> **Benchmark Date:** {results['timestamp']}  
> **Evaluation Mode:** {results.get('evaluation_mode', 'oracle').upper()}
> **Downstream Requirement Source:** {results.get('downstream_requirement_source', 'ground_truth_oracle_contracts')}
> **Downstream Evidence Source:** {results.get('downstream_evidence_source', 'retrieved_top_5_passages')}
> **Total Requirements Evaluated:** 100  
> **Total Atomic Conditions Evaluated:** {cm_data.get('total_conditions', 172)}  
> **Total Technical Documents:** 20 (DOCX, PDF, XLSX)  
> **Overall Verification Accuracy:** **{vm['accuracy']:.2f}%**  
> **Macro F1-Score:** **{vm['macro_f1']:.2f}%**  

---

## 1. Executive Summary & Pipeline Health

The 100-requirement synthetic benchmark tests real-world automotive compliance auditing across 10 mission-critical domains (High-Voltage BMS, Traction Inverters, DC-DC Converters, Charging, Thermal, CAN-FD/Ethernet, UDS Diagnostics, Functional Safety ASIL-D, Cybersecurity ISO 21434, and Environmental EMC).

```
Pipeline Performance Summary:
  • Requirement Extraction F1 : {em['f1']:.2f}%
  • Normalized Exact Contract Recall: {em.get('atomic_condition_exact_recall', 0):.2f}%
  • Extraction Completeness Gate: {em.get('self_reported_contract_completeness_rate', 0):.2f}% ({len(em.get('incomplete_contract_ids', []))} routed as incomplete)
  • Document Retrieval Recall@3: {rm['document_recall_at_3']:.2f}% | Recall@5: {rm['document_recall_at_5']:.2f}% (MRR: {rm['document_mrr']:.4f})
  • Document-qualified Passage Recall@3: {rm['passage_recall_at_3']:.2f}% | Recall@5: {rm['passage_recall_at_5']:.2f}% (MRR: {rm['passage_mrr']:.4f})
  • 5-Class Requirement Macro F1: {vm['macro_f1']:.2f}% (Accuracy: {vm['accuracy']:.2f}%)
  • Mixed-Provenance Atomic Agreement: {cm_data.get('condition_accuracy', sm.get('condition_accuracy', 0)):.2f}% (F1: {cm_data.get('condition_f1', 0):.2f}%)
  • Explicit Atomic Accuracy   : {explicit_atomic_display}
  • Raw LLM Atomic Accuracy    : {f'{raw_llm_acc:.2f}%' if raw_llm_acc is not None else 'N/A (no LLM path)'}
  • Aggregation Oracle Accuracy: {f'{aggregation_oracle_acc:.2f}%' if aggregation_oracle_acc is not None else 'N/A'}
  • Audit-Defensible Accuracy  : {f'{audit_defensible_acc:.2f}%' if audit_defensible_acc is not None else 'N/A'}
  • Unsupported Claim Rate    : {sm['unsupported_claim_rate']:.2f}% (Evidence-grounded)
  • False Automatic Closures  : {review_safety.get('false_automatic_closures', 0)}
  • Auto-Closure Precision    : {f"{review_safety.get('automatic_closure_precision'):.2f}%" if review_safety.get('automatic_closure_precision') is not None else 'N/A'}
```

Atomic ground-truth provenance: **{label_sources.get('explicit', 0)} explicit** condition labels and **{label_sources.get('inferred_from_requirement', 0)} inferred** labels. Inferred labels are derived from the final requirement status and `missing_conditions`; they are useful for regression tracking but are not independently annotated atomic truth.

---

## 2. 5-Class Confusion Matrix

{cm_header}
{cm_sep}
{"\n".join(cm_rows)}

---

## 3. Detailed Verification Metrics by Class

| Class | Precision | Recall | F1-Score | Support | True Positives | False Positives | False Negatives |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **SUPPORTED** | {vm['per_class']['SUPPORTED']['precision']:.2f}% | {vm['per_class']['SUPPORTED']['recall']:.2f}% | {vm['per_class']['SUPPORTED']['f1']:.2f}% | {vm['per_class']['SUPPORTED']['support']} | {vm['per_class']['SUPPORTED']['true_positives']} | {vm['per_class']['SUPPORTED']['false_positives']} | {vm['per_class']['SUPPORTED']['false_negatives']} |
| **PARTIAL** | {vm['per_class']['PARTIAL']['precision']:.2f}% | {vm['per_class']['PARTIAL']['recall']:.2f}% | {vm['per_class']['PARTIAL']['f1']:.2f}% | {vm['per_class']['PARTIAL']['support']} | {vm['per_class']['PARTIAL']['true_positives']} | {vm['per_class']['PARTIAL']['false_positives']} | {vm['per_class']['PARTIAL']['false_negatives']} |
| **CONFLICT** | {vm['per_class']['CONFLICT']['precision']:.2f}% | {vm['per_class']['CONFLICT']['recall']:.2f}% | {vm['per_class']['CONFLICT']['f1']:.2f}% | {vm['per_class']['CONFLICT']['support']} | {vm['per_class']['CONFLICT']['true_positives']} | {vm['per_class']['CONFLICT']['false_positives']} | {vm['per_class']['CONFLICT']['false_negatives']} |
| **MISSING** | {vm['per_class']['MISSING']['precision']:.2f}% | {vm['per_class']['MISSING']['recall']:.2f}% | {vm['per_class']['MISSING']['f1']:.2f}% | {vm['per_class']['MISSING']['support']} | {vm['per_class']['MISSING']['true_positives']} | {vm['per_class']['MISSING']['false_positives']} | {vm['per_class']['MISSING']['false_negatives']} |
| **UNKNOWN** | {vm['per_class']['UNKNOWN']['precision']:.2f}% | {vm['per_class']['UNKNOWN']['recall']:.2f}% | {vm['per_class']['UNKNOWN']['f1']:.2f}% | {vm['per_class']['UNKNOWN']['support']} | {vm['per_class']['UNKNOWN']['true_positives']} | {vm['per_class']['UNKNOWN']['false_positives']} | {vm['per_class']['UNKNOWN']['false_negatives']} |
| **MACRO AVG** | **{vm['macro_precision']:.2f}%** | **{vm['macro_recall']:.2f}%** | **{vm['macro_f1']:.2f}%** | **{results['total_requirements']}** | - | - | - |

---

## 4. Multi-Condition & Specialty Metrics

| Metric | Score | Industry Target | Assessment |
|---|:---:|:---:|:---|
| **Conflict Detection F1** | **{sm['conflict_f1']:.2f}%** | >= 90.0% | {'✅ Met' if sm['conflict_f1'] >= 90 else '⚠️ Review Needed'} |
| **Missing Evidence Detection F1** | **{sm['missing_f1']:.2f}%** | >= 90.0% | {'✅ Met' if sm['missing_f1'] >= 90 else '⚠️ Review Needed'} |
| **Partial Compliance Detection F1** | **{sm['partial_f1']:.2f}%** | >= 85.0% | {'✅ Met' if sm['partial_f1'] >= 85 else '⚠️ Review Needed'} |
| **UNKNOWN Detection F1** | **{sm.get('unknown_f1', 0):.2f}%** | >= 70.0% | {'✅ Met' if sm.get('unknown_f1', 0) >= 70 else '⚠️ Review Needed'} |
| **Mixed-Provenance Atomic Agreement** | **{cm_data.get('condition_accuracy', 0):.2f}%** | Regression only | Inferred labels are not authoritative |
| **Explicit Atomic Accuracy** | **{explicit_atomic_display}** | >= 90.0% | {'✅ Met' if (explicit_atomic_accuracy or 0) >= 90 else '⚠️ Review Needed'} |
| **Atomic Condition F1** | **{cm_data.get('condition_f1', 0):.2f}%** | >= 85.0% | {'✅ Met' if cm_data.get('condition_f1', 0) >= 85 else '⚠️ Review Needed'} |
| **Numeric-Condition Verdict Accuracy** | **{sm['numeric_condition_verdict_accuracy']:.2f}%** | >= 90.0% | {'✅ Met' if sm['numeric_condition_verdict_accuracy'] >= 90 else '⚠️ Review Needed'} |
| **Audit-Defensible Accuracy** | **{(audit_defensible_acc or 0):.2f}%** | >= 90.0% | {'✅ Met' if (audit_defensible_acc or 0) >= 90 else '⚠️ Review Needed'} |
| **Unsupported Claim Rate** | **{sm['unsupported_claim_rate']:.2f}%** | <= 5.0% | {'✅ Safe' if sm['unsupported_claim_rate'] <= 5 else '❌ High Risk'} |

### Review-Gate Business Safety

The review gate does not change semantic verdict accuracy. It controls whether a predicted `SUPPORTED` requirement is safe to close automatically.

| Metric | Result | Safety Target | Assessment |
|---|:---:|:---:|:---:|
| **False SUPPORTED Predictions** | **{review_safety.get('false_supported_predictions', 0)}** | Classification diagnostic | Reported separately |
| **False SUPPORTED Routed to Review** | **{review_safety.get('false_supported_routed_to_review', 0)}** | All false SUPPORTED | {'✅' if review_safety.get('false_supported_routed_to_review', 0) == review_safety.get('false_supported_predictions', 0) else '⚠️'} |
| **Review-Gate Capture Rate** | **{f"{review_safety.get('review_gate_capture_rate'):.2f}%" if review_safety.get('review_gate_capture_rate') is not None else 'N/A'}** | 100% | {'✅ Safe' if review_safety.get('review_gate_capture_rate') in (None, 100.0) else '❌ Review Escapes'} |
| **False Automatic Closures** | **{review_safety.get('false_automatic_closures', 0)}** | 0 | {'✅ Safe' if review_safety.get('false_automatic_closures', 0) == 0 else '❌ Unsafe'} |
| **Automatic-Closure Precision** | **{f"{review_safety.get('automatic_closure_precision'):.2f}%" if review_safety.get('automatic_closure_precision') is not None else 'N/A'}** | 100% | {'✅ Safe' if review_safety.get('automatic_closure_precision') in (None, 100.0) else '❌ Unsafe'} |
| **Supported Review Rate** | **{f"{review_safety.get('supported_review_rate'):.2f}%" if review_safety.get('supported_review_rate') is not None else 'N/A'}** | Monitor | Workflow load indicator |

---

## 5. Signal-Based Root Cause Failure Classification

```
Total Failures: {len(failures)} / 100
Failure Categorization:
"""
    for cat, cnt in failure_cat_counts.most_common():
        content += f"  • {cat:<35}: {cnt:2d} failure(s)\n"

    content += f"""```

### Detailed Failure Cases:

| Req ID | Expected | Predicted | Failure Category | Ground Truth Failure Context |
|---|:---:|:---:|---|---|
{fail_table}

---

## 6. Bottleneck Analysis & Next Technical Improvements

### 🔍 Main Bottleneck:
"""
    if aggregation_oracle_acc is not None and aggregation_oracle_acc < 100.0:
        content += "- **Aggregation Policy** is inconsistent with the benchmark's atomic-state semantics even when expected condition states are supplied directly.\n"
    elif transition_effect.get("damaged", 0) > transition_effect.get("corrected", 0):
        content += "- **Deterministic Post-Processing** causes more atomic regressions than corrections. Inspect qualification and reconciliation transitions in the atomic diagnostics sheet.\n"
    elif rm['document_recall_at_5'] < 90.0:
        content += "- **Retrieval & Evidence Ranking** is the primary bottleneck. Evidence chunks for complex cross-system requirements were missed in the top-5 candidate pool.\n"
    elif raw_llm_acc is not None and raw_llm_acc < 90.0:
        content += "- **LLM Atomic Evidence Interpretation** is the primary measured bottleneck after separating it from Python post-processing.\n"
    elif vm['macro_f1'] < 90.0:
        content += "- **Multi-Condition Reasoner & Scope Discrimination** is the primary bottleneck. Retrieval succeeded in finding candidate chunks, but multi-condition boundaries or component scope limits were misclassified.\n"
    else:
        content += "- **Pipeline is well-balanced** with high fidelity across both semantic retrieval and hybrid verification stages.\n"

    content += """
### 🚀 Recommended Next Improvements:
1. **Annotate Explicit Atomic Truth:** Add an expected state for every benchmark condition so atomic accuracy no longer depends on labels inferred from the final requirement verdict.
2. **Use Oracle Ablations:** Compare end-to-end, contract-oracle, evidence-oracle, and combined-oracle runs before changing a production stage.
3. **Review Stage Transitions:** Prioritize cases marked `DAMAGED`, then address `UNCHANGED_WRONG`; do not tune aggregation based only on final-status mismatches.
"""

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[OK] Generated detailed Markdown report at {md_path.name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the TraceAudit complex benchmark")
    parser.add_argument(
        "--mode",
        choices=tuple(MODE_ALIASES),
        default="end-to-end",
        help=(
            "end-to-end uses extracted contracts and retrieved evidence; oracle-contracts bypasses extraction; "
            "oracle-evidence bypasses retrieval; oracle-contracts-evidence bypasses both. "
            "The legacy 'oracle' name aliases oracle-contracts."
        ),
    )
    args = parser.parse_args()
    asyncio.run(run_benchmark(mode=args.mode))
