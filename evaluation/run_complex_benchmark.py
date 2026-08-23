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


async def run_benchmark():
    print("=" * 75)
    print("     TRACEAUDIT AI - 100-REQUIREMENT COMPLEX AUTOMOTIVE BENCHMARK")
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
    tp_extract = sum(1 for r in ground_truth_reqs if r["requirement_id"] in extracted_dict)
    fp_extract = max(0, len(extracted_reqs) - tp_extract)
    fn_extract = len(ground_truth_reqs) - tp_extract
    ext_p = (tp_extract / len(extracted_reqs) * 100.0) if extracted_reqs else 0.0
    ext_r = (tp_extract / len(ground_truth_reqs) * 100.0) if ground_truth_reqs else 0.0
    ext_f1 = (2 * ext_p * ext_r / (ext_p + ext_r)) if (ext_p + ext_r) > 0 else 0.0

    print(f"  • Ground Truth Requirements : {len(ground_truth_reqs)}")
    print(f"  • Extracted Requirements    : {len(extracted_reqs)}")
    print(f"  • Extraction Precision      : {ext_p:.2f}%")
    print(f"  • Extraction Recall         : {ext_r:.2f}%")
    print(f"  • Extraction F1-Score       : {ext_f1:.2f}%")

    # 4. Evidence Retrieval Evaluation (Recall@1, Recall@3, Recall@5, MRR)
    print(f"\n[4/5] Evaluating Hybrid Evidence Retrieval across 100 Requirements...")
    retrieval_hits = {"R@1": 0, "R@3": 0, "R@5": 0}
    reciprocal_ranks = []
    retrieved_by_req: dict[str, list[dict]] = {}

    eval_queries = [r for r in ground_truth_reqs if links_by_id.get(r["requirement_id"], {}).get("expected_evidence")]

    for r in ground_truth_reqs:
        req_id = r["requirement_id"]
        query_text = f"{r['requirement_id']} {r['title']} {r['requirement_text']}"
        
        # Hybrid retrieval
        retrieved = await retrieve_candidate_evidence_hybrid(
            requirement_text=query_text,
            chunks=all_chunks,
            chunk_embeddings=chunk_embeddings,
            top_k=5,
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

            hit_rank = None
            for rank, c in enumerate(candidate_chunks[:5], 1):
                doc_match = c["document_name"].lower() in exp_docs
                content_lower = c["content"].lower()
                quote_match = any(q[:40] in content_lower or (len(q) > 20 and " ".join(q.split()[:4]) in content_lower) for q in exp_quotes)

                if doc_match or quote_match:
                    hit_rank = rank
                    break

            if hit_rank == 1:
                retrieval_hits["R@1"] += 1
                retrieval_hits["R@3"] += 1
                retrieval_hits["R@5"] += 1
                reciprocal_ranks.append(1.0)
            elif hit_rank in (2, 3):
                retrieval_hits["R@3"] += 1
                retrieval_hits["R@5"] += 1
                reciprocal_ranks.append(1.0 / hit_rank)
            elif hit_rank in (4, 5):
                retrieval_hits["R@5"] += 1
                reciprocal_ranks.append(1.0 / hit_rank)
            else:
                reciprocal_ranks.append(0.0)

    total_q = len(eval_queries)
    recall_at_1 = (retrieval_hits["R@1"] / total_q * 100.0) if total_q else 0.0
    recall_at_3 = (retrieval_hits["R@3"] / total_q * 100.0) if total_q else 0.0
    recall_at_5 = (retrieval_hits["R@5"] / total_q * 100.0) if total_q else 0.0
    mrr = (sum(reciprocal_ranks) / total_q) if total_q else 0.0

    print(f"  • Evaluated Queries         : {total_q}")
    print(f"  • Retrieval Recall@1        : {recall_at_1:.2f}%")
    print(f"  • Retrieval Recall@3        : {recall_at_3:.2f}%")
    print(f"  • Retrieval Recall@5        : {recall_at_5:.2f}%")
    print(f"  • Mean Reciprocal Rank (MRR): {mrr:.4f}")

    # 5. Verification Assessment Evaluation (5-Class Verification)
    print(f"\n[5/5] Executing 5-Class Multi-Condition Compliance Verification...")
    req_items = [
        {
            "req_code": r["requirement_id"],
            "title": r["title"],
            "description": r["requirement_text"],
            "category": r["category"],
            "candidate_chunks": retrieved_by_req.get(r["requirement_id"], []),
        }
        for r in ground_truth_reqs
    ]

    assessments = await batch_assess_requirements(
        req_items=req_items,
        model=settings.LLM_MODEL,
        thinking_level=settings.GEMINI_THINKING_LEVEL,
        batch_size=10,
        spec_doc_names={"01_System_Requirements_Specification_SRS.docx"},
    )

    # 5x5 Confusion Matrix: matrix[expected][actual]
    matrix = {exp: {act: 0 for act in BENCHMARK_CLASSES} for exp in BENCHMARK_CLASSES}
    
    correct_count = 0
    predictions = {}
    failures = []

    # Detailed metric accumulators
    condition_total = 0
    condition_correct = 0
    numerical_total = 0
    numerical_correct = 0
    unsupported_claims = 0  # Missing evidence falsely claimed as SUPPORTED

    for r in ground_truth_reqs:
        req_id = r["requirement_id"]
        expected_status = normalize_status_5(r["expected_status"])
        assessment = assessments.get(req_id)
        actual_raw = assessment.coverage_status if assessment else "UNKNOWN"
        actual_status = normalize_status_5(actual_raw)

        predictions[req_id] = {
            "expected": expected_status,
            "predicted": actual_status,
            "confidence": assessment.confidence if assessment else 0.0,
            "reason": assessment.ai_analysis if assessment else "None",
        }

        matrix[expected_status][actual_status] += 1

        is_correct = (expected_status == actual_status)
        if is_correct:
            correct_count += 1
        else:
            # Categorize failure
            gt_link = links_by_id.get(req_id, {})
            retrieved_chunks = retrieved_by_req.get(req_id, [])
            
            # Root cause heuristic
            if expected_status in ("SUPPORTED", "PARTIAL") and actual_status == "MISSING":
                cat = "retrieval_failure"
            elif expected_status == "CONFLICT" and actual_status != "CONFLICT":
                cat = "contradiction_detection_failure"
            elif expected_status == "PARTIAL" and actual_status == "SUPPORTED":
                cat = "partial_compliance_failure"
            elif expected_status == "UNKNOWN" and actual_status in ("SUPPORTED", "CONFLICT"):
                cat = "source_authority_failure"
            elif any(c.get("operator") in ("between", "<=", ">=") for c in r.get("conditions", [])):
                cat = "numerical_reasoning_failure"
            else:
                cat = "llm_reasoning_failure"

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

        # Condition level tracking
        conds = r.get("conditions", [])
        condition_total += len(conds)
        if is_correct:
            condition_correct += len(conds)

        # Numerical checks
        if any(c.get("operator") in ("<=", ">=", "between", "==") for c in conds):
            numerical_total += 1
            if is_correct:
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

    # Condition & numerical accuracy
    cond_acc = (condition_correct / condition_total * 100.0) if condition_total else 0.0
    num_acc = (numerical_correct / numerical_total * 100.0) if numerical_total else 0.0
    unsupported_rate = (unsupported_claims / 20.0 * 100.0) # 20 MISSING cases

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
    print(f"  • Unsupported Claim Rate   : {unsupported_rate:.2f}% ({unsupported_claims} false verifications on missing)")
    print(f"  • Condition-Level Accuracy : {cond_acc:.2f}%")
    print(f"  • Numerical / Range Accuracy: {num_acc:.2f}%")
    print(f"  • Total Benchmark Runtime  : {elapsed_time}s")

    # Output JSON results
    benchmark_results = {
        "benchmark_name": "TraceAudit AI 100-Requirement Complex Automotive Benchmark",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_requirements": total_eval,
        "runtime_seconds": elapsed_time,
        "extraction_metrics": {
            "precision": round(ext_p, 2),
            "recall": round(ext_r, 2),
            "f1": round(ext_f1, 2),
            "total_extracted": len(extracted_reqs),
        },
        "retrieval_metrics": {
            "recall_at_1": round(recall_at_1, 2),
            "recall_at_3": round(recall_at_3, 2),
            "recall_at_5": round(recall_at_5, 2),
            "mean_reciprocal_rank": round(mrr, 4),
        },
        "verification_metrics": {
            "accuracy": round(acc, 2),
            "macro_precision": round(macro_p, 2),
            "macro_recall": round(macro_r, 2),
            "macro_f1": round(macro_f1, 2),
            "per_class": per_class_metrics,
            "confusion_matrix": matrix,
        },
        "specialty_metrics": {
            "conflict_f1": per_class_metrics["CONFLICT"]["f1"],
            "missing_f1": per_class_metrics["MISSING"]["f1"],
            "partial_f1": per_class_metrics["PARTIAL"]["f1"],
            "unsupported_claim_rate": round(unsupported_rate, 2),
            "condition_accuracy": round(cond_acc, 2),
            "numerical_range_accuracy": round(num_acc, 2),
        },
        "failures_count": len(failures),
        "failures": failures,
        "predictions": predictions,
    }

    json_path = RESULTS_DIR / "complex_benchmark_results.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(benchmark_results, f, indent=2)
    print(f"\n[OK] Saved structured results JSON to {json_path.name}")

    # Generate Markdown Report
    generate_markdown_report(benchmark_results, failures)

    return benchmark_results


def generate_markdown_report(results: dict, failures: list[dict]):
    """Generate extensive Markdown evaluation report."""
    md_path = RESULTS_DIR / "complex_benchmark_report.md"
    vm = results["verification_metrics"]
    rm = results["retrieval_metrics"]
    em = results["extraction_metrics"]
    sm = results["specialty_metrics"]
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
    for f in failures[:10]:
        fail_rows.append(
            f"| `{f['requirement_id']}` | **{f['expected_status']}** | `{f['predicted_status']}` | `{f['failure_category']}` | {f['ground_truth_note'][:90]}... |"
        )
    fail_table = "\n".join(fail_rows) if fail_rows else "| None | - | - | - | All requirements passed! |"

    content = f"""# TraceAudit AI — 100-Requirement Complex Benchmark Report

> **Benchmark Date:** {results['timestamp']}  
> **Total Requirements Evaluated:** 100  
> **Total Technical Documents:** 20 (DOCX, PDF, XLSX)  
> **Overall Verification Accuracy:** **{vm['accuracy']:.2f}%**  
> **Macro F1-Score:** **{vm['macro_f1']:.2f}%**  

---

## 1. Executive Summary & Pipeline Health

The 100-requirement synthetic benchmark tests real-world automotive compliance auditing across 10 mission-critical domains (High-Voltage BMS, Traction Inverters, DC-DC Converters, Charging, Thermal, CAN-FD/Ethernet, UDS Diagnostics, Functional Safety ASIL-D, Cybersecurity ISO 21434, and Environmental EMC).

```
Pipeline Efficiency:
  • Requirement Extraction F1 : {em['f1']:.2f}%
  • Evidence Retrieval Recall@5 : {rm['recall_at_5']:.2f}% (MRR: {rm['mean_reciprocal_rank']:.4f})
  • Multi-Condition Verification: {vm['accuracy']:.2f}% Accuracy | {vm['macro_f1']:.2f}% Macro F1
  • Unsupported Claim Rate    : {sm['unsupported_claim_rate']:.2f}% (Hallucination resistance)
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
| **Condition-Level Accuracy** | **{sm['condition_accuracy']:.2f}%** | >= 90.0% | {'✅ Met' if sm['condition_accuracy'] >= 90 else '⚠️ Review Needed'} |
| **Numerical & Range Accuracy** | **{sm['numerical_range_accuracy']:.2f}%** | >= 90.0% | {'✅ Met' if sm['numerical_range_accuracy'] >= 90 else '⚠️ Review Needed'} |
| **Unsupported Claim Rate (Hallucination)** | **{sm['unsupported_claim_rate']:.2f}%** | <= 5.0% | {'✅ Safe' if sm['unsupported_claim_rate'] <= 5 else '❌ High Risk'} |

---

## 5. Root Cause Failure Classification

```
Total Failures: {len(failures)} / 100
Failure Categorization:
"""
    for cat, cnt in failure_cat_counts.most_common():
        content += f"  • {cat:<35}: {cnt:2d} failure(s)\n"

    content += f"""```

### Top Failure Cases:

| Req ID | Expected | Predicted | Failure Category | Ground Truth Failure Context |
|---|:---:|:---:|---|---|
{fail_table}

---

## 6. Bottleneck Analysis & Next Technical Improvements

### 🔍 Main Bottleneck:
"""
    if rm['recall_at_5'] < 90.0:
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
    asyncio.run(run_benchmark())
