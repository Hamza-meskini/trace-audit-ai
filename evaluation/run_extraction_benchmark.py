"""Run the complex extraction benchmark without retrieval or verdict reasoning."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import itertools
import json
import math
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "backend"
BENCHMARK = REPO / "evaluation" / "extraction_benchmark"
DOCS = BENCHMARK / "documents"
RESULTS = REPO / "evaluation" / "results"
RESULTS.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BENCHMARK))

from app.config import settings
from app.services.extraction import extract_requirements_from_text
from app.services.ingestion import parse_document_with_metadata
from app.services.requirement_visual_recovery import recover_requirement_text_from_pages
from validate_extraction_benchmark import validate


DEFAULT_MODEL = "system.ai.llama-4-maverick"
EXTRACTION_CHECKPOINT_VERSION = "split-model-atomic-v3"


def extraction_checkpoint_path(
    model: str,
    document_names: set[str],
    atomic_model: str | None = None,
    atomic_thinking_level: str | None = None,
    atomic_fallback_model: str | None = None,
) -> Path:
    model_slug = re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")
    atomic_slug = re.sub(
        r"[^a-z0-9]+",
        "-",
        f"{atomic_model or 'same'}-{atomic_thinking_level or 'default'}-{atomic_fallback_model or 'none'}".lower(),
    ).strip("-")
    scope_digest = hashlib.sha256(
        "\n".join(sorted(document_names)).encode("utf-8")
    ).hexdigest()[:10]
    return RESULTS / f".extraction-checkpoint-{model_slug}-atomic-{atomic_slug}-{scope_digest}.json"


def load_extraction_checkpoint(
    path: Path,
    dataset: dict[str, Any],
    model: str,
    document_names: set[str],
    atomic_model: str | None = None,
    atomic_thinking_level: str | None = None,
    atomic_fallback_model: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    expected = {
        "checkpoint_version": EXTRACTION_CHECKPOINT_VERSION,
        "benchmark_id": dataset.get("benchmark_id"),
        "benchmark_version": dataset.get("version"),
        "model": model,
        "provider": settings.LLM_PROVIDER,
        "documents": sorted(document_names),
        "atomic_model": atomic_model,
        "atomic_thinking_level": atomic_thinking_level,
        "atomic_fallback_model": atomic_fallback_model,
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        return {}
    predictions = payload.get("predictions")
    if not isinstance(predictions, dict):
        return {}
    return {
        name: rows
        for name, rows in predictions.items()
        if name in document_names and isinstance(rows, list)
    }


def save_extraction_checkpoint(
    path: Path,
    dataset: dict[str, Any],
    model: str,
    document_names: set[str],
    predictions: dict[str, list[dict[str, Any]]],
    atomic_model: str | None = None,
    atomic_thinking_level: str | None = None,
    atomic_fallback_model: str | None = None,
) -> None:
    payload = {
        "checkpoint_version": EXTRACTION_CHECKPOINT_VERSION,
        "benchmark_id": dataset.get("benchmark_id"),
        "benchmark_version": dataset.get("version"),
        "model": model,
        "provider": settings.LLM_PROVIDER,
        "documents": sorted(document_names),
        "atomic_model": atomic_model,
        "atomic_thinking_level": atomic_thinking_level,
        "atomic_fallback_model": atomic_fallback_model,
        "completed_documents": sorted(predictions),
        "predictions": predictions,
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def tokens(value: Any) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(value or "").lower().replace("µ", "u"))


def token_f1(left: Any, right: Any) -> float:
    a, b = Counter(tokens(left)), Counter(tokens(right))
    if not a or not b:
        return 0.0
    overlap = sum((a & b).values())
    precision, recall = overlap / sum(b.values()), overlap / sum(a.values())
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def source_span_grounded(span: Any, source: Any) -> bool:
    span_tokens = tokens(span)
    source_tokens = set(tokens(source))
    if not span_tokens or not source_tokens:
        return False
    if len(span_tokens) < 4:
        return all(token in source_tokens for token in span_tokens)
    return sum(token in source_tokens for token in span_tokens) / len(span_tokens) >= 0.9


def logic_leaf_ids(node: Any) -> set[str]:
    if not isinstance(node, dict):
        return set()
    output = {str(node["condition_id"])} if node.get("condition_id") else set()
    for child in node.get("children") or []:
        output.update(logic_leaf_ids(child))
    for key in ("antecedent", "consequent", "if", "then"):
        output.update(logic_leaf_ids(node.get(key)))
    return output


def pct(part: int | float, whole: int | float) -> float:
    return round(100.0 * part / whole, 2) if whole else 0.0


def normalized_id(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def load_requirement_vision_cache(dataset: dict[str, Any]) -> dict[str, dict[int, dict[str, Any]]]:
    """Reuse a completed visual baseline instead of repeatedly billing providers."""
    path = RESULTS / "extraction_all_requirement-vision_results.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if payload.get("benchmark_id") != dataset.get("benchmark_id") or payload.get("benchmark_version") != dataset.get("version"):
        return {}
    documents = payload.get("metrics", {}).get("requirement_visual_recovery", {}).get("documents", {})
    cache: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)
    for document_name, diagnostics in documents.items():
        for page in diagnostics.get("pages", []):
            if page.get("status") not in {"complete", "cache_hit"}:
                continue
            cache[document_name][int(page["page"])] = {
                key: value for key, value in page.items()
                if key not in {"page", "reasons"}
            }
            cache[document_name][int(page["page"])]["status"] = "complete"
    return dict(cache)


def normalized_operator(value: Any) -> str:
    raw = str(value or "").strip().lower()
    aliases = {
        "at_least": ">=", "gte": ">=", "greater_than_or_equal_to": ">=", "greater_or_equal": ">=",
        "at_most": "<=", "lte": "<=", "less_than_or_equal_to": "<=", "less_or_equal": "<=",
        "greater_than": ">", "less_than": "<",
        "equals": "==", "equal": "==", "equal_to": "==",
        "within": "between", "in_range": "between",
    }
    return aliases.get(raw, raw)


def normalized_unit(value: Any) -> str:
    raw = str(value or "").lower().replace("°", "deg").replace("ohms", "ohm")
    compact = re.sub(r"[^a-z0-9/%]", "", raw)
    aliases = {
        "seconds": "s", "second": "s", "sec": "s",
        "milliseconds": "ms", "millisecond": "ms",
        "minutes": "min", "minute": "min",
        "volts": "v", "volt": "v", "vdc": "v", "vac": "v",
        "percent": "%", "percentage": "%",
    }
    return aliases.get(compact, compact)


def same_scalar(left: Any, right: Any) -> bool:
    if left is None and right is None:
        return True
    if isinstance(left, bool) or isinstance(right, bool):
        return str(left).lower() == str(right).lower()
    try:
        return math.isclose(float(left), float(right), rel_tol=1e-6, abs_tol=1e-6)
    except (TypeError, ValueError):
        return normalized_id(left) == normalized_id(right)


def field_value(condition: dict[str, Any]) -> Any:
    if condition.get("threshold") is not None:
        return condition.get("threshold")
    if condition.get("min_value") is not None or condition.get("max_value") is not None:
        return (condition.get("min_value"), condition.get("max_value"))
    return None


def same_value(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    left, right = field_value(expected), field_value(actual)
    if isinstance(left, tuple) or isinstance(right, tuple):
        left = left if isinstance(left, tuple) else (None, None)
        right = right if isinstance(right, tuple) else (None, None)
        return same_scalar(left[0], right[0]) and same_scalar(left[1], right[1])
    return same_scalar(left, right)


def condition_score(expected: dict[str, Any], actual: dict[str, Any]) -> float:
    description = token_f1(expected.get("description"), actual.get("description"))
    parameter = token_f1(expected.get("parameter"), actual.get("parameter"))
    operator = float(normalized_operator(expected.get("operator")) == normalized_operator(actual.get("operator")))
    value = float(same_value(expected, actual))
    unit = float(normalized_unit(expected.get("unit")) == normalized_unit(actual.get("unit")))
    return 0.50 * description + 0.15 * parameter + 0.12 * operator + 0.15 * value + 0.08 * unit


def align_conditions(expected: list[dict[str, Any]], actual: list[dict[str, Any]]) -> list[tuple[int, int, float]]:
    if not expected or not actual:
        return []
    best: tuple[float, tuple[int, ...]] = (-1.0, ())
    # Atomic contracts in this benchmark have no more than three conditions.
    if len(actual) >= len(expected):
        for chosen in itertools.permutations(range(len(actual)), len(expected)):
            score = sum(condition_score(expected[i], actual[j]) for i, j in enumerate(chosen))
            if score > best[0]:
                best = (score, chosen)
        pairs = [(i, j, condition_score(expected[i], actual[j])) for i, j in enumerate(best[1])]
    else:
        inverse = align_conditions(actual, expected)
        pairs = [(j, i, score) for i, j, score in inverse]
    return [item for item in pairs if item[2] >= 0.30]


def source_visibility(
    dataset: dict[str, Any],
    parsed: dict[str, Any],
    recovered_by_page: dict[str, dict[int, str]] | None = None,
) -> dict[str, Any]:
    rows = []
    for requirement in dataset["requirements"]:
        result = parsed[requirement["document"]]
        page_chunks = [chunk.content for chunk in result.chunks if chunk.page_number == requirement["source_page"]]
        content = "\n".join(page_chunks)
        recovered = (recovered_by_page or {}).get(requirement["document"], {}).get(requirement["source_page"], "")
        if recovered:
            content = f"{content}\n{recovered}"
        expected_tokens = tokens(requirement["requirement_text"])
        actual_tokens = set(tokens(content))
        coverage = sum(token in actual_tokens for token in expected_tokens) / len(expected_tokens) if expected_tokens else 0.0
        rows.append({
            "requirement_id": requirement["requirement_id"],
            "document": requirement["document"],
            "page": requirement["source_page"],
            "modality": requirement["source_modality"],
            "id_visible": normalized_id(requirement["requirement_id"]) in normalized_id(content),
            "token_coverage": round(coverage, 4),
        })
    by_modality = {}
    for modality in sorted({row["modality"] for row in rows}):
        selected = [row for row in rows if row["modality"] == modality]
        by_modality[modality] = {
            "requirements": len(selected),
            "id_visibility": pct(sum(row["id_visible"] for row in selected), len(selected)),
            "mean_text_token_coverage": round(100 * sum(row["token_coverage"] for row in selected) / len(selected), 2),
        }
    return {
        "requirement_id_visibility": pct(sum(row["id_visible"] for row in rows), len(rows)),
        "mean_requirement_text_token_coverage": round(100 * sum(row["token_coverage"] for row in rows) / len(rows), 2),
        "by_modality": by_modality,
        "requirements": rows,
    }


def extraction_metrics(dataset: dict[str, Any], predictions: dict[str, list[dict[str, Any]]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    truth_by_doc = defaultdict(list)
    for item in dataset["requirements"]:
        truth_by_doc[item["document"]].append(item)
    expected_ids = {normalized_id(item["requirement_id"]): item for item in dataset["requirements"]}
    predicted_flat = [item for values in predictions.values() for item in values]
    predicted_by_id = {normalized_id(item.get("req_code")): item for item in predicted_flat}
    matched_ids = expected_ids.keys() & predicted_by_id.keys()
    rows: list[dict[str, Any]] = []
    counters = Counter()
    field_totals = Counter()
    structural = Counter()
    modality = defaultdict(Counter)

    for key, expected in expected_ids.items():
        actual = predicted_by_id.get(key)
        expected_conditions = expected["conditions"]
        actual_conditions = (actual or {}).get("conditions", [])
        actual_clauses = (actual or {}).get("semantic_clauses", [])
        actual_coverage = (actual or {}).get("clause_coverage", [])
        source_text = expected.get("requirement_text", "")
        for condition in actual_conditions:
            structural["conditions"] += 1
            structural["grounded_condition_spans"] += int(
                source_span_grounded(condition.get("source_span"), source_text)
            )
            structural["dual_parameters"] += int(bool(
                condition.get("source_parameter") and condition.get("canonical_parameter")
            ))
            structural["condition_clause_links"] += int(bool(condition.get("clause_ids")))
        normative_clause_ids = {
            str(clause.get("clause_id")) for clause in actual_clauses
            if clause.get("clause_type") in {"APPLICABILITY", "VERIFICATION"}
        }
        covered_clause_ids = {
            str(mapping.get("clause_id")) for mapping in actual_coverage
            if mapping.get("clause_id")
        }
        structural["normative_clauses"] += len(normative_clause_ids)
        structural["covered_normative_clauses"] += len(normative_clause_ids & covered_clause_ids)
        condition_ids = {
            str(condition.get("condition_id")) for condition in actual_conditions
            if condition.get("condition_id")
        }
        tree_ids = logic_leaf_ids((actual or {}).get("logic_tree"))
        structural["logic_tree_contracts"] += int(bool(condition_ids) and condition_ids.issubset(tree_ids))
        structural["found_contracts"] += int(actual is not None)
        structural["validation_pass_contracts"] += int(
            actual is not None
            and not (actual or {}).get("validation_issues")
            and (actual or {}).get("contract_complete") is True
        )
        pairs = align_conditions(expected_conditions, actual_conditions)
        counters["expected_conditions"] += len(expected_conditions)
        counters["predicted_conditions"] += len(actual_conditions)
        counters["aligned_conditions"] += len(pairs)
        modality[expected["source_modality"]]["requirements"] += 1
        modality[expected["source_modality"]]["found"] += int(actual is not None)
        modality[expected["source_modality"]]["expected_conditions"] += len(expected_conditions)
        modality[expected["source_modality"]]["aligned_conditions"] += len(pairs)
        exact_conditions = 0
        for expected_index, actual_index, score in pairs:
            left, right = expected_conditions[expected_index], actual_conditions[actual_index]
            checks = {
                "description": token_f1(left.get("description"), right.get("description")) >= 0.50,
                "parameter": token_f1(left.get("parameter"), right.get("parameter")) >= 0.50,
                "operator": normalized_operator(left.get("operator")) == normalized_operator(right.get("operator")),
                "value": same_value(left, right),
                "unit": normalized_unit(left.get("unit")) == normalized_unit(right.get("unit")),
                "role": str(left.get("condition_role")) == str(right.get("condition_role")),
                "visual": bool(left.get("requires_visual_evidence")) == bool(right.get("requires_visual_evidence")),
            }
            for name, passed in checks.items():
                field_totals[f"{name}_total"] += 1
                field_totals[f"{name}_correct"] += int(passed)
            exact_conditions += int(all(checks.values()))
        expected_logic = expected["logic"].get("operator")
        actual_logic = (actual or {}).get("logic", {}).get("operator")
        rows.append({
            "requirement_id": expected["requirement_id"],
            "document": expected["document"],
            "source_modality": expected["source_modality"],
            "found": actual is not None,
            "expected_condition_count": len(expected_conditions),
            "predicted_condition_count": len(actual_conditions),
            "aligned_condition_count": len(pairs),
            "exact_condition_count": exact_conditions,
            "expected_logic": expected_logic,
            "predicted_logic": actual_logic,
            "logic_correct": expected_logic == actual_logic,
            "contract_complete": (actual or {}).get("contract_complete"),
            "unmapped_obligations": (actual or {}).get("unmapped_obligations", []),
            "decomposition_confidence": (actual or {}).get("decomposition_confidence"),
            "validation_issue_count": len((actual or {}).get("validation_issues", [])),
            "alignment": [{"expected_index": a + 1, "predicted_index": b + 1, "score": round(score, 3)} for a, b, score in pairs],
        })

    matched = len(matched_ids)
    metrics = {
        "requirement_discovery": {
            "expected": len(expected_ids),
            "predicted": len(predicted_flat),
            "matched": matched,
            "recall": pct(matched, len(expected_ids)),
            "precision": pct(matched, len(predicted_flat)),
            "f1": pct(2 * matched, len(expected_ids) + len(predicted_flat)),
            "missed_ids": [item["requirement_id"] for key, item in expected_ids.items() if key not in predicted_by_id],
            "extra_ids": [item.get("req_code") for item in predicted_flat if normalized_id(item.get("req_code")) not in expected_ids],
        },
        "atomic_decomposition": {
            "expected": counters["expected_conditions"],
            "predicted": counters["predicted_conditions"],
            "aligned": counters["aligned_conditions"],
            "recall": pct(counters["aligned_conditions"], counters["expected_conditions"]),
            "precision": pct(counters["aligned_conditions"], counters["predicted_conditions"]),
            "f1": pct(
                2 * counters["aligned_conditions"],
                counters["expected_conditions"] + counters["predicted_conditions"],
            ),
            "under_decomposed_requirements": sum(row["predicted_condition_count"] < row["expected_condition_count"] for row in rows),
            "over_decomposed_requirements": sum(row["predicted_condition_count"] > row["expected_condition_count"] for row in rows),
            "exact_contracts": sum(
                row["exact_condition_count"] == row["expected_condition_count"]
                and row["predicted_condition_count"] == row["expected_condition_count"]
                and row["logic_correct"]
                for row in rows
            ),
            "exact_contracts_note": "Strict canonical-schema agreement; diagnostic only, not semantic correctness.",
        },
        "structural_quality": {
            "source_span_grounding_rate": pct(
                structural["grounded_condition_spans"], structural["conditions"]
            ),
            "source_and_canonical_parameter_rate": pct(
                structural["dual_parameters"], structural["conditions"]
            ),
            "condition_to_clause_link_rate": pct(
                structural["condition_clause_links"], structural["conditions"]
            ),
            "normative_clause_coverage_rate": pct(
                structural["covered_normative_clauses"], structural["normative_clauses"]
            ),
            "complete_logic_tree_rate": pct(
                structural["logic_tree_contracts"], structural["found_contracts"]
            ),
            "validation_pass_rate": pct(
                structural["validation_pass_contracts"], structural["found_contracts"]
            ),
        },
        "field_accuracy": {
            name: pct(field_totals[f"{name}_correct"], field_totals[f"{name}_total"])
            for name in ("description", "parameter", "operator", "value", "unit", "role", "visual")
        },
        "logic_operator_accuracy": pct(sum(row["logic_correct"] for row in rows), len(rows)),
        "contract_complete_rate": pct(sum(row["contract_complete"] is True for row in rows), len(rows)),
        "by_modality": {
            name: {
                "requirements": values["requirements"],
                "requirement_recall": pct(values["found"], values["requirements"]),
                "atomic_recall": pct(values["aligned_conditions"], values["expected_conditions"]),
            }
            for name, values in sorted(modality.items())
        },
    }
    return metrics, rows


def markdown_report(result: dict[str, Any]) -> str:
    ingestion = result["metrics"]["ingestion"]
    extraction = result["metrics"]["extraction"]
    discovery = extraction["requirement_discovery"]
    atomic = extraction["atomic_decomposition"]
    lines = [
        "# TraceAudit Complex Extraction Benchmark",
        "",
        f"- Model: `{result['model']}`",
        f"- Atomic decomposition model: `{result.get('atomic_model', result['model'])}` ({result.get('atomic_thinking_level', 'default')})",
        f"- Atomic fallback model: `{result.get('atomic_fallback_model') or 'disabled'}`",
        f"- Requirement discovery F1: **{discovery['f1']:.2f}%**",
        f"- Semantic atomic-condition precision / recall / F1: **{atomic['precision']:.2f}% / {atomic['recall']:.2f}% / {atomic['f1']:.2f}%**",
        f"- Source-span grounding: **{extraction['structural_quality']['source_span_grounding_rate']:.2f}%**",
        f"- Normative clause coverage: **{extraction['structural_quality']['normative_clause_coverage_rate']:.2f}%**",
        f"- Structural validation pass rate: **{extraction['structural_quality']['validation_pass_rate']:.2f}%**",
        f"- Strict canonical-schema matches (diagnostic only): **{atomic['exact_contracts']}/{discovery['expected']}**",
        f"- Ingestion requirement-ID visibility: **{ingestion['source_visibility']['requirement_id_visibility']:.2f}%**",
        "",
        "## Stage metrics",
        "",
        "| Modality | Source ID visibility | Requirement recall | Atomic recall |",
        "|---|---:|---:|---:|",
    ]
    visibility = ingestion["source_visibility"]["by_modality"]
    for modality, values in extraction["by_modality"].items():
        lines.append(f"| {modality} | {visibility.get(modality, {}).get('id_visibility', 0):.2f}% | {values['requirement_recall']:.2f}% | {values['atomic_recall']:.2f}% |")
    lines += [
        "",
        "## Requirement diagnostics",
        "",
        "| Requirement | Modality | Found | Expected atoms | Predicted atoms | Logic |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in result["requirements"]:
        lines.append(f"| {row['requirement_id']} | {row['source_modality']} | {'yes' if row['found'] else 'no'} | {row['expected_condition_count']} | {row['predicted_condition_count']} | {'yes' if row['logic_correct'] else 'no'} |")
    lines += ["", "This benchmark stops before evidence retrieval and verdict classification. Failures here describe the quality of the input supplied to later audit stages."]
    return "\n".join(lines)


def ingestion_markdown(result: dict[str, Any]) -> str:
    ingestion = result["metrics"]["ingestion"]
    visibility = ingestion["source_visibility"]
    lines = [
        "# TraceAudit Complex Ingestion Benchmark",
        "",
        f"- Requirement-ID visibility: **{visibility['requirement_id_visibility']:.2f}%**",
        f"- Mean requirement-text token coverage: **{visibility['mean_requirement_text_token_coverage']:.2f}%**",
        f"- Tables detected: **{ingestion['totals']['tables_detected']}/{ingestion['totals']['authored_tables']}**",
        f"- Figures detected: **{ingestion['totals']['figures_detected']}/{ingestion['totals']['authored_figures']}**",
        f"- OCR pages / failed pages: **{ingestion['totals']['ocr_pages']} / {ingestion['totals']['ocr_failed_pages']}**",
        "",
        "## Visibility by source modality",
        "",
        "| Modality | Requirements | ID visibility | Mean text coverage |",
        "|---|---:|---:|---:|",
    ]
    for modality, values in visibility["by_modality"].items():
        lines.append(f"| {modality} | {values['requirements']} | {values['id_visibility']:.2f}% | {values['mean_text_token_coverage']:.2f}% |")
    lines += ["", "This report measures parser output before requirement extraction or LLM reasoning."]
    return "\n".join(lines)


async def run(
    model: str,
    thinking_level: str | None,
    only_document: str | None = None,
    *,
    atomic_model: str | None = None,
    atomic_thinking_level: str | None = None,
    atomic_fallback_model: str | None = None,
    ingestion_only: bool = False,
    vision_only: bool = False,
    resume: bool = True,
) -> dict[str, Any]:
    validation = validate()
    if not validation["valid"]:
        raise ValueError("Invalid benchmark: " + "; ".join(validation["errors"]))
    dataset = json.loads((BENCHMARK / "ground_truth.json").read_text(encoding="utf-8"))
    selected_documents = [item for item in dataset["documents"] if not only_document or item["filename"] == only_document]
    if only_document and not selected_documents:
        raise ValueError(f"Unknown document {only_document}")
    selected_names = {item["filename"] for item in selected_documents}
    scoped_dataset = {**dataset, "documents": selected_documents, "requirements": [item for item in dataset["requirements"] if item["document"] in selected_names]}
    active_thinking = thinking_level or settings.GEMINI_THINKING_LEVEL
    active_atomic_model = atomic_model or settings.ATOMIC_DECOMPOSITION_MODEL or model
    active_atomic_thinking = atomic_thinking_level or settings.ATOMIC_DECOMPOSITION_THINKING_LEVEL
    active_atomic_fallback = (
        settings.ATOMIC_DECOMPOSITION_FALLBACK_MODEL
        if atomic_fallback_model is None
        else atomic_fallback_model
    )
    started = time.time()
    print("=" * 82)
    print("TRACEAUDIT COMPLEX EXTRACTION BENCHMARK")
    print(
        f"Discovery model: {model} | Atomic model: {active_atomic_model} "
        f"({active_atomic_thinking}) | Documents: {len(selected_documents)}"
    )
    print("=" * 82)

    parsed, predictions, document_metrics = {}, {}, {}
    print("[1/3] Parsing document structure...")
    for spec in selected_documents:
        parsed_result = parse_document_with_metadata(str(DOCS / spec["filename"]))
        parsed[spec["filename"]] = parsed_result
        document_metrics[spec["filename"]] = {
            "parser_backend": parsed_result.parser_backend,
            "page_count": parsed_result.page_count,
            "chunk_count": len(parsed_result.chunks),
            "tables_detected": parsed_result.diagnostics.get("tables_detected", 0),
            "figures_detected": parsed_result.diagnostics.get("figures_detected", 0),
            "ocr_pages": parsed_result.diagnostics.get("ocr_pages", 0),
            "ocr_failed_pages": parsed_result.diagnostics.get("ocr_failed_pages", 0),
        }
        print(f"  {spec['filename']}: {len(parsed_result.chunks)} chunks, {document_metrics[spec['filename']]['tables_detected']} tables, {document_metrics[spec['filename']]['figures_detected']} figures")

    visibility = source_visibility(scoped_dataset, parsed)
    print(f"  Requirement-ID visibility after ingestion: {visibility['requirement_id_visibility']:.2f}%")
    if ingestion_only:
        totals = {
            "authored_tables": sum(item.get("expected_tables", 0) for item in selected_documents),
            "authored_figures": sum(item.get("expected_figures", 0) for item in selected_documents),
            "tables_detected": sum(item["tables_detected"] for item in document_metrics.values()),
            "figures_detected": sum(item["figures_detected"] for item in document_metrics.values()),
            "ocr_pages": sum(item["ocr_pages"] for item in document_metrics.values()),
            "ocr_failed_pages": sum(item["ocr_failed_pages"] for item in document_metrics.values()),
        }
        result = {
            "benchmark_id": dataset["benchmark_id"],
            "benchmark_version": dataset["version"],
            "stage": "ingestion-only",
            "runtime_seconds": round(time.time() - started, 2),
            "scope": {"documents": sorted(selected_names), "requirements": len(scoped_dataset["requirements"])},
            "validation": validation,
            "metrics": {"ingestion": {"documents": document_metrics, "totals": totals, "source_visibility": visibility}},
        }
        scope_slug = Path(only_document).stem if only_document else "all"
        json_path = RESULTS / f"extraction_{scope_slug}_ingestion-only_results.json"
        report_path = RESULTS / f"extraction_{scope_slug}_ingestion-only_report.md"
        json_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        report_path.write_text(ingestion_markdown(result), encoding="utf-8")
        print("Ingestion benchmark complete")
        print(f"Tables detected: {totals['tables_detected']}/{totals['authored_tables']}")
        print(f"Figures detected: {totals['figures_detected']}/{totals['authored_figures']}")
        print(f"JSON: {json_path}")
        print(f"Report: {report_path}")
        return result

    print("[2/3] Recovering requirements from candidate page images...")
    augmented_text: dict[str, str] = {}
    recovered_by_page: dict[str, dict[int, str]] = defaultdict(dict)
    recovery_diagnostics: dict[str, Any] = {}
    visual_cache = load_requirement_vision_cache(dataset)
    for index, spec in enumerate(selected_documents, 1):
        parsed_result = parsed[spec["filename"]]
        chunk_views = [
            {
                "id": f"{Path(spec['filename']).stem}-chunk-{chunk.chunk_index}",
                "document_id": Path(spec["filename"]).stem,
                "document_name": spec["filename"],
                "page_number": chunk.page_number,
                "content": chunk.content,
                "metadata": dict(chunk.metadata or {}),
            }
            for chunk in parsed_result.chunks
        ]
        for view in chunk_views:
            cached = visual_cache.get(spec["filename"], {}).get(int(view.get("page_number") or 0))
            if cached:
                view["metadata"]["requirement_visual_recovery"] = dict(cached)
        base_text = "\n\n".join(chunk.content for chunk in parsed_result.chunks)
        recovery = await recover_requirement_text_from_pages(
            file_path=str(DOCS / spec["filename"]),
            chunks=chunk_views,
            model=model,
        )
        recovery_diagnostics[spec["filename"]] = {key: value for key, value in recovery.items() if key != "text"}
        for page in recovery.get("pages", []):
            recovered_by_page[spec["filename"]][int(page["page"])] = str(page.get("recovered_text") or "")
        augmented_text[spec["filename"]] = f"{base_text}\n\n{recovery.get('text', '')}".strip()
        print(
            f"  [{index}/{len(selected_documents)}] {spec['filename']}: "
            f"{recovery['recovered_pages']}/{recovery['candidate_pages']} candidate pages recovered "
            f"({recovery['cache_hits']} cache hits)"
        )

    post_vision_visibility = source_visibility(scoped_dataset, parsed, recovered_by_page)
    print(f"  Requirement-ID visibility after visual recovery: {post_vision_visibility['requirement_id_visibility']:.2f}%")
    if vision_only:
        result = {
            "benchmark_id": dataset["benchmark_id"],
            "benchmark_version": dataset["version"],
            "stage": "requirement-vision-only",
            "model": model,
            "runtime_seconds": round(time.time() - started, 2),
            "scope": {"documents": sorted(selected_names), "requirements": len(scoped_dataset["requirements"])},
            "validation": validation,
            "metrics": {
                "ingestion": {"documents": document_metrics, "source_visibility": visibility},
                "requirement_visual_recovery": {
                    "documents": recovery_diagnostics,
                    "source_visibility_after_recovery": post_vision_visibility,
                },
            },
        }
        scope_slug = Path(only_document).stem if only_document else "all"
        json_path = RESULTS / f"extraction_{scope_slug}_requirement-vision_results.json"
        report_path = RESULTS / f"extraction_{scope_slug}_requirement-vision_report.md"
        lines = [
            "# TraceAudit Requirement-Stage Vision Benchmark",
            "",
            f"- ID visibility before vision: **{visibility['requirement_id_visibility']:.2f}%**",
            f"- ID visibility after vision: **{post_vision_visibility['requirement_id_visibility']:.2f}%**",
            f"- Candidate pages: **{sum(item['candidate_pages'] for item in recovery_diagnostics.values())}**",
            f"- Recovered pages: **{sum(item['recovered_pages'] for item in recovery_diagnostics.values())}**",
            "",
            "| Modality | Before | After |",
            "|---|---:|---:|",
        ]
        for modality, values in post_vision_visibility["by_modality"].items():
            before = visibility["by_modality"].get(modality, {}).get("id_visibility", 0)
            lines.append(f"| {modality} | {before:.2f}% | {values['id_visibility']:.2f}% |")
        report_path.write_text("\n".join(lines), encoding="utf-8")
        json_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        print("Requirement-stage vision benchmark complete")
        print(f"JSON: {json_path}")
        print(f"Report: {report_path}")
        return result
    print("[3/3] Extracting requirements and atomic contracts...")
    checkpoint_path = extraction_checkpoint_path(
        model,
        selected_names,
        active_atomic_model,
        active_atomic_thinking,
        active_atomic_fallback,
    )
    if resume:
        restored = load_extraction_checkpoint(
            checkpoint_path,
            dataset,
            model,
            selected_names,
            active_atomic_model,
            active_atomic_thinking,
            active_atomic_fallback,
        )
        predictions.update(restored)
        if restored:
            print(
                f"  [Checkpoint] Restored {len(restored)}/{len(selected_documents)} completed document(s) "
                f"from {checkpoint_path.name}.",
                flush=True,
            )
    else:
        checkpoint_path.unlink(missing_ok=True)
    for index, spec in enumerate(selected_documents, 1):
        if spec["filename"] in predictions:
            print(
                f"  [{index}/{len(selected_documents)}] {spec['filename']}: resumed from checkpoint "
                f"({len(predictions[spec['filename']])} requirements)",
                flush=True,
            )
            continue
        text = augmented_text[spec["filename"]]
        extracted = await extract_requirements_from_text(
            text=text,
            doc_name=spec["filename"],
            model=model,
            thinking_level=active_thinking,
            atomic_model=active_atomic_model,
            atomic_thinking_level=active_atomic_thinking,
            atomic_fallback_model=active_atomic_fallback,
            allow_rule_fallback=False,
            allow_model_fallback=False,
        )
        predictions[spec["filename"]] = [item.model_dump(exclude_none=True) for item in extracted]
        save_extraction_checkpoint(
            checkpoint_path,
            dataset,
            model,
            selected_names,
            predictions,
            active_atomic_model,
            active_atomic_thinking,
            active_atomic_fallback,
        )
        print(f"  [{index}/{len(selected_documents)}] {spec['filename']}: {len(extracted)} requirements")

    extraction, rows = extraction_metrics(scoped_dataset, predictions)
    result = {
        "benchmark_id": dataset["benchmark_id"],
        "benchmark_version": dataset["version"],
        "model": model,
        "thinking_level": active_thinking,
        "atomic_model": active_atomic_model,
        "atomic_thinking_level": active_atomic_thinking,
        "atomic_fallback_model": active_atomic_fallback,
        "runtime_seconds": round(time.time() - started, 2),
        "scope": {"documents": sorted(selected_names), "requirements": len(scoped_dataset["requirements"])},
        "validation": validation,
        "metrics": {
            "ingestion": {"documents": document_metrics, "source_visibility": visibility},
            "requirement_visual_recovery": {
                "documents": recovery_diagnostics,
                "source_visibility_after_recovery": post_vision_visibility,
            },
            "extraction": extraction,
        },
        "predictions": predictions,
        "requirements": rows,
    }
    slug = re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")
    atomic_slug = re.sub(r"[^a-z0-9]+", "-", active_atomic_model.lower()).strip("-")
    if atomic_slug != re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-"):
        slug = f"{slug}_atomic-{atomic_slug}-{active_atomic_thinking.lower()}"
    scope_slug = Path(only_document).stem if only_document else "all"
    json_path = RESULTS / f"extraction_{scope_slug}_{slug}_results.json"
    report_path = RESULTS / f"extraction_{scope_slug}_{slug}_report.md"
    json_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    report_path.write_text(markdown_report(result), encoding="utf-8")
    checkpoint_path.unlink(missing_ok=True)
    print("Benchmark complete")
    print(f"Requirement discovery F1: {extraction['requirement_discovery']['f1']:.2f}%")
    print(
        "Semantic atomic precision / recall / F1: "
        f"{extraction['atomic_decomposition']['precision']:.2f}% / "
        f"{extraction['atomic_decomposition']['recall']:.2f}% / "
        f"{extraction['atomic_decomposition']['f1']:.2f}%"
    )
    print(f"Source-span grounding: {extraction['structural_quality']['source_span_grounding_rate']:.2f}%")
    print(f"Normative clause coverage: {extraction['structural_quality']['normative_clause_coverage_rate']:.2f}%")
    print(
        "Strict canonical-schema matches (diagnostic only): "
        f"{extraction['atomic_decomposition']['exact_contracts']}/{len(scoped_dataset['requirements'])}"
    )
    print(f"JSON: {json_path}")
    print(f"Report: {report_path}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=None, help="Model identifier (defaults to DEFAULT_MODEL or LLM_MODEL)")
    parser.add_argument("--provider", default=None, help="LLM provider: tokenrouter, databricks, gemini, groq, openai")
    parser.add_argument("--base-url", default=None, help="Custom base URL for OpenAI/TokenRouter endpoint")
    parser.add_argument("--api-key", default=None, help="Custom API key for provider")
    parser.add_argument("--thinking-level", default=None)
    parser.add_argument(
        "--atomic-model",
        default=None,
        help="Model for semantic clause planning and atomic decomposition (defaults to ATOMIC_DECOMPOSITION_MODEL)",
    )
    parser.add_argument(
        "--atomic-thinking-level",
        default=None,
        help="Thinking level for atomic decomposition (defaults to ATOMIC_DECOMPOSITION_THINKING_LEVEL)",
    )
    parser.add_argument(
        "--atomic-fallback-model",
        default=None,
        help="Optional stage-local fallback model for planning/atomic construction",
    )
    parser.add_argument("--document", default=None, help="Run one exact PDF filename for a faster diagnostic")
    parser.add_argument("--ingestion-only", action="store_true", help="Score parser visibility without making any LLM calls")
    parser.add_argument("--vision-only", action="store_true", help="Measure targeted requirement-page visual recovery without contract extraction")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore any compatible incomplete-run checkpoint and start extraction from the first document",
    )
    args = parser.parse_args()
    if args.validate_only:
        result = validate()
        print(json.dumps(result, indent=2))
        return 0 if result["valid"] else 1
    if args.ingestion_only and args.vision_only:
        parser.error("--ingestion-only and --vision-only are mutually exclusive")

    # Dynamic CLI overrides
    if args.provider:
        settings.LLM_PROVIDER = args.provider.strip().lower()
        import os
        os.environ["LLM_PROVIDER"] = settings.LLM_PROVIDER

    if args.base_url:
        import os
        target_provider = (args.provider or settings.LLM_PROVIDER or "").strip().lower()
        if target_provider == "tokenrouter":
            settings.TOKENROUTER_BASE_URL = args.base_url
            os.environ["TOKENROUTER_BASE_URL"] = args.base_url
        else:
            settings.OPENAI_BASE_URL = args.base_url
            os.environ["OPENAI_BASE_URL"] = args.base_url

    if args.api_key:
        import os
        target_provider = (args.provider or settings.LLM_PROVIDER or "").strip().lower()
        if target_provider == "tokenrouter":
            settings.TOKENROUTER_API_KEY = args.api_key
            os.environ["TOKENROUTER_API_KEY"] = args.api_key
        elif target_provider == "databricks":
            settings.DATABRICKS_TOKEN = args.api_key
            os.environ["DATABRICKS_TOKEN"] = args.api_key
        elif target_provider == "groq":
            settings.GROQ_API_KEY = args.api_key
            os.environ["GROQ_API_KEY"] = args.api_key
        elif target_provider == "gemini":
            settings.GEMINI_API_KEY = args.api_key
            os.environ["GEMINI_API_KEY"] = args.api_key
        else:
            settings.OPENAI_API_KEY = args.api_key
            os.environ["OPENAI_API_KEY"] = args.api_key

    model = args.model or settings.LLM_MODEL or DEFAULT_MODEL
    settings.LLM_MODEL = model

    if not args.provider:
        from app.services.llm_client import resolve_llm_provider
        inferred = resolve_llm_provider(model)
        settings.LLM_PROVIDER = inferred

    print(f">> Extraction Benchmark: Provider='{settings.LLM_PROVIDER}', Model='{model}'")

    asyncio.run(
        run(
            model,
            args.thinking_level,
            args.document,
            atomic_model=args.atomic_model,
            atomic_thinking_level=args.atomic_thinking_level,
            atomic_fallback_model=args.atomic_fallback_model,
            ingestion_only=args.ingestion_only,
            vision_only=args.vision_only,
            resume=not args.no_resume,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
