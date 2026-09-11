"""Run the 48-requirement Nova benchmark through the complete TraceAudit pipeline."""

from __future__ import annotations

import argparse
import copy
import hashlib
from datetime import datetime, timezone
import asyncio
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable


REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "backend"
BENCHMARK = REPO / "evaluation" / "extraction_benchmark"
DOCS = BENCHMARK / "documents"
RESULTS = REPO / "evaluation" / "results"
RESULTS.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BENCHMARK))

from app.config import settings
from app.services.classification import batch_assess_requirements
from app.services.document_classifier import profile_documents
from app.services.extraction import ExtractedRequirement
from app.services.retrieval import precompute_chunk_embeddings, retrieve_candidate_evidence_hybrid
from app.services.visual_analysis import describe_figure_candidates
from evaluation.run_extraction_benchmark import run as run_extraction_stage
from evaluation.atomic_evaluation import POLICY, SCORING_VERSION, decomposition, verification_metrics
from evaluation.run_fmvss305_benchmark import (
    FINAL_CLASSES,
    _condition_query,
    _field,
    _ingest_document,
    _macro_f1,
    _normal_final,
    _oracle_contracts,
    _percent,
    _quote_coverage,
)
from validate_end_to_end_benchmark import validate


MODES = {
    "oracle-contracts-evidence": (True, True),
    "oracle-contracts": (True, False),
    "end-to-end": (False, False),
}
DEFAULT_MODEL = "system.ai.llama-4-maverick"


def load_dataset() -> dict[str, Any]:
    return json.loads((BENCHMARK / "ground_truth.json").read_text(encoding="utf-8"))


def load_completed_extraction_result(
    dataset: dict[str, Any],
    model: str,
    atomic_model: str,
) -> dict[str, Any] | None:
    """Reuse a fully completed extraction stage after a downstream failure."""
    slug = re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")
    path = RESULTS / f"extraction_all_{slug}_results.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    expected_documents = {item["filename"] for item in dataset["documents"]}
    predictions = payload.get("predictions") or {}
    compatible = (
        payload.get("benchmark_id") == dataset.get("benchmark_id")
        and payload.get("benchmark_version") == dataset.get("version")
        and payload.get("model") == model
        and payload.get("atomic_model") == atomic_model
        and set(predictions) == expected_documents
    )
    return payload if compatible else None


def prediction_contracts(predictions: list[ExtractedRequirement]) -> list[dict[str, Any]]:
    """Build inference inputs from predictions alone; never consult the answer key."""
    codes = [item.req_code for item in predictions]
    if len(codes) != len(set(codes)):
        raise ValueError("Duplicate extracted requirement IDs would overwrite verification results")
    return [{
        "req_code": item.req_code,
        "title": item.title,
        "description": item.description or item.title,
        "category": item.category,
        "conditions": [condition.model_dump(exclude_none=True) for condition in item.conditions],
        "clause_coverage": [clause.model_dump(exclude_none=True) for clause in item.clause_coverage],
        "unmapped_obligations": list(item.unmapped_obligations),
        "contract_complete": item.contract_complete,
        "logic": item.logic.model_dump(),
    } for item in predictions]


