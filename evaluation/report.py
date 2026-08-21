"""Report generator for TraceAudit benchmarks.

Exports:
  - evaluation/results/latest_results.json (Full machine-readable benchmark results)
  - evaluation/results/latest_report.md (Human-readable Markdown report with tables & failure analysis)
"""

import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Any


RESULTS_DIR = Path(__file__).resolve().parent / "results"


def generate_markdown_report(data: dict[str, Any]) -> str:
    """Format structured evaluation output into clean, informative Markdown."""
    meta = data.get("metadata", {})
    ds = data.get("dataset", {})
    ext = data.get("extraction", {})
    ret = data.get("retrieval", {})
    ver = data.get("verification", {})
    spec = data.get("specialty", {})
    failures = data.get("failures", [])
    cm = ver.get("confusion_matrix", {})

    lines = []
    lines.append("# TRACEAUDIT BENCHMARK REPORT")
    lines.append("")
    lines.append(f"**Execution Timestamp:** {meta.get('timestamp', datetime.now(timezone.utc).isoformat())}  ")
    lines.append(f"**Model / Engine Evaluated:** `{meta.get('model', 'TraceAudit Deterministic + Gemini Hybrid')}`  ")
    lines.append(f"**Benchmark Dataset:** `{meta.get('dataset_name', 'Automotive Battery Control Unit (BCU-800V)')}`  ")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 1. Dataset Overview
    lines.append("## 1. Dataset Overview")
    lines.append("")
    lines.append("| Metric | Count |")
    lines.append("|---|---|")
    lines.append(f"| Ground Truth Requirements | **{ds.get('requirements_count', 0)}** |")
    lines.append(f"| Synthetic Technical Documents | **{ds.get('documents_count', 0)}** |")
    lines.append(f"| Indexed Evidence Chunks | **{ds.get('total_chunks_indexed', 0)}** |")
    lines.append(f"| Verified (Supported) Cases in GT | **{ds.get('supported_count', 0)}** |")
    lines.append(f"| Partial Coverage Cases in GT | **{ds.get('partial_count', 0)}** |")
    lines.append(f"| Missing Evidence Cases in GT | **{ds.get('missing_count', 0)}** |")
    lines.append(f"| Contradiction Cases in GT | **{ds.get('conflict_count', 0)}** |")
    lines.append("")

    # 2. Executive Summary
    lines.append("## 2. Executive Metrics Summary")
    lines.append("")
    lines.append("| Evaluation Dimension | Primary Metric | Score | Target Status |")
    lines.append("|---|---|---|---|")
    lines.append(f"| **Requirement Extraction** | F1 Score | **{ext.get('f1', 0.0)}%** | {'🟢 PASS' if ext.get('f1', 0) >= 85 else '🟡 REVIEW'} |")
    lines.append(f"| **Evidence Retrieval** | Recall@3 | **{ret.get('recall_at_3', 0.0)}%** | {'🟢 PASS' if ret.get('recall_at_3', 0) >= 80 else '🟡 REVIEW'} |")
    lines.append(f"| **Evidence Retrieval** | Mean Reciprocal Rank (MRR) | **{ret.get('mean_reciprocal_rank', 0.0)}** | {'🟢 PASS' if ret.get('mean_reciprocal_rank', 0) >= 0.70 else '🟡 REVIEW'} |")
    lines.append(f"| **Verification Classification** | Accuracy | **{ver.get('accuracy', 0.0)}%** | {'🟢 PASS' if ver.get('accuracy', 0) >= 80 else '🟡 REVIEW'} |")
    lines.append(f"| **Verification Classification** | Macro F1 | **{ver.get('macro_f1', 0.0)}%** | {'🟢 PASS' if ver.get('macro_f1', 0) >= 80 else '🟡 REVIEW'} |")
    lines.append(f"| **Contradiction Detection** | Conflict F1 | **{spec.get('conflict_f1', 0.0)}%** | {'🟢 PASS' if spec.get('conflict_f1', 0) >= 75 else '🟡 REVIEW'} |")
    lines.append(f"| **Missing Evidence Detection** | Missing F1 | **{spec.get('missing_f1', 0.0)}%** | {'🟢 PASS' if spec.get('missing_f1', 0) >= 80 else '🟡 REVIEW'} |")
    lines.append(f"| **Unsupported Claim Rate (Hallucination)** | Rate on Missing Evidence | **{spec.get('unsupported_claim_rate', 0.0)}%** | {'🟢 ZERO' if spec.get('unsupported_claim_rate', 0) == 0 else '🔴 FAIL'} |")
    lines.append("")

    # 3. Stage-by-Stage Deep Dive
    lines.append("## 3. Stage-by-Stage Performance")
    lines.append("")
    lines.append("### 3.1 Requirement Extraction")
    lines.append(f"- **Precision:** {ext.get('precision', 0.0)}%")
    lines.append(f"- **Recall:** {ext.get('recall', 0.0)}%")
    lines.append(f"- **F1 Score:** {ext.get('f1', 0.0)}%")
    lines.append(f"- Extracted: {ext.get('total_extracted', 0)} clauses vs Ground Truth: {ext.get('total_ground_truth', 0)}")
    lines.append("")

    lines.append("### 3.2 Evidence Retrieval (Top-K)")
    lines.append(f"- **Recall@1:** {ret.get('recall_at_1', 0.0)}% (first retrieved chunk contains ground truth)")
    lines.append(f"- **Recall@3:** {ret.get('recall_at_3', 0.0)}% (ground truth hit within top 3 chunks)")
    lines.append(f"- **Recall@5:** {ret.get('recall_at_5', 0.0)}% (ground truth hit within top 5 chunks)")
    lines.append(f"- **MRR:** {ret.get('mean_reciprocal_rank', 0.0)}")
    lines.append("")

    lines.append("### 3.3 Verification Breakdown per Class")
    lines.append("")
    lines.append("| Status Class | Ground Truth Count | Precision | Recall | F1 Score |")
    lines.append("|---|---|---|---|---|")
    per_class = ver.get("per_class", {})
    for c in ["Supported", "Partial", "Missing", "Conflict"]:
        pc = per_class.get(c, {})
        lines.append(f"| **{c}** | {pc.get('support', 0)} | {pc.get('precision', 0.0)}% | {pc.get('recall', 0.0)}% | {pc.get('f1', 0.0)}% |")
    lines.append("")

    # 4. Confusion Matrix
    lines.append("### 3.4 Verification Confusion Matrix")
    lines.append("")
    lines.append("| Expected \\ Actual | Supported | Partial | Missing | Conflict |")
    lines.append("|---|---|---|---|---|")
    for exp in ["Supported", "Partial", "Missing", "Conflict"]:
        row = cm.get(exp, {})
        s = row.get("Supported", 0)
        p = row.get("Partial", 0)
        m = row.get("Missing", 0)
        c = row.get("Conflict", 0)
        lines.append(f"| **{exp}** | {s} | {p} | {m} | {c} |")
    lines.append("")

    # 5. Failure Analysis
    lines.append("## 4. Failure & Root-Cause Analysis")
    lines.append("")
    if not failures:
        lines.append("🎉 **Zero Failures Recorded! All requirements were verified with 100% precision.**")
    else:
        lines.append(f"Total Discrepancies: **{len(failures)}** out of {ds.get('requirements_count', 0)} requirements.")
        lines.append("")
        lines.append("| Req ID | Title | Expected | Actual | Error Type | Explanation |")
        lines.append("|---|---|---|---|---|---|")
        for f in failures:
            lines.append(
                f"| `{f.get('requirement_id')}` | {f.get('requirement_title')} | `{f.get('expected_status')}` | "
                f"`{f.get('actual_status')}` | **{f.get('error_type')}** | {f.get('explanation')} |"
            )
    lines.append("")

    # 6. Engineering Recommendations
    lines.append("## 5. Pipeline Improvement Recommendations")
    lines.append("")
    lines.append("1. **Retrieval Semantic Expansion:** Enhance BM25 ranking by incorporating dense sentence embeddings (`text-embedding-004`) to capture synonyms (e.g. *dielectric breakdown* ↔ *high-pot isolation*).")
    lines.append("2. **Multi-Point Verification:** Extend deterministic regex parser to automatically construct continuous interpolation envelopes for multi-tier curves.")
    lines.append("3. **Cross-Document Entity Linking:** Track supplier part numbers and interface pinouts explicitly across documents to improve conflict recall.")
    lines.append("")

    return "\n".join(lines)


def save_evaluation_results(data: dict[str, Any], output_dir: Path = RESULTS_DIR) -> tuple[Path, Path]:
    """Save latest_results.json and latest_report.md to output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / "latest_results.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    md_content = generate_markdown_report(data)
    md_path = output_dir / "latest_report.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    print(f"Benchmark results saved:")
    print(f"  - JSON: {json_path}")
    print(f"  - Markdown Report: {md_path}")

    return json_path, md_path
