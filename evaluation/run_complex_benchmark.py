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
from app.config import settings

# Benchmark 5 Status Classes
BENCHMARK_CLASSES = ["SUPPORTED", "PARTIAL", "CONFLICT", "MISSING", "UNKNOWN"]
SRS_DOC_NAME = "01_System_Requirements_Specification_SRS.docx"


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
    if normalized_mode == "oracle":
        return [
            {
                "req_code": item["requirement_id"],
                "title": item["title"],
                "description": item["requirement_text"],
                "category": item.get("category", "General"),
                "severity": item.get("severity", "Medium"),
                "conditions": list(item.get("conditions", [])),
            }
            for item in ground_truth_reqs
        ]
    if normalized_mode != "end-to-end":
        raise ValueError("mode must be 'end-to-end' or 'oracle'")

    return [
        {
            "req_code": item.req_code,
            "title": item.title,
            "description": item.description or item.title,
            "category": item.category,
            "severity": item.severity,
            "conditions": [condition.model_dump(exclude_none=True) for condition in item.conditions],
        }
        for item in extracted_reqs
    ]


def _predicted_condition_status(
    expected_condition: dict[str, Any],
    predicted_results: list[Any],
) -> str:
    """Align evaluator-only condition labels without altering pipeline inputs."""
    expected_id = expected_condition.get("condition_id", "")
    direct = next(
        (result for result in predicted_results if result.condition_id == expected_id),
        None,
    )
    if direct is not None:
        return direct.status

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
                    for value in (result.description, result.reason)
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
            return best.status
    return "UNTESTED"


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
    for field in ("threshold", "min_value", "max_value"):
        expected_value = expected.get(field)
        if expected_value is not None and _normalized_contract_value(expected_value) != _normalized_contract_value(actual.get(field)):
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


