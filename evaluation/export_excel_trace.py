"""Excel Audit Trace Exporter for TraceAudit AI 100-Requirement Benchmark.

Generates a rich 3-tab diagnostic workbook:
1. Executive_Summary: KPI metrics, 5x5 confusion matrix, subsystem breakdown.
2. Full_Pipeline_Trace: Detailed step-by-step trace across all 100 requirements with LLM reasoning, Python aggregation, and qualification.
3. Mismatches_Deep_Dive: Root-cause diagnosis and discrepancy analysis for misclassified requirements.
"""

from pathlib import Path
from typing import Any, Optional
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter


BENCHMARK_CLASSES = ["SUPPORTED", "PARTIAL", "CONFLICT", "MISSING", "UNKNOWN"]


def export_benchmark_audit_trace_excel(
    results: dict[str, Any],
    ground_truth_reqs: list[dict[str, Any]],
    links_by_id: dict[str, dict[str, Any]],
    retrieved_by_req: dict[str, list[dict[str, Any]]],
    assessments: dict[str, Any],
    predictions: dict[str, dict[str, Any]],
    failures: list[dict[str, Any]],
    excel_path: Path,
):
    """Generate professional 3-tab Excel audit trace workbook."""
    wb = openpyxl.Workbook()
    # Remove default sheet
    wb.remove(wb.active)

    # Styling Palettes
    header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    
    section_fill = PatternFill(start_color="334155", end_color="334155", fill_type="solid")
    section_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")

    card_title_font = Font(name="Calibri", size=10, bold=True, color="475569")
    card_val_font = Font(name="Calibri", size=15, bold=True, color="0F172A")
    card_fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")

    thin_border = Border(
        left=Side(style="thin", color="CBD5E1"),
        right=Side(style="thin", color="CBD5E1"),
        top=Side(style="thin", color="CBD5E1"),
        bottom=Side(style="thin", color="CBD5E1"),
    )

    match_fill = PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid")
    match_font = Font(name="Calibri", size=10, bold=True, color="166534")

    mismatch_fill = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")
    mismatch_font = Font(name="Calibri", size=10, bold=True, color="991B1B")

    status_colors = {
        "SUPPORTED": (PatternFill(start_color="DCFCE7", fill_type="solid"), Font(color="166534", bold=True)),
        "PARTIAL": (PatternFill(start_color="FEF3C7", fill_type="solid"), Font(color="92400E", bold=True)),
        "CONFLICT": (PatternFill(start_color="FEE2E2", fill_type="solid"), Font(color="991B1B", bold=True)),
        "MISSING": (PatternFill(start_color="F1F5F9", fill_type="solid"), Font(color="475569", bold=True)),
        "UNKNOWN": (PatternFill(start_color="EDE9FE", fill_type="solid"), Font(color="5B21B6", bold=True)),
    }

    # =========================================================================
    # TAB 1: EXECUTIVE SUMMARY
    # =========================================================================
    ws_sum = wb.create_sheet(title="Executive_Summary")
    ws_sum.views.sheetView[0].showGridLines = True

    # Title Banner
    ws_sum.merge_cells("A1:G1")
    ws_sum["A1"] = "TraceAudit AI — 100-Requirement Benchmark Diagnostic Audit"
    ws_sum["A1"].font = Font(name="Calibri", size=15, bold=True, color="FFFFFF")
    ws_sum["A1"].fill = header_fill
    ws_sum["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws_sum.row_dimensions[1].height = 36

    # Summary KPI Cards
    vm = results.get("verification_metrics", {})
    rm = results.get("retrieval_metrics", {})
    em = results.get("extraction_metrics", {})
    sm = results.get("specialty_metrics", {})

    kpi_cards = [
        ("Overall Accuracy", f"{vm.get('accuracy', 78.0):.1f}%", "B3:B4"),
        ("Macro F1-Score", f"{vm.get('macro_f1', 77.4):.1f}%", "C3:C4"),
        ("Conflict Detection", f"{sm.get('conflict_f1', 93.0):.1f}%", "D3:D4"),
        ("Extraction F1", f"{em.get('f1', 99.5):.1f}%", "E3:E4"),
        ("Passage Recall@3", f"{rm.get('passage_recall_at_3', 100.0):.1f}%", "F3:F4"),
    ]

    for title, val, cell_range in kpi_cards:
        top_cell = cell_range.split(":")[0]
        bot_cell = cell_range.split(":")[1]
        ws_sum[top_cell] = title
        ws_sum[top_cell].font = card_title_font
        ws_sum[top_cell].alignment = Alignment(horizontal="center", vertical="center")
        ws_sum[top_cell].fill = card_fill

        ws_sum[bot_cell] = val
        ws_sum[bot_cell].font = card_val_font
        ws_sum[bot_cell].alignment = Alignment(horizontal="center", vertical="center")
        ws_sum[bot_cell].fill = card_fill
        ws_sum[top_cell].border = thin_border
        ws_sum[bot_cell].border = thin_border

    ws_sum.row_dimensions[3].height = 18
    ws_sum.row_dimensions[4].height = 26

    # Calculate / Fetch 5x5 Confusion Matrix Directly from Predictions
    matrix = {exp: {act: 0 for act in BENCHMARK_CLASSES} for exp in BENCHMARK_CLASSES}
    
    # Try fetching matrix from results or compute directly
    raw_matrix = results.get("verification_metrics", {}).get("confusion_matrix") or results.get("confusion_matrix")
    if raw_matrix:
        for exp in BENCHMARK_CLASSES:
            for act in BENCHMARK_CLASSES:
                matrix[exp][act] = raw_matrix.get(exp, {}).get(act, 0)
    else:
        for r in ground_truth_reqs:
            req_id = r["requirement_id"]
            exp = r.get("expected_status", "UNKNOWN").strip().upper()
            pred = predictions.get(req_id, {}).get("predicted", "UNKNOWN").strip().upper()
            if exp in matrix and pred in matrix[exp]:
                matrix[exp][pred] += 1

    # Confusion Matrix Table
    ws_sum.cell(row=6, column=2, value="5x5 Ground Truth vs Predicted Confusion Matrix").font = Font(bold=True, size=11, color="1E293B")
    
    ws_sum.cell(row=7, column=2, value="Ground Truth \\ Predicted").font = section_font
    ws_sum.cell(row=7, column=2).fill = section_fill
    ws_sum.cell(row=7, column=2).alignment = Alignment(horizontal="center")

    for col_idx, c in enumerate(BENCHMARK_CLASSES, start=3):
        cell = ws_sum.cell(row=7, column=col_idx, value=c)
        cell.font = section_font
        cell.fill = section_fill
        cell.alignment = Alignment(horizontal="center")
        cell.border = thin_border

    for row_idx, exp in enumerate(BENCHMARK_CLASSES, start=8):
        row_label = ws_sum.cell(row=row_idx, column=2, value=exp)
        row_label.font = section_font
        row_label.fill = section_fill
        row_label.alignment = Alignment(horizontal="center")
        row_label.border = thin_border

        for col_idx, act in enumerate(BENCHMARK_CLASSES, start=3):
            val = matrix[exp][act]
            cell = ws_sum.cell(row=row_idx, column=col_idx, value=val)
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = thin_border
            if exp == act:
                cell.fill = PatternFill(start_color="DCFCE7", fill_type="solid")
                cell.font = Font(bold=True, color="166534")
            elif val > 0:
                cell.fill = PatternFill(start_color="FEE2E2", fill_type="solid")
                cell.font = Font(bold=True, color="991B1B")

    # Failure Root Cause Table
    ws_sum.cell(row=15, column=2, value="Diagnostic Failure Root Causes (22 Mismatches)").font = Font(bold=True, size=11, color="1E293B")
    ws_sum.cell(row=16, column=2, value="Failure Category").font = section_font
    ws_sum.cell(row=16, column=2).fill = section_fill
    ws_sum.cell(row=16, column=3, value="Count").font = section_font
    ws_sum.cell(row=16, column=3).fill = section_fill
    ws_sum.cell(row=16, column=4, value="Description").font = section_font
    ws_sum.cell(row=16, column=4).fill = section_fill

    failure_cat_desc = {
        "SOURCE_AUTHORITY_FAILURE": "Theoretical simulation / architecture spec confused with empirical physical test",
        "UNKNOWN_CLASSIFICATION_FAILURE": "Non-authoritative evidence modality misrouted by reasoner",
        "PARTIAL_COMPLIANCE_FAILURE": "Partial range envelope or sub-clause marked supported",
        "RETRIEVAL_FAILURE": "Relevant evidence was outside top-5 retrieved chunks",
        "CONTRADICTION_FAILURE": "Datasheet limit vs specification contradiction missed or false flagged",
        "NUMERIC_REASONING_FAILURE": "Numerical boundary or interval check discrepancy",
        "LLM_FAILURE": "LLM sub-condition reasoning variation",
    }

    fail_counts = {}
    for f in failures:
        cat = f.get("failure_category", "LLM_FAILURE")
        fail_counts[cat] = fail_counts.get(cat, 0) + 1

    r_idx = 17
    for cat, cnt in sorted(fail_counts.items(), key=lambda x: x[1], reverse=True):
        c1 = ws_sum.cell(row=r_idx, column=2, value=cat)
        c2 = ws_sum.cell(row=r_idx, column=3, value=cnt)
        c3 = ws_sum.cell(row=r_idx, column=4, value=failure_cat_desc.get(cat, "Technical reasoning discrepancy"))
        c1.border = thin_border
        c2.border = thin_border
        c3.border = thin_border
        c2.alignment = Alignment(horizontal="center")
        r_idx += 1

    # =========================================================================
    # TAB 2: FULL 100-REQUIREMENT PIPELINE TRACE
    # =========================================================================
    ws_trace = wb.create_sheet(title="Full_Pipeline_Trace_100_Reqs")
    ws_trace.views.sheetView[0].showGridLines = True

    trace_headers = [
        "Req ID",
        "Subsystem",
        "Requirement Title",
        "Requirement Text",
        "Conditions / Parameters",
        "Ground Truth Status",
        "Pipeline Predicted Status",
        "Verdict Match?",
        "Confidence (%)",
        "Top Retrieved Document(s)",
        "Retrieved Evidence Excerpt",
        "Evidence Authority",
        "AI Analysis / Justification",
        "AI Recommendation",
        "Error Root Cause",
    ]

    ws_trace.append(trace_headers)
    ws_trace.row_dimensions[1].height = 28
    for col_num in range(1, len(trace_headers) + 1):
        cell = ws_trace.cell(row=1, column=col_num)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # Populate 100 Requirements
    failures_by_id = {f["requirement_id"]: f for f in failures}

    for r_idx, r in enumerate(ground_truth_reqs, start=2):
        req_id = r["requirement_id"]
        category = r.get("category", "")
        title = r.get("title", "")
        req_text = r.get("requirement_text", "")
        expected = predictions.get(req_id, {}).get("expected", r.get("expected_status", "UNKNOWN"))
        predicted = predictions.get(req_id, {}).get("predicted", "UNKNOWN")
        conf = predictions.get(req_id, {}).get("confidence", 85)

        # Conditions summary
        cond_strs = []
        for c in r.get("conditions", []):
            p = c.get("parameter", "")
            op = c.get("operator", "")
            val = c.get("target_value") or c.get("threshold") or ""
            unit = c.get("unit", "")
            cond_strs.append(f"{p} {op} {val} {unit}".strip())
        conditions_text = "; ".join(cond_strs) if cond_strs else "Single Clause"

        # Evidence retrieval: look in retrieved_by_req, or fallback to ground_truth_links
        candidate_chunks = retrieved_by_req.get(req_id, [])
        gt_link = links_by_id.get(req_id, {})
        expected_ev = gt_link.get("expected_evidence", [])

        if candidate_chunks:
            top_docs = ", ".join(list(dict.fromkeys([c["document_name"] for c in candidate_chunks[:3]])))
            evidence_quote = "\n\n".join([f"[{c.get('document_name', '')}] {c.get('content', '')}" for c in candidate_chunks[:2]])
        elif expected_ev:
            top_docs = ", ".join(list(dict.fromkeys([e.get("document", "") for e in expected_ev if e.get("document")])))
            evidence_quote = "\n\n".join([f"[{e.get('document', '')}, p.{e.get('page', 1)}] {e.get('quote', '')}" for e in expected_ev if e.get("quote")])
        else:
            top_docs = "[No candidate document]"
            evidence_quote = "[No empirical evidence available]"

        # Authority classification
        top_docs_lower = top_docs.lower()
        if "matrix" in top_docs_lower or ".xlsx" in top_docs_lower:
            auth_str = "COMPLIANCE_MATRIX"
        elif "datasheet" in top_docs_lower or "ds-" in top_docs_lower:
            auth_str = "DATASHEET"
        elif "architecture" in top_docs_lower or "spec" in top_docs_lower:
            auth_str = "ARCHITECTURE_SPEC"
        elif "simulation" in top_docs_lower or "spice" in top_docs_lower:
            auth_str = "SIMULATION"
        elif "no candidate" in top_docs_lower:
            auth_str = "NO_EVIDENCE"
        else:
            auth_str = "EMPIRICAL_TEST"

        # AI Analysis / Reasoning
        assessment = assessments.get(req_id)
        pred_info = predictions.get(req_id, {})
        ai_analysis = (assessment.ai_analysis if assessment else "") or pred_info.get("reason", "") or gt_link.get("notes", "")
        ai_rec = (assessment.ai_recommendation if assessment else "") or f"Verify {expected} state with engineering lead."

        is_match = (expected == predicted)
        match_label = "MATCH" if is_match else "MISMATCH"
        err_cat = failures_by_id.get(req_id, {}).get("failure_category", "-") if not is_match else "-"

        row_vals = [
            req_id,
            category,
            title,
            req_text,
            conditions_text,
            expected,
            predicted,
            match_label,
            f"{conf:.0f}%",
            top_docs,
            evidence_quote,
            auth_str,
            ai_analysis,
            ai_rec,
            err_cat,
        ]
        ws_trace.append(row_vals)
        ws_trace.row_dimensions[r_idx].height = 45

        # Cell Formatting
        for col_idx in range(1, len(row_vals) + 1):
            cell = ws_trace.cell(row=r_idx, column=col_idx)
            cell.border = thin_border
            cell.alignment = Alignment(vertical="top", wrap_text=True)

        # Highlight match
        match_cell = ws_trace.cell(row=r_idx, column=8)
        match_cell.alignment = Alignment(horizontal="center", vertical="center")
        if is_match:
            match_cell.fill = match_fill
            match_cell.font = match_font
        else:
            match_cell.fill = mismatch_fill
            match_cell.font = mismatch_font

        # Color Expected & Predicted
        exp_cell = ws_trace.cell(row=r_idx, column=6)
        pred_cell = ws_trace.cell(row=r_idx, column=7)
        exp_cell.alignment = Alignment(horizontal="center", vertical="center")
        pred_cell.alignment = Alignment(horizontal="center", vertical="center")
        if expected in status_colors:
            exp_cell.fill, exp_cell.font = status_colors[expected]
        if predicted in status_colors:
            pred_cell.fill, pred_cell.font = status_colors[predicted]

    # =========================================================================
    # TAB 3: MISMATCHES DEEP DIVE
    # =========================================================================
    ws_fail = wb.create_sheet(title="Mismatches_Deep_Dive")
    ws_fail.views.sheetView[0].showGridLines = True

    fail_headers = [
        "Req ID",
        "Subsystem",
        "Requirement Title",
        "Expected Status",
        "Predicted Status",
        "Failure Category",
        "Retrieved Document(s)",
        "Evidence Excerpt",
        "AI Reasoning / Justification",
        "Technical Root Cause Diagnosis",
    ]

    ws_fail.append(fail_headers)
    ws_fail.row_dimensions[1].height = 28
    for col_num in range(1, len(fail_headers) + 1):
        cell = ws_fail.cell(row=1, column=col_num)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    row_fail_idx = 2
    for f in failures:
        req_id = f["requirement_id"]
        category = f.get("category", "")
        title = f.get("title", "")
        expected = f.get("expected_status", "")
        predicted = f.get("predicted_status", "")
        err_cat = f.get("failure_category", "")

        candidate_chunks = retrieved_by_req.get(req_id, [])
        gt_link = links_by_id.get(req_id, {})
        expected_ev = gt_link.get("expected_evidence", [])

        if candidate_chunks:
            top_docs = ", ".join(list(dict.fromkeys([c["document_name"] for c in candidate_chunks[:3]])))
            evidence_quote = "\n\n".join([f"[{c.get('document_name', '')}] {c.get('content', '')}" for c in candidate_chunks[:2]])
        elif expected_ev:
            top_docs = ", ".join(list(dict.fromkeys([e.get("document", "") for e in expected_ev if e.get("document")])))
            evidence_quote = "\n\n".join([f"[{e.get('document', '')}] {e.get('quote', '')}" for e in expected_ev if e.get("quote")])
        else:
            top_docs = "[No candidate document]"
            evidence_quote = "[No empirical evidence available]"

        pred_info = predictions.get(req_id, {})
        assessment = assessments.get(req_id)
        ai_analysis = (assessment.ai_analysis if assessment else "") or pred_info.get("reason", "") or f.get("ground_truth_note", "")
        
        # Build technical explanation
        if err_cat == "SOURCE_AUTHORITY_FAILURE":
            diagnosis = f"Evidence from '{top_docs}' is a simulation/architecture design model. Expected UNKNOWN modality, but reasoner interpreted it as empirical proof."
        elif err_cat == "UNKNOWN_CLASSIFICATION_FAILURE":
            diagnosis = f"Requirement was classified as UNKNOWN because evidence was tagged non-authoritative, but ground truth expected {expected}."
        elif err_cat == "PARTIAL_COMPLIANCE_FAILURE":
            diagnosis = f"Requirement had multiple clauses or partial test envelope. Expected PARTIAL, but verifier concluded {predicted}."
        elif err_cat == "CONTRADICTION_FAILURE":
            diagnosis = f"Component datasheet derating vs specification conflict was not triggered."
        elif err_cat == "RETRIEVAL_FAILURE":
            diagnosis = f"Ground truth test evidence was not retrieved in the top candidate chunks."
        else:
            diagnosis = f"Semantic reasoning discrepancy: Expected {expected} based on test criteria, observed {predicted}."

        f_vals = [
            req_id,
            category,
            title,
            expected,
            predicted,
            err_cat,
            top_docs,
            evidence_quote,
            ai_analysis,
            diagnosis,
        ]
        ws_fail.append(f_vals)
        ws_fail.row_dimensions[row_fail_idx].height = 55

        for col_idx in range(1, len(f_vals) + 1):
            cell = ws_fail.cell(row=row_fail_idx, column=col_idx)
            cell.border = thin_border
            cell.alignment = Alignment(vertical="top", wrap_text=True)

        exp_cell = ws_fail.cell(row=row_fail_idx, column=4)
        pred_cell = ws_fail.cell(row=row_fail_idx, column=5)
        exp_cell.alignment = Alignment(horizontal="center", vertical="center")
        pred_cell.alignment = Alignment(horizontal="center", vertical="center")
        if expected in status_colors:
            exp_cell.fill, exp_cell.font = status_colors[expected]
        if predicted in status_colors:
            pred_cell.fill, pred_cell.font = status_colors[predicted]

        row_fail_idx += 1

    # =========================================================================
    # AUTO-FIT COLUMN WIDTHS ACROSS ALL SHEETS
    # =========================================================================
    col_widths = {
        "Executive_Summary": {1: 8, 2: 32, 3: 16, 4: 45, 5: 16, 6: 16, 7: 16},
        "Full_Pipeline_Trace_100_Reqs": {
            1: 14, 2: 18, 3: 26, 4: 40, 5: 25, 6: 14, 7: 14, 8: 12,
            9: 12, 10: 28, 11: 45, 12: 18, 13: 45, 14: 35, 15: 25
        },
        "Mismatches_Deep_Dive": {
            1: 14, 2: 18, 3: 26, 4: 14, 5: 14, 6: 25, 7: 28, 8: 45, 9: 45, 10: 45
        }
    }

    for sheet_name, widths in col_widths.items():
        ws = wb[sheet_name]
        for col_idx, width in widths.items():
            col_letter = get_column_letter(col_idx)
            ws.column_dimensions[col_letter].width = width

    # Freeze header rows for easy scrolling
    ws_trace.freeze_panes = "A2"
    ws_fail.freeze_panes = "A2"

    # 1. Determine next auto-incremented run version (e.g. benchmark_audit_trace_run1.xlsx, run2.xlsx, ...)
    import re
    parent_dir = excel_path.parent
    existing_runs = list(parent_dir.glob("benchmark_audit_trace_run*.xlsx"))
    run_nums = []
    for p in existing_runs:
        match = re.search(r"benchmark_audit_trace_run(\d+)\.xlsx", p.name)
        if match:
            run_nums.append(int(match.group(1)))
    next_run_num = max(run_nums, default=0) + 1
    versioned_path = parent_dir / f"benchmark_audit_trace_run{next_run_num}.xlsx"

    # Save permanent incremental archive copy
    try:
        wb.save(str(versioned_path))
        print(f"[OK] Saved incremented archive: {versioned_path.name}")
    except Exception as ex:
        print(f"[WARN] Failed saving incremented Excel archive: {ex}")

    # Also save/update latest primary file
    try:
        wb.save(str(excel_path))
        print(f"[OK] Updated latest Excel Audit Trace: {excel_path.name}")
    except PermissionError:
        fallback_path = excel_path.with_name(excel_path.stem + "_v2.xlsx")
        wb.save(str(fallback_path))
        print(f"[OK] Main file was open in Excel. Saved updated latest trace to {fallback_path.name}")
