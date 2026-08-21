"""TraceAudit Benchmark Runner.

Executes the real, production TraceAudit pipeline against the synthetic benchmark
dataset and computes end-to-end quantitative metrics against ground truth.

Usage:
  python -m evaluation.run_evaluation
  python evaluation/run_evaluation.py
  python evaluation/run_evaluation.py --model gemini-3.7-flash --thinking-level HIGH
"""

import sys
import os
import json
import argparse
import asyncio
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

# Ensure repo root and backend/ are in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Set stdout encoding to UTF-8 for Windows console support
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Import real TraceAudit backend services
from app.services.ingestion import parse_document
from app.services.extraction import extract_requirements_from_text
from app.services.retrieval import retrieve_candidate_evidence
from app.services.classification import assess_requirement_coverage
from app.config import settings

# Import evaluation modules
from evaluation.generate_dataset import generate_all, DOCS_DIR, GT_DIR
from evaluation.metrics import (
    evaluate_extraction,
    evaluate_retrieval,
    evaluate_verification,
    evaluate_specialty_metrics,
    classify_failure,
    normalize_code,
)
from evaluation.report import save_evaluation_results


def load_ground_truth() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Load ground truth requirements, evidence links, and expected findings from JSON."""
    if not (GT_DIR / "requirements.json").exists():
        print("Ground truth not found. Generating synthetic dataset now...")
        generate_all()

    with open(GT_DIR / "requirements.json", "r", encoding="utf-8") as f:
        reqs = json.load(f)
    with open(GT_DIR / "evidence_links.json", "r", encoding="utf-8") as f:
        links = json.load(f)
    with open(GT_DIR / "expected_findings.json", "r", encoding="utf-8") as f:
        findings = json.load(f)

    return reqs, links, findings


async def run_benchmark(
    model: str = "gemini-3.7-flash",
    thinking_level: str = "HIGH",
    regenerate_data: bool = False,
) -> dict[str, Any]:
    """Run full end-to-end evaluation pipeline against the real TraceAudit engine."""
    print("=" * 70)
    print("           TRACEAUDIT AI - PIPELINE BENCHMARK EVALUATION")
    print("=" * 70)

    if regenerate_data or not list(DOCS_DIR.glob("*")):
        print(">> Generating synthetic technical documents and ground truth...")
        generate_all()

    gt_reqs, gt_links, gt_findings = load_ground_truth()
    print(f">> Loaded Ground Truth: {len(gt_reqs)} requirements, {len(gt_links)} evidence links.")

    # ──────────────────────────────────────────────────────────────────────────
    # Stage 1: Document Ingestion (Real PyMuPDF, python-docx, openpyxl parser)
    # ──────────────────────────────────────────────────────────────────────────
    print("\n[1/4] Ingesting & Parsing Synthetic Benchmark Documents...")
    doc_files = sorted(list(DOCS_DIR.iterdir()))
    all_chunks: list[dict[str, Any]] = []
    chunk_uuid_counter = 1

    spec_doc_text = ""
    spec_doc_name = ""

    for doc_path in doc_files:
        if not doc_path.is_file():
            continue
        try:
            parsed = parse_document(str(doc_path))
            for pc in parsed:
                chunk_id = f"chunk-{chunk_uuid_counter:04d}"
                chunk_uuid_counter += 1
                chunk_dict = {
                    "id": chunk_id,
                    "chunk_id": chunk_id,
                    "document_name": doc_path.name,
                    "doc_type": "Specification" if "SRS" in doc_path.name or "Spec" in doc_path.name else "Test Report",
                    "page_number": pc.page_number,
                    "content": pc.content,
                }
                all_chunks.append(chunk_dict)

                if "SRS" in doc_path.name or "Product_Requirements" in doc_path.name:
                    spec_doc_text += f"\n{pc.content}"
                    spec_doc_name = doc_path.name

            print(f"  [OK] Parsed {doc_path.name} -> {len(parsed)} chunks")
        except Exception as ex:
            print(f"  [ERROR] Error parsing {doc_path.name}: {ex}")

    print(f">> Total chunks indexed across {len(doc_files)} documents: {len(all_chunks)}")

    # ──────────────────────────────────────────────────────────────────────────
    # Stage 2: Requirement Extraction (Gemini Structured Extraction / Rule Engine)
    # ──────────────────────────────────────────────────────────────────────────
    print("\n[2/4] Running Requirement Extraction on Specification...")
    extracted_reqs = await extract_requirements_from_text(
        text=spec_doc_text,
        doc_name=spec_doc_name or "01_Product_Requirements_SRS.docx",
        model=model,
        thinking_level=thinking_level,
    )
    print(f">> Extracted {len(extracted_reqs)} requirements from specification.")

    # Evaluate extraction metrics
    extraction_metrics = evaluate_extraction(gt_reqs, extracted_reqs)
    print(f"  Extraction Precision: {extraction_metrics.precision}% | Recall: {extraction_metrics.recall}% | F1: {extraction_metrics.f1}%")

    # ──────────────────────────────────────────────────────────────────────────
    # Stage 3: Evidence Retrieval & Candidate Selection (Real BM25 + Boost Engine)
    # ──────────────────────────────────────────────────────────────────────────
    print("\n[3/4] Running Evidence Candidate Retrieval for each requirement...")
    retrieved_by_req: dict[str, list[dict[str, Any]]] = {}

    # We evaluate against all ground-truth requirements to ensure reproducible coverage testing
    for r in gt_reqs:
        req_code = normalize_code(r.get("req_code") or r.get("requirement_id"))
        query = f"{r.get('title', '')} {r.get('description', '')}"

        candidate_chunks = retrieve_candidate_evidence(
            requirement_text=query,
            chunks=all_chunks,
            top_k=7,
            min_score=0.2,
        )

        retrieved_by_req[req_code] = [
            {
                "chunk_id": c.chunk_id,
                "document_name": c.document_name,
                "page_number": c.page_number,
                "content": c.content,
                "score": c.score,
            }
            for c in candidate_chunks
        ]

    retrieval_metrics = evaluate_retrieval(gt_links, retrieved_by_req)
    print(f"  Recall@1: {retrieval_metrics.recall_at_1}% | Recall@3: {retrieval_metrics.recall_at_3}% | Recall@5: {retrieval_metrics.recall_at_5}% | MRR: {retrieval_metrics.mean_reciprocal_rank}")

    # ──────────────────────────────────────────────────────────────────────────
    # Stage 4: Verification, Contradiction Detection & Coverage Classification
    # ──────────────────────────────────────────────────────────────────────────
    print("\n[4/4] Running Verification & Contradiction Detection Engine...")
    actual_predictions: dict[str, str] = {}
    detailed_assessments: dict[str, Any] = {}

    for r in gt_reqs:
        req_code = normalize_code(r.get("req_code") or r.get("requirement_id"))
        candidates = retrieved_by_req.get(req_code, [])

        assessment = assess_requirement_coverage(
            req_code=req_code,
            title=r.get("title", ""),
            description=r.get("description", ""),
            category=r.get("category", "General"),
            candidate_chunks=candidates,
        )

        actual_predictions[req_code] = assessment.coverage_status
        detailed_assessments[req_code] = {
            "coverage_status": assessment.coverage_status,
            "confidence": assessment.confidence,
            "review_state": assessment.review_state,
            "ai_analysis": assessment.ai_analysis,
            "ai_recommendation": assessment.ai_recommendation,
            "evidence_links_count": len(assessment.evidence_links),
        }

    # Evaluate classification, confusion matrix, and specialty metrics
    verification_metrics = evaluate_verification(gt_reqs, actual_predictions)
    specialty_metrics = evaluate_specialty_metrics(gt_reqs, actual_predictions)

    print(f"  Verification Accuracy: {verification_metrics.accuracy}% | Macro F1: {verification_metrics.macro_f1}%")
    print(f"  Conflict Detection F1: {specialty_metrics.conflict_f1}% | Missing Detection F1: {specialty_metrics.missing_f1}%")
    print(f"  Unsupported Claim Rate: {specialty_metrics.unsupported_claim_rate}%")

    # ──────────────────────────────────────────────────────────────────────────
    # Failure Analysis
    # ──────────────────────────────────────────────────────────────────────────
    failures: list[dict[str, Any]] = []
    # Map ground truth sources for lookup
    gt_sources_map = {}
    for link in gt_links:
        code = normalize_code(link.get("req_code") or link.get("requirement_id"))
        gt_sources_map.setdefault(code, []).append(link)

    for r in gt_reqs:
        req_code = normalize_code(r.get("req_code") or r.get("requirement_id"))
        actual_status = actual_predictions.get(req_code, "Missing")
        retrieved_list = retrieved_by_req.get(req_code, [])
        exp_sources = gt_sources_map.get(req_code, [])

        failure = classify_failure(
            gt_req=r,
            actual_status=actual_status,
            retrieved_chunks=retrieved_list,
            expected_evidence=exp_sources,
        )
        if failure:
            failures.append({
                "requirement_id": failure.requirement_id,
                "requirement_title": failure.requirement_title,
                "category": failure.category,
                "expected_status": failure.expected_status,
                "actual_status": failure.actual_status,
                "error_type": failure.error_type,
                "explanation": failure.explanation,
                "expected_evidence": failure.expected_evidence,
                "retrieved_evidence": [
                    {"document": c.get("document_name"), "page": c.get("page_number"), "content_preview": c.get("content", "")[:120]}
                    for c in failure.retrieved_evidence[:2]
                ],
            })

    # Assemble structured results dictionary
    benchmark_data = {
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "thinking_level": thinking_level,
            "dataset_name": "Automotive Battery Control Unit (BCU-800V)",
            "traceaudit_version": "0.1.0",
        },
        "dataset": {
            "requirements_count": len(gt_reqs),
            "documents_count": len(doc_files),
            "total_chunks_indexed": len(all_chunks),
            "supported_count": sum(1 for r in gt_reqs if r.get("expected_status") == "Supported"),
            "partial_count": sum(1 for r in gt_reqs if r.get("expected_status") == "Partial"),
            "missing_count": sum(1 for r in gt_reqs if r.get("expected_status") == "Missing"),
            "conflict_count": sum(1 for r in gt_reqs if r.get("expected_status") == "Conflict"),
        },
        "extraction": {
            "total_ground_truth": extraction_metrics.total_ground_truth,
            "total_extracted": extraction_metrics.total_extracted,
            "precision": extraction_metrics.precision,
            "recall": extraction_metrics.recall,
            "f1": extraction_metrics.f1,
            "matched_count": len(extraction_metrics.matched_codes),
            "unmatched_count": len(extraction_metrics.unmatched_ground_truth),
        },
        "retrieval": {
            "total_queries": retrieval_metrics.total_queries,
            "recall_at_1": retrieval_metrics.recall_at_1,
            "recall_at_3": retrieval_metrics.recall_at_3,
            "recall_at_5": retrieval_metrics.recall_at_5,
            "mean_reciprocal_rank": retrieval_metrics.mean_reciprocal_rank,
        },
        "verification": {
            "total_evaluated": verification_metrics.total_evaluated,
            "accuracy": verification_metrics.accuracy,
            "macro_precision": verification_metrics.macro_precision,
            "macro_recall": verification_metrics.macro_recall,
            "macro_f1": verification_metrics.macro_f1,
            "weighted_f1": verification_metrics.weighted_f1,
            "confusion_matrix": verification_metrics.confusion_matrix,
            "per_class": {
                k: {
                    "precision": v.precision,
                    "recall": v.recall,
                    "f1": v.f1,
                    "support": v.support,
                    "true_positives": v.true_positives,
                    "false_positives": v.false_positives,
                    "false_negatives": v.false_negatives,
                }
                for k, v in verification_metrics.per_class.items()
            },
        },
        "specialty": {
            "conflict_precision": specialty_metrics.conflict_precision,
            "conflict_recall": specialty_metrics.conflict_recall,
            "conflict_f1": specialty_metrics.conflict_f1,
            "missing_precision": specialty_metrics.missing_precision,
            "missing_recall": specialty_metrics.missing_recall,
            "missing_f1": specialty_metrics.missing_f1,
            "unsupported_claim_rate": specialty_metrics.unsupported_claim_rate,
            "total_missing_in_gt": specialty_metrics.total_missing_in_gt,
            "unsupported_claims_count": specialty_metrics.unsupported_claims_count,
        },
        "failures": failures,
        "per_requirement_results": [
            {
                "requirement_id": r.get("requirement_id"),
                "req_code": r.get("req_code"),
                "title": r.get("title"),
                "expected_status": r.get("expected_status"),
                "actual_status": actual_predictions.get(normalize_code(r.get("req_code"))),
                "is_correct": r.get("expected_status") == actual_predictions.get(normalize_code(r.get("req_code"))),
                "assessment": detailed_assessments.get(normalize_code(r.get("req_code"))),
            }
            for r in gt_reqs
        ],
    }

    # Save to disk
    save_evaluation_results(benchmark_data)

    print("\n" + "=" * 70)
    print("                    BENCHMARK EXECUTION SUMMARY")
    print("=" * 70)
    print(f"  Requirements Evaluated:     {len(gt_reqs)}")
    print(f"  Extraction F1:              {extraction_metrics.f1}%")
    print(f"  Retrieval Recall@3:         {retrieval_metrics.recall_at_3}%")
    print(f"  Verification Accuracy:      {verification_metrics.accuracy}%")
    print(f"  Macro F1 Score:             {verification_metrics.macro_f1}%")
    print(f"  Conflict Detection F1:      {specialty_metrics.conflict_f1}%")
    print(f"  Missing Evidence F1:        {specialty_metrics.missing_f1}%")
    print(f"  Unsupported Claim Rate:     {specialty_metrics.unsupported_claim_rate}% (Hallucinations)")
    print(f"  Discrepancies / Failures:   {len(failures)}")
    print("=" * 70)

    return benchmark_data


def main():
    parser = argparse.ArgumentParser(description="TraceAudit AI Pipeline Benchmark Runner")
    parser.add_argument("--model", type=str, default="gemini-3.7-flash", help="LLM model identifier")
    parser.add_argument("--thinking-level", type=str, default="HIGH", help="Gemini thinking level (HIGH, MEDIUM, LOW)")
    parser.add_argument("--regenerate", action="store_true", help="Force regenerate synthetic documents and ground truth")

    args = parser.parse_args()

    asyncio.run(run_benchmark(
        model=args.model,
        thinking_level=args.thinking_level,
        regenerate_data=args.regenerate,
    ))


if __name__ == "__main__":
    main()