def all_documents(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    return [*dataset["documents"], *dataset["evidence_documents"]]


def oracle_evidence(requirement: dict[str, Any], chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    index_by_id = {chunk["id"]: index for index, chunk in enumerate(chunks)}

    def add(chunk: dict[str, Any]) -> None:
        if chunk["id"] not in selected_ids:
            selected_ids.add(chunk["id"])
            selected.append(dict(chunk, score=1.0))

    for condition in requirement.get("conditions", []):
        for annotation in condition.get("evidence", []):
            candidates = [
                chunk for chunk in chunks
                if chunk.get("document_name") == annotation.get("document")
                and int(chunk.get("page_number") or 0) == int(annotation.get("page") or 0)
            ]
            if not candidates:
                continue
            expected_block_type = str(annotation.get("block_type") or "").lower()
            best = max(
                candidates,
                key=lambda item: (
                    str((item.get("metadata") or {}).get("block_type") or "").lower() == expected_block_type,
                    _quote_coverage(annotation.get("quote", ""), item.get("content", "")),
                ),
            )
            add(best)
            block_type = str((best.get("metadata") or {}).get("block_type") or "").lower()
            if block_type in {"table", "figure", "formula", "checkbox"}:
                index = index_by_id[best["id"]]
                for neighbor_index in (index - 1, index + 1):
                    if 0 <= neighbor_index < len(chunks):
                        neighbor = chunks[neighbor_index]
                        if neighbor.get("document_name") == best.get("document_name") and neighbor.get("page_number") == best.get("page_number"):
                            add(neighbor)
    return selected


def expected_evidence_keys(requirement: dict[str, Any]) -> set[tuple[str, int]]:
    return {
        (annotation["document"], int(annotation["page"]))
        for condition in requirement.get("conditions", [])
        for annotation in condition.get("evidence", [])
    }


def retrieval_metrics(requirements: list[dict[str, Any]], retrieved: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    hits = {1: 0, 3: 0, 5: 0}
    full = {1: 0, 3: 0, 5: 0}
    any_hits = {1: 0, 3: 0, 5: 0}
    expected_total = 0
    reciprocal: list[float] = []
    details: dict[str, Any] = {}
    for requirement in requirements:
        expected = expected_evidence_keys(requirement)
        ranked = [
            (item.get("document_name"), int(item.get("page_number") or 0))
            for item in retrieved.get(requirement["requirement_id"], [])
        ]
        expected_total += len(expected)
        first = next((rank for rank, key in enumerate(ranked[:5], 1) if key in expected), None)
        reciprocal.append(1 / first if first else 0.0)
        for k in hits:
            found = expected & set(ranked[:k])
            hits[k] += len(found)
            any_hits[k] += int(bool(found))
            full[k] += int(expected.issubset(set(ranked[:k])))
        details[requirement["requirement_id"]] = {
            "expected_document_pages": sorted(f"{doc}:p{page}" for doc, page in expected),
            "retrieved_document_pages": [f"{doc}:p{page}" for doc, page in ranked],
            "first_hit_rank": first,
        }
    total = len(requirements)
    return {
        "expected_document_pages": expected_total,
        **{f"recall_at_{k}": _percent(hits[k], expected_total) for k in hits},
        **{f"requirement_any_hit_at_{k}": _percent(any_hits[k], total) for k in any_hits},
        **{f"requirement_full_coverage_at_{k}": _percent(full[k], total) for k in full},
        "mrr": round(sum(reciprocal) / len(reciprocal), 4) if reciprocal else 0.0,
        "details": details,
    }


def document_profile_metrics(dataset: dict[str, Any], profiles: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for document in all_documents(dataset):
        profile = profiles.get(Path(document["filename"]).stem)
        role = str(_field(profile, "primary_role", ""))
        expected_role = document.get("expected_role")
        basis = str(_field(profile, "verification_basis", ""))
        expected_basis = document.get("expected_verification_basis")
        rows.append({
            "document": document["filename"],
            "expected_role": expected_role,
            "predicted_role": role,
            "role_correct": role == expected_role,
            "expected_verification_basis": expected_basis,
            "predicted_verification_basis": basis,
            "basis_correct": expected_basis is None or basis == expected_basis,
            "confidence": _field(profile, "confidence", 0.0),
        })
    return {
        "role_accuracy": _percent(sum(row["role_correct"] for row in rows), len(rows)),
        "verification_basis_accuracy": _percent(sum(row["basis_correct"] for row in rows), len(rows)),
        "documents": rows,
    }


def markdown_report(result: dict[str, Any]) -> str:
    metrics = result["metrics"]
    lines = [
        "# TraceAudit Nova End-to-End Benchmark",
        "",
        f"- Mode: `{result['mode']}`",
        f"- Discovery/profile model: `{result['model']}`",
        f"- Atomic decomposition model: `{result['atomic_model']}`",
        f"- Verification model: `{result.get('verification_model', result['model'])}`",
        f"- Requirements: **{metrics['final_verdict']['total']}**",
        f"- Final verdict accuracy: **{metrics['final_verdict']['accuracy']:.2f}%**",
        f"- Combined extraction + atomic status score: **{metrics['final_atomic']['accuracy']:.2f}%**",
        f"- Atomic status accuracy on eligible atoms: **{metrics['final_atomic']['aligned_accuracy']}%** (coverage **{metrics['final_atomic']['alignment_coverage']:.2f}%**)",
        f"- Structured atom precision / recall / F1: **{metrics['structured_decomposition']['precision']:.2f}% / {metrics['structured_decomposition']['recall']:.2f}% / {metrics['structured_decomposition']['f1']:.2f}%**",
        f"- Structured matches needing review: **{metrics['structured_decomposition']['ambiguous_pairs']} ambiguous; {metrics['structured_decomposition']['unmatched_expected']} reference atoms unmatched**",
        f"- Logic equivalence: **{metrics['structured_decomposition']['logic']['accuracy']:.2f}%** (full truth-table check after identity alignment)",
        f"- Retrieval Recall@3: **{metrics['retrieval']['recall_at_3']:.2f}%**",
        f"- Document role accuracy: **{metrics['document_profile']['role_accuracy']:.2f}%**",
        f"- Unsafe false auto-closes: **{metrics['review_gate']['unsafe_false_auto_closes']}**",
        "",
        "## Requirement outcomes",
        "",
        "| Requirement | Logic | Expected | Predicted | Review | Correct |",
        "|---|---|---|---|---|---|",
    ]
    for row in result["requirements"]:
        lines.append(
            f"| {row['requirement_id']} | {row['logic_operator']} | {row['expected_status']} | "
            f"{row['predicted_status']} | {row['predicted_review_state']} | {'yes' if row['correct'] else 'no'} |"
        )
    lines += [
        "",
        "## Evaluation limitations",
        "",
        "Oracle modes supply reference contracts; oracle-contracts-evidence also selects reference evidence. "
        "Their atomic status score does not measure extraction accuracy. This synthetic development corpus "
        "has been used for tuning and is not a held-out generalization test. End-to-end atomic alignment "
        "uses status-blind structured matching with exact constraint checks and limited unit normalization. "
        "Identity matching remains a lexical proxy requiring expert validation. Oracle decomposition scores "
        "reflect supplied reference contracts, not model extraction. Unmatched items are unresolved, not automatically hallucinations. "
        "An interval remains one atom for status scoring; expanded constraint coverage is reported separately in JSON.",
        f"Unexpected extracted IDs: {result.get('metrics', {}).get('integrity', {}).get('unexpected_extracted_ids', [])}",
        "",
        "## Mode interpretation",
        "",
        "`oracle-contracts-evidence` isolates profiling, multimodal evidence understanding, reasoning, aggregation, and review safety. "
        "`oracle-contracts` adds real retrieval. `end-to-end` additionally discovers and decomposes requirements from all eight requirement PDFs.",
    ]
    return "\n".join(lines)


async def run(
    mode: str,
    model: str,
    thinking_level: str | None,
    atomic_model: str,
    atomic_thinking_level: str | None,
    atomic_fallback_model: str,
    verification_model: str,
    verification_thinking_level: str | None,
    batch_size: int,
    resume: bool,
) -> dict[str, Any]:
    if mode not in MODES:
        raise ValueError(f"Unknown mode: {mode}")
    validation = validate()
    if not validation["valid"]:
        raise ValueError("Invalid benchmark: " + "; ".join(validation["errors"]))
    use_oracle_contracts, use_oracle_evidence = MODES[mode]
    dataset = load_dataset()
    truth = dataset["requirements"]
    truth_by_id = {item["requirement_id"]: item for item in truth}
    started = time.time()
    print("=" * 86)
    print("TRACEAUDIT NOVA 48-REQUIREMENT END-TO-END BENCHMARK")
    print(
        f"Mode: {mode} | Discovery/profile model: {model} | "
        f"Atomic model: {atomic_model} | Verification model: {verification_model}"
    )
    print("=" * 86)

    print("[1/6] Ingesting 8 requirement PDFs and 4 evidence PDFs...")
    chunks_by_name: dict[str, list[dict[str, Any]]] = {}
    all_chunks: list[dict[str, Any]] = []
    for spec in all_documents(dataset):
        chunks = _ingest_document(DOCS / spec["filename"], spec["doc_type"])
        chunks_by_name[spec["filename"]] = chunks
        all_chunks.extend(chunks)
        pages = len({item.get("page_number") for item in chunks if item.get("page_number")})
        print(f"  {spec['filename']}: {len(chunks)} chunks across {pages} pages")

    print("[2/6] Profiling document authority and verification basis...")
    specs = all_documents(dataset)
    profiles = await profile_documents(
        documents=[SimpleNamespace(id=Path(item["filename"]).stem, original_filename=item["filename"]) for item in specs],
        all_chunks=all_chunks,
        model=model,
        thinking_level=thinking_level or settings.GEMINI_THINKING_LEVEL,
    )
    for spec in specs:
        profile_data = profiles[Path(spec["filename"]).stem].model_dump()
        for chunk in chunks_by_name[spec["filename"]]:
            chunk["document_profile"] = profile_data
    profile_score = document_profile_metrics(dataset, profiles)
    print(f"  Document role accuracy: {profile_score['role_accuracy']:.2f}%")

    print("[3/6] Preparing requirement contracts...")
    if use_oracle_contracts:
        contracts, extraction_score = _oracle_contracts(truth)
    else:
        extraction_result = load_completed_extraction_result(dataset, model, atomic_model) if resume else None
        if extraction_result:
            print("  Reusing the compatible completed extraction result.")
        else:
            extraction_result = await run_extraction_stage(
                model,
                thinking_level,
                atomic_model=atomic_model,
                atomic_thinking_level=atomic_thinking_level,
                atomic_fallback_model=atomic_fallback_model,
                resume=resume,
            )
        predictions = [
            ExtractedRequirement.model_validate(item)
            for document_rows in extraction_result.get("predictions", {}).values()
            for item in document_rows
        ]
        contracts = prediction_contracts(predictions)
        extraction_score = {
            **extraction_result["metrics"]["extraction"],
            "requirement_visual_recovery": extraction_result["metrics"].get("requirement_visual_recovery"),
        }
    print(f"  Contracts queued for verification: {len(contracts)}/{len(truth)}")

    evaluated_contracts = copy.deepcopy(contracts)

    print("[4/6] Retrieving evidence passages...")
    evidence_names = {item["filename"] for item in dataset["evidence_documents"]}
    evidence_chunks = [chunk for name in evidence_names for chunk in chunks_by_name[name]]
    embeddings = await precompute_chunk_embeddings(evidence_chunks)
    retrieved_by_id: dict[str, list[dict[str, Any]]] = {}
    for index, item in enumerate(contracts, 1):
        retrieved = await retrieve_candidate_evidence_hybrid(
            requirement_text=f"{item['title']} {item['description']}",
            chunks=evidence_chunks,
            chunk_embeddings=embeddings,
            top_k=5,
            min_score=0.20,
            condition_queries=[_condition_query(item["req_code"], condition) for condition in item.get("conditions", [])],
        )
        retrieved_by_id[item["req_code"]] = [{
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
        if index % 8 == 0 or index == len(contracts):
            print(f"  Retrieved evidence for {index}/{len(contracts)} requirements")
    retrieval_score = retrieval_metrics(truth, retrieved_by_id)
    print(f"  Evidence page Recall@3: {retrieval_score['recall_at_3']:.2f}%")

    print("[5/6] Interpreting visual evidence and verifying atomic conditions...")
    req_items = []
    for item in contracts:
        candidates = (
            oracle_evidence(truth_by_id[item["req_code"]], evidence_chunks)
            if use_oracle_evidence
            else retrieved_by_id.get(item["req_code"], [])
        )
        req_items.append({**item, "candidate_chunks": candidates})
    visual = await describe_figure_candidates(
        req_items,
        {Path(name).stem: str(DOCS / name) for name in evidence_names},
        model=model,
    )
    assessments = await batch_assess_requirements(
        req_items=req_items,
        model=verification_model,
        thinking_level=(
            verification_thinking_level
            or thinking_level
            or settings.GEMINI_THINKING_LEVEL
        ),
        batch_size=batch_size,
        spec_doc_names={item["filename"] for item in dataset["documents"]},
    )

    print("[6/6] Scoring extraction, retrieval, reasoning, aggregation, and review safety...")
    matrix = {expected: {predicted: 0 for predicted in [*FINAL_CLASSES, "NOT_EXTRACTED"]} for expected in FINAL_CLASSES}
    final_conditions: dict[str, Iterable[Any]] = {}
    raw_conditions: dict[str, Iterable[Any]] = {}
    rows = []
    false_supported = unsafe = review_correct = 0
    for expected in truth:
        req_id = expected["requirement_id"]
        assessment = assessments.get(req_id)
        predicted = _normal_final(_field(assessment, "coverage_status")) if assessment else "NOT_EXTRACTED"
        matrix[expected["expected_status"]][predicted] += 1
        review = _field(assessment, "review_state", "Needs review") if assessment else "Needs review"
        review_match = str(review).lower() == expected["expected_review_state"].lower()
        review_correct += int(review_match)
        is_false_supported = predicted == "SUPPORTED" and expected["expected_status"] != "SUPPORTED"
        false_supported += int(is_false_supported)
        unsafe += int(is_false_supported and review == "Reviewed")
        diagnostics = _field(assessment, "pipeline_diagnostics", {}) or {}
        raw = diagnostics.get("llm_condition_results", [])
        final = list(_field(assessment, "condition_results", []) or [])
        raw_conditions[req_id] = raw
        final_conditions[req_id] = final
        rows.append({
            "requirement_id": req_id,
            "logic_operator": expected["logic"]["operator"],
            "expected_status": expected["expected_status"],
            "predicted_status": predicted,
            "correct": assessment is not None and predicted == expected["expected_status"],
            "prediction_missing": assessment is None,
            "expected_review_state": expected["expected_review_state"],
            "predicted_review_state": review,
            "review_correct": review_match,
            "confidence": _field(assessment, "confidence", 0.0) if assessment else 0.0,
            "ai_analysis": _field(assessment, "ai_analysis", "") if assessment else "Requirement was not extracted",
            "retrieved_document_pages": [
                f"{item.get('document_name')}:p{item.get('page_number')}"
                for item in retrieved_by_id.get(req_id, [])
            ],
            "raw_llm_condition_results": raw,
            "final_condition_results": [item.model_dump() if hasattr(item, "model_dump") else item for item in final],
            "pipeline_diagnostics": diagnostics,
        })

    correct = sum(row["correct"] for row in rows)
    final_metrics = {
        "accuracy": _percent(correct, len(truth)),
        "macro_f1": _macro_f1(matrix),
        "correct": correct,
        "total": len(truth),
        "confusion_matrix": matrix,
        "expected_distribution": dict(Counter(item["expected_status"] for item in truth)),
        "predicted_distribution": dict(Counter(item["predicted_status"] for item in rows)),
    }
    atomic_alignment = decomposition(truth, evaluated_contracts)
    metrics = {
        "structured_decomposition": atomic_alignment,
        "integrity": {
            "scoring_version": SCORING_VERSION,
            "scoring_policy": POLICY,
            "scorer_sha256": hashlib.sha256((REPO / "evaluation" / "atomic_evaluation.py").read_bytes()).hexdigest(),
            "ground_truth_sha256": hashlib.sha256((BENCHMARK / "ground_truth.json").read_bytes()).hexdigest(),
            "gold_contracts_supplied": use_oracle_contracts,
            "gold_evidence_selection": use_oracle_evidence,
            "atomic_alignment": "status_blind_structured_contracts",
            "atomic_metric_limitation": POLICY["limitations"],
            "evaluation_scope": "Synthetic development corpus, not an independent held-out evaluation",
            "unexpected_extracted_ids": sorted({item["req_code"] for item in contracts} - set(truth_by_id)),
            "missing_extracted_ids": sorted(set(truth_by_id) - {item["req_code"] for item in contracts}),
        },
        "corpus": validation["counts"],
        "document_profile": profile_score,
        "extraction": extraction_score,
        "retrieval": retrieval_score,
        "visual_evidence": visual,
        "raw_llm_atomic": verification_metrics(truth, raw_conditions, atomic_alignment),
        "final_atomic": verification_metrics(truth, final_conditions, atomic_alignment),
        "final_verdict": final_metrics,
        "review_gate": {
            "review_state_accuracy": _percent(review_correct, len(truth)),
            "false_supported": false_supported,
            "unsafe_false_auto_closes": unsafe,
        },
    }
    result = {
        "run_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ"),
        "evaluated_contracts": evaluated_contracts,
        "benchmark_id": dataset["benchmark_id"],
        "benchmark_version": dataset["version"],
        "mode": mode,
        "model": model,
        "atomic_model": atomic_model,
        "verification_model": verification_model,
        "atomic_thinking_level": atomic_thinking_level,
        "verification_thinking_level": verification_thinking_level,
        "thinking_level": thinking_level,
        "runtime_seconds": round(time.time() - started, 2),
        "validation": validation,
        "documents": {"requirements": dataset["documents"], "evidence": dataset["evidence_documents"]},
        "document_profiles": {
            spec["filename"]: profiles[Path(spec["filename"]).stem].model_dump() for spec in specs
        },
        "metrics": metrics,
        "requirements": rows,
    }
    model_slug = re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")
    atomic_slug = re.sub(r"[^a-z0-9]+", "-", atomic_model.lower()).strip("-")
    verification_slug = re.sub(r"[^a-z0-9]+", "-", verification_model.lower()).strip("-")
    slug = f"{model_slug}_atomic-{atomic_slug}_verify-{verification_slug}"
    json_path = RESULTS / f"nova_{mode}_{slug}_results.json"
    report_path = RESULTS / f"nova_{mode}_{slug}_report.md"
    archive = RESULTS / "runs" / result["run_id"]
    archive.mkdir(parents=True, exist_ok=False)
    serialized = json.dumps(result, indent=2, default=str)
    (archive / "results.json").write_text(serialized, encoding="utf-8")
    (archive / "report.md").write_text(markdown_report(result), encoding="utf-8")
    json_path.write_text(serialized, encoding="utf-8")
    report_path.write_text(markdown_report(result), encoding="utf-8")
    print("Benchmark complete")
    print(f"Final verdict accuracy: {final_metrics['accuracy']:.2f}% ({correct}/{len(truth)})")
    print(f"Structured atom precision/recall/F1: {atomic_alignment['precision']:.2f}% / {atomic_alignment['recall']:.2f}% / {atomic_alignment['f1']:.2f}%")
    print(f"Combined extraction + atomic status score: {metrics['final_atomic']['accuracy']:.2f}%")
    print(f"Atomic status accuracy on eligible atoms: {metrics['final_atomic']['aligned_accuracy']}% (coverage {metrics['final_atomic']['alignment_coverage']:.2f}%)")
    print(f"Immutable run: {archive}")
    print(f"Retrieval Recall@3: {retrieval_score['recall_at_3']:.2f}%")
    print(f"Unsafe false auto-closes: {unsafe}")
    print(f"JSON: {json_path}")
    print(f"Report: {report_path}")
    return result


def configure_provider(provider: str | None, model: str) -> None:
    if provider:
        settings.LLM_PROVIDER = provider.strip().lower()
        os.environ["LLM_PROVIDER"] = settings.LLM_PROVIDER
    else:
        from app.services.llm_client import resolve_llm_provider
        settings.LLM_PROVIDER = resolve_llm_provider(model)
    settings.LLM_MODEL = model
    os.environ["LLM_MODEL"] = model


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=sorted(MODES), default="end-to-end")
    parser.add_argument("--provider", default="databricks")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--thinking-level", default=None)
    parser.add_argument("--atomic-model", default=None, help="Defaults to the primary model")
    parser.add_argument("--atomic-thinking-level", default=None)
    parser.add_argument("--atomic-fallback-model", default="", help="Empty disables stage-local model fallback")
    parser.add_argument("--verification-model", default=None, help="Defaults to the primary model")
    parser.add_argument("--verification-thinking-level", default=None)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.validate_only:
        result = validate()
        print(json.dumps(result, indent=2))
        return 0 if result["valid"] else 1
    atomic_model = args.atomic_model or args.model
    verification_model = args.verification_model or args.model
    configure_provider(args.provider, args.model)
    dashscope_models = [
        candidate
        for candidate in (atomic_model, verification_model)
        if candidate.strip().lower().startswith("qwen3.8-")
    ]
    if dashscope_models and not settings.effective_dashscope_api_key:
        raise SystemExit(
            "DASHSCOPE_API_KEY is required for "
            + ", ".join(dict.fromkeys(dashscope_models))
            + ". Bare qwen3.8-* model IDs are Alibaba DashScope-only and will not "
              "be sent to Databricks."
        )
    print(
        f">> Nova Benchmark: Provider='{settings.LLM_PROVIDER}', Model='{args.model}', "
        f"Atomic='{atomic_model}', Verification='{verification_model}'"
    )
    asyncio.run(run(
        args.mode,
        args.model,
        args.thinking_level,
        atomic_model,
        args.atomic_thinking_level,
        args.atomic_fallback_model,
        verification_model,
        args.verification_thinking_level,
        args.batch_size,
        not args.no_resume,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