async def run_benchmark(mode: str = "end-to-end"):
    mode = mode.strip().lower()
    if mode not in {"end-to-end", "oracle"}:
        raise ValueError("mode must be 'end-to-end' or 'oracle'")
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

    print(f"  • Ground Truth Requirements : {len(ground_truth_reqs)}")
    print(f"  • Extracted Requirements    : {len(extracted_reqs)}")
    print(f"  • Extraction Precision      : {ext_p:.2f}%")
    print(f"  • Extraction Recall         : {ext_r:.2f}%")
    print(f"  • Extraction F1-Score       : {ext_f1:.2f}%")
    print(f"  • Requirement ID Recall     : {id_tp_extract / len(ground_truth_reqs) * 100.0:.2f}%")
    print(f"  • Atomic Condition Recall   : {ext_condition_recall:.2f}%")
    print(f"  • Exact Contract Recall     : {exact_condition_recall:.2f}%")

    pipeline_requirements = _build_pipeline_requirements(
        mode,
        extracted_reqs,
        ground_truth_reqs,
    )
    pipeline_by_id = {item["req_code"]: item for item in pipeline_requirements}
    print(
        f"  • Downstream Requirement Source: "
        f"{'EXTRACTED CONTRACTS' if mode == 'end-to-end' else 'GROUND-TRUTH ORACLE CONTRACTS'}"
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
        retrieved = await retrieve_candidate_evidence_hybrid(
            requirement_text=query_text,
            chunks=all_chunks,
            chunk_embeddings=chunk_embeddings,
            top_k=5,
            exclude_doc_names={SRS_DOC_NAME},
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
    req_items = [
        {
            **source_req,
            "candidate_chunks": retrieved_by_req.get(source_req["req_code"], []),
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

        predictions[req_id] = {
            "expected": expected_status,
            "predicted": actual_status,
            "confidence": assessment.confidence if assessment else 0.0,
            "reason": assessment.ai_analysis if assessment else "None",
            "condition_results": [
                result.model_dump() for result in (assessment.condition_results if assessment else [])
            ],
        }

        matrix[expected_status][actual_status] += 1

        extraction_missing = mode == "end-to-end" and req_id not in pipeline_by_id
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

            if extraction_missing:
                cat = "EXTRACTION_FAILURE"
            elif retrieval_missed:
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
                "failure_category": cat,
                "reason": assessment.ai_analysis if assessment else "No analysis produced",
                "retrieved_evidence": [c["content"][:100] for c in retrieved_chunks[:2]],
                "ground_truth_note": gt_link.get("notes", ""),
            })

        # Atomic Condition Evaluation
        conds = r.get("conditions", [])
        missing_cond_refs = gt_link.get("missing_conditions", [])

        predicted_condition_results = list(assessment.condition_results if assessment else [])

        for cond in conds:
            total_atomic_conditions += 1
            cid = cond.get("condition_id", "")
            
            # Determine ground truth expectation for this atomic condition
            if expected_status == "SUPPORTED":
                gt_cond_status = "PROVEN"
            elif expected_status == "MISSING":
                gt_cond_status = "UNTESTED"
            elif expected_status == "UNKNOWN":
                gt_cond_status = "INCONCLUSIVE"
            elif expected_status == "CONFLICT":
                gt_cond_status = "FAILED"
            elif expected_status == "PARTIAL":
                is_missing = any(cid in mc or (cond.get("parameter") and cond["parameter"] in mc) for mc in missing_cond_refs)
                gt_cond_status = "PENDING" if is_missing else "PROVEN"
            else:
                gt_cond_status = "UNTESTED"

            # Score the verifier's actual per-condition output. Missing condition
            # IDs are conservatively UNTESTED; no ground-truth status is used to
            # manufacture a prediction.
            pred_cond_status = _predicted_condition_status(cond, predicted_condition_results)

            if pred_cond_status == gt_cond_status:
                correct_atomic_conditions += 1

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

            if cond.get("operator") in ("<=", "<", ">=", ">", "between", "==") and (
                isinstance(cond.get("threshold"), (int, float))
                or (isinstance(cond.get("threshold"), str) and any(ch.isdigit() for ch in cond["threshold"]))
            ):
                numerical_total += 1
                if pred_cond_status == gt_cond_status:
                    numerical_correct += 1

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
    unsupported_rate = (unsupported_claims / 20.0 * 100.0)  # 20 MISSING cases
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
    print(f"  • Condition-Level Accuracy : {cond_acc:.2f}% (P: {cond_prec:.2f}%, R: {cond_rec:.2f}%, F1: {cond_f1:.2f}%)")
    print(f"  • Numerical / Range Accuracy: {num_acc:.2f}%")
    print(f"  • Total Benchmark Runtime  : {elapsed_time}s")

    # Output JSON results
    benchmark_results = {
        "benchmark_name": "TraceAudit AI 100-Requirement Complex Automotive Benchmark",
        "evaluation_mode": mode,
        "downstream_requirement_source": (
            "extracted_contracts" if mode == "end-to-end" else "ground_truth_oracle_contracts"
        ),
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
                if mode == "end-to-end"
                else "oracle: classification correctness using ground-truth contracts"
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
        },
        "specialty_metrics": {
            "conflict_f1": per_class_metrics["CONFLICT"]["f1"],
            "missing_f1": per_class_metrics["MISSING"]["f1"],
            "partial_f1": per_class_metrics["PARTIAL"]["f1"],
            "unknown_f1": per_class_metrics["UNKNOWN"]["f1"],
            "unsupported_claim_rate": round(unsupported_rate, 2),
            "condition_accuracy": round(cond_acc, 2),
            "condition_f1": round(cond_f1, 2),
            "numerical_range_accuracy": round(num_acc, 2),
        },
        "failures_count": len(failures),
        "failures": failures,
        "predictions": predictions,
        "retrieval_trace": retrieved_by_req,
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
    cm_data = results.get("condition_metrics", {})
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
  • Exact Extracted Contract Recall: {em.get('atomic_condition_exact_recall', 0):.2f}%
  • Document Retrieval Recall@3: {rm['document_recall_at_3']:.2f}% | Recall@5: {rm['document_recall_at_5']:.2f}% (MRR: {rm['document_mrr']:.4f})
  • Document-qualified Passage Recall@3: {rm['passage_recall_at_3']:.2f}% | Recall@5: {rm['passage_recall_at_5']:.2f}% (MRR: {rm['passage_mrr']:.4f})
  • 5-Class Requirement Macro F1: {vm['macro_f1']:.2f}% (Accuracy: {vm['accuracy']:.2f}%)
  • Atomic Condition Accuracy  : {cm_data.get('condition_accuracy', sm.get('condition_accuracy', 0)):.2f}% (F1: {cm_data.get('condition_f1', 0):.2f}%)
  • Unsupported Claim Rate    : {sm['unsupported_claim_rate']:.2f}% (Evidence-grounded)
```

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
| **Atomic Condition Accuracy** | **{cm_data.get('condition_accuracy', 0):.2f}%** | >= 90.0% | {'✅ Met' if cm_data.get('condition_accuracy', 0) >= 90 else '⚠️ Review Needed'} |
| **Atomic Condition F1** | **{cm_data.get('condition_f1', 0):.2f}%** | >= 85.0% | {'✅ Met' if cm_data.get('condition_f1', 0) >= 85 else '⚠️ Review Needed'} |
| **Numerical & Range Accuracy** | **{sm['numerical_range_accuracy']:.2f}%** | >= 90.0% | {'✅ Met' if sm['numerical_range_accuracy'] >= 90 else '⚠️ Review Needed'} |
| **Unsupported Claim Rate** | **{sm['unsupported_claim_rate']:.2f}%** | <= 5.0% | {'✅ Safe' if sm['unsupported_claim_rate'] <= 5 else '❌ High Risk'} |

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
    if rm['document_recall_at_5'] < 90.0:
        content += "- **Retrieval & Evidence Ranking** is the primary bottleneck. Evidence chunks for complex cross-system requirements were missed in the top-5 candidate pool.\n"
    elif vm['macro_f1'] < 90.0:
        content += "- **Multi-Condition Reasoner & Scope Discrimination** is the primary bottleneck. Retrieval succeeded in finding candidate chunks, but multi-condition boundaries or component scope limits were misclassified.\n"
    else:
        content += "- **Pipeline is well-balanced** with high fidelity across both semantic retrieval and hybrid verification stages.\n"

    content += """
### 🚀 Recommended Next Improvements:
1. **Adaptive Chunk Reranking:** Integrate cross-encoder reranker for dense technical terms (e.g. distinguishing battery coolant temperature vs ASIC junction temperature).
2. **Atomic Condition Decomposition:** Pass extracted condition trees directly into the LLM verification reasoner prompt to evaluate each sub-condition as a formal boolean clause.
3. **Document Authority Layer:** Explicitly tag supplier datasheets vs system validation reports in evidence prompts to enforce scope hierarchy rules.
"""

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[OK] Generated detailed Markdown report at {md_path.name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the TraceAudit complex benchmark")
    parser.add_argument(
        "--mode",
        choices=("end-to-end", "oracle"),
        default="end-to-end",
        help=(
            "end-to-end feeds extracted contracts into retrieval/verification; "
            "oracle isolates retrieval/verifier behavior with benchmark contracts"
        ),
    )
    args = parser.parse_args()
    asyncio.run(run_benchmark(mode=args.mode))
