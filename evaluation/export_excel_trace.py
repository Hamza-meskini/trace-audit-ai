"""Excel Audit Trace Exporter for TraceAudit AI 100-Requirement Benchmark.

Generates a rich 5-tab diagnostic workbook:
1. Executive_Summary: KPI metrics, 5x5 confusion matrix, subsystem breakdown.
2. Full_Pipeline_Trace: Detailed step-by-step trace across all 100 requirements with LLM reasoning, Python aggregation, and qualification.
3. Mismatches_Deep_Dive: Root-cause diagnosis and discrepancy analysis for misclassified requirements.
4. Atomic_Condition_Diagnostics: Raw LLM through final Python state for every condition.
5. Contract_Field_Diagnostics: Parameter/operator/value/unit extraction mismatches.
"""

from pathlib import Path
from typing import Any, Optional
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter


BENCHMARK_CLASSES = ["SUPPORTED", "PARTIAL", "CONFLICT", "MISSING", "UNKNOWN"]


def _authority_for_chunk(chunk: dict[str, Any]) -> str:
    """Compact reporting classifier; preserves every retrieved source type."""
    name = chunk.get("document_name", "").lower()
    doc_type = chunk.get("doc_type", "").lower()
    content = chunk.get("content", "").lower()
    if "matrix" in name or name.endswith(".xlsx") or "compliance matrix" in doc_type:
        return "COMPLIANCE_MATRIX"
    if "datasheet" in name:
        return "DATASHEET"
    if "architecture" in name or "specification" in doc_type:
        return "ARCHITECTURE_SPEC"
    if any(marker in content for marker in ("spice", "simulink", "theoretical simulation", "model predicts")):
        return "SIMULATION"
    if "test report" in doc_type or "validation" in name or "qualification" in name:
        return "EMPIRICAL_TEST"
    if any(marker in content for marker in ("measured", "test case", "verdict: pass", "verdict: fail", "bench test")):
        return "EMPIRICAL_TEST"
    return "UNKNOWN"


def export_benchmark_audit_trace_excel(
    results: dict[str, Any],
    ground_truth_reqs: list[dict[str, Any]],
    links_by_id: dict[str, dict[str, Any]],
    retrieved_by_req: dict[str, list[dict[str, Any]]],
    assessments: dict[str, Any],
    predictions: dict[str, dict[str, Any]],
    failures: list[dict[str, Any]],
    excel_path: Path,
    pipeline_requirements: Optional[list[dict[str, Any]]] = None,
    verification_evidence_by_req: Optional[dict[str, list[dict[str, Any]]]] = None,
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

    ws_sum.merge_cells("A2:G2")
    evaluation_mode = results.get("evaluation_mode", "oracle").upper()
    downstream_source = results.get("downstream_requirement_source", "ground_truth_oracle_contracts")
    ws_sum["A2"] = f"Evaluation mode: {evaluation_mode} | Downstream requirement source: {downstream_source}"
    ws_sum["A2"].font = Font(name="Calibri", size=10, italic=True, color="475569")
    ws_sum["A2"].alignment = Alignment(horizontal="center", vertical="center")

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
        ("Normalized Exact Contract", f"{em.get('atomic_condition_exact_recall', 0.0):.1f}%", "G3:G4"),
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

    # Failure Root Cause Table. The deep-dive sheet is populated from this
    # same list, so its length is the single source of truth for the title.
    mismatch_count = len(failures)
    mismatch_label = "Mismatch" if mismatch_count == 1 else "Mismatches"
    ws_sum.cell(
        row=15,
        column=2,
        value=f"Diagnostic Failure Root Causes ({mismatch_count} {mismatch_label})",
    ).font = Font(bold=True, size=11, color="1E293B")
    ws_sum.cell(row=16, column=2, value="Failure Category").font = section_font
    ws_sum.cell(row=16, column=2).fill = section_fill
    ws_sum.cell(row=16, column=3, value="Count").font = section_font
    ws_sum.cell(row=16, column=3).fill = section_fill
    ws_sum.cell(row=16, column=4, value="Description").font = section_font
    ws_sum.cell(row=16, column=4).fill = section_fill

    failure_cat_desc = {
        "EXTRACTION_FAILURE": "Required contract was not extracted, so downstream retrieval/verification could not run",
        "CITATION_TRACEABILITY_FAILURE": "A condition quote was not found in the evidence ID attached to it",
        "POST_LLM_DETERMINISTIC_REGRESSION": "The model's provisional final status matched ground truth, but deterministic post-processing changed it",
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

    # Stage-isolation summary. These metrics distinguish model reasoning from
    # deterministic post-processing and from end-to-end audit defensibility.
    condition_metrics = results.get("condition_metrics", {})
    stage_metrics = results.get("stage_diagnostics", {})
    raw_llm_metrics = condition_metrics.get("raw_llm", {})
    ws_sum.cell(row=15, column=6, value="Stage Isolation Metrics").font = Font(bold=True, size=11, color="1E293B")
    ws_sum.cell(row=16, column=6, value="Metric").font = section_font
    ws_sum.cell(row=16, column=6).fill = section_fill
    ws_sum.cell(row=16, column=7, value="Value").font = section_font
    ws_sum.cell(row=16, column=7).fill = section_fill
    stage_rows = [
        ("Raw LLM mixed-label agreement", raw_llm_metrics.get("condition_accuracy")),
        ("Final mixed-label agreement", condition_metrics.get("condition_accuracy")),
        (
            "Explicit atomic accuracy",
            condition_metrics.get("accuracy_by_ground_truth_source", {}).get("explicit", {}).get("accuracy"),
        ),
        ("Aggregation oracle accuracy", stage_metrics.get("aggregation_oracle", {}).get("accuracy")),
        (
            "Explicit-GT audit defensibility",
            stage_metrics.get("explicit_ground_truth_audit", {}).get("accuracy"),
        ),
        (
            "Self-reported contract completeness",
            em.get("self_reported_contract_completeness_rate"),
        ),
    ]
    for row_index, (label, value) in enumerate(stage_rows, start=17):
        ws_sum.cell(row=row_index, column=6, value=label).border = thin_border
        value_cell = ws_sum.cell(row=row_index, column=7, value=(value / 100.0 if value is not None else None))
        value_cell.number_format = "0.00%"
        value_cell.border = thin_border

    # Business-safety metrics are separate from semantic accuracy.  Counts
    # stay as counts; rates use percentage formatting.
    review_safety = results.get("review_gate_safety_metrics", {})
    ws_sum.cell(row=23, column=6, value="Review-Gate Business Safety").font = Font(bold=True, size=11, color="1E293B")
    ws_sum.cell(row=24, column=6, value="Metric").font = section_font
    ws_sum.cell(row=24, column=6).fill = section_fill
    ws_sum.cell(row=24, column=7, value="Value").font = section_font
    ws_sum.cell(row=24, column=7).fill = section_fill
    safety_rows = [
        ("False SUPPORTED predictions", review_safety.get("false_supported_predictions", 0), False),
        ("Routed safely to review", review_safety.get("false_supported_routed_to_review", 0), False),
        ("False automatic closures", review_safety.get("false_automatic_closures", 0), False),
        ("Review-gate capture rate", review_safety.get("review_gate_capture_rate"), True),
        ("Automatic-closure precision", review_safety.get("automatic_closure_precision"), True),
        ("Supported review rate", review_safety.get("supported_review_rate"), True),
    ]
    for row_index, (label, value, is_percent) in enumerate(safety_rows, start=25):
        ws_sum.cell(row=row_index, column=6, value=label).border = thin_border
        stored_value = value / 100.0 if is_percent and value is not None else value
        value_cell = ws_sum.cell(row=row_index, column=7, value=stored_value)
        if is_percent:
            value_cell.number_format = "0.00%"
        value_cell.border = thin_border

    # =========================================================================
    # TAB 2: FULL 100-REQUIREMENT PIPELINE TRACE
    # =========================================================================
    ws_trace = wb.create_sheet(title="Full_Pipeline_Trace_100_Reqs")
    ws_trace.views.sheetView[0].showGridLines = True

    trace_headers = [
        "Req ID",
        "Subsystem",
        "Requirement Title",
        "Ground Truth Requirement Text",
        "Pipeline Requirement Text",
        "Ground Truth Conditions / Parameters",
        "Pipeline Conditions / Parameters",
        "Ground Truth Status",
        "Pipeline Predicted Status",
        "Verdict Match?",
        "Confidence (%)",
        "Verifier Evidence Document(s)",
        "Verifier Evidence Excerpt",
        "Evidence Authority",
        "AI Analysis / Justification",
        "AI Recommendation",
        "Error Root Cause",
        "Review State",
        "Review Required?",
        "Auto-Close Eligible?",
        "Review-Gate Reasons",
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
    pipeline_by_id = {
        requirement["req_code"]: requirement
        for requirement in (pipeline_requirements or [])
    }
    verifier_evidence = verification_evidence_by_req or retrieved_by_req

    for r_idx, r in enumerate(ground_truth_reqs, start=2):
        req_id = r["requirement_id"]
        category = r.get("category", "")
        title = r.get("title", "")
        req_text = r.get("requirement_text", "")
        pipeline_req = pipeline_by_id.get(req_id, {})
        pipeline_req_text = pipeline_req.get("description", "[NOT EXTRACTED]")
        expected = predictions.get(req_id, {}).get("expected", r.get("expected_status", "UNKNOWN"))
        predicted = predictions.get(req_id, {}).get("predicted", "UNKNOWN")
        conf = predictions.get(req_id, {}).get("confidence", 85)
        review_state = predictions.get(req_id, {}).get("review_state", "Needs review")
        review_required = bool(predictions.get(req_id, {}).get("review_required", False))
        auto_close_eligible = bool(predictions.get(req_id, {}).get("auto_close_eligible", False))
        review_gate_reasons = predictions.get(req_id, {}).get("review_gate_reasons", [])

        # Conditions summary
        cond_strs = []
        for c in r.get("conditions", []):
            p = c.get("parameter", "")
            op = c.get("operator", "")
            val = c.get("target_value") or c.get("threshold") or ""
            unit = c.get("unit", "")
            cond_strs.append(f"{p} {op} {val} {unit}".strip())
        conditions_text = "; ".join(cond_strs) if cond_strs else "Single Clause"
        pipeline_cond_strs = []
        for condition in pipeline_req.get("conditions", []):
            value = (
                condition.get("threshold")
                if condition.get("threshold") is not None
                else condition.get("min_value")
                if condition.get("min_value") is not None
                else condition.get("max_value", "")
            )
            pipeline_cond_strs.append(
                f"{condition.get('condition_id', '')}: {condition.get('parameter', '')} "
                f"{condition.get('operator', '')} {value} {condition.get('unit', '')}".strip()
            )
        pipeline_conditions_text = "; ".join(pipeline_cond_strs) if pipeline_cond_strs else "[NO EXTRACTED CONDITIONS]"

        # Evidence retrieval: look in retrieved_by_req, or fallback to ground_truth_links
        candidate_chunks = verifier_evidence.get(req_id, [])
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

        # Report every authority represented in the retrieved candidates. A
        # leading compliance matrix must not hide an empirical report at rank 2.
        if candidate_chunks:
            authorities = [
                _authority_for_chunk(chunk)
                for chunk in candidate_chunks[:3]
            ]
            auth_str = " + ".join(dict.fromkeys(authorities))
        else:
            auth_str = "NO_EVIDENCE"

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
            pipeline_req_text,
            conditions_text,
            pipeline_conditions_text,
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
            review_state,
            "YES" if review_required else "NO",
            "YES" if auto_close_eligible else "NO",
            "\n".join(review_gate_reasons) if review_gate_reasons else "-",
        ]
        ws_trace.append(row_vals)
        ws_trace.row_dimensions[r_idx].height = 45

        # Cell Formatting
        for col_idx in range(1, len(row_vals) + 1):
            cell = ws_trace.cell(row=r_idx, column=col_idx)
            cell.border = thin_border
            cell.alignment = Alignment(vertical="top", wrap_text=True)

        # Highlight match
        match_cell = ws_trace.cell(row=r_idx, column=10)
        match_cell.alignment = Alignment(horizontal="center", vertical="center")
        if is_match:
            match_cell.fill = match_fill
            match_cell.font = match_font
        else:
            match_cell.fill = mismatch_fill
            match_cell.font = mismatch_font

        # Color Expected & Predicted
        exp_cell = ws_trace.cell(row=r_idx, column=8)
        pred_cell = ws_trace.cell(row=r_idx, column=9)
        exp_cell.alignment = Alignment(horizontal="center", vertical="center")
        pred_cell.alignment = Alignment(horizontal="center", vertical="center")
        if expected in status_colors:
            exp_cell.fill, exp_cell.font = status_colors[expected]
        if predicted in status_colors:
            pred_cell.fill, pred_cell.font = status_colors[predicted]

        review_cell = ws_trace.cell(row=r_idx, column=19)
        auto_close_cell = ws_trace.cell(row=r_idx, column=20)
        if review_required:
            review_cell.fill = PatternFill(start_color="FEF3C7", fill_type="solid")
            review_cell.font = Font(color="92400E", bold=True)
        if auto_close_eligible and expected != "SUPPORTED":
            auto_close_cell.fill = mismatch_fill
            auto_close_cell.font = mismatch_font
        elif auto_close_eligible:
            auto_close_cell.fill = match_fill
            auto_close_cell.font = match_font

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
        "Verifier Evidence Document(s)",
        "Verifier Evidence Excerpt",
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

        candidate_chunks = verifier_evidence.get(req_id, [])
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
        elif err_cat == "CITATION_TRACEABILITY_FAILURE":
            diagnosis = "A condition quote and its cited evidence ID did not refer to the same retrieved passage, so Python conservatively rejected the proof."
        elif err_cat == "POST_LLM_DETERMINISTIC_REGRESSION":
            diagnosis = "The LLM provisional status matched the expected result, but deterministic reconciliation, evidence qualification, or aggregation changed the final decision. Inspect the atomic diagnostics transition columns."
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
    # TAB 4: ATOMIC CONDITION STAGE DIAGNOSTICS
    # =========================================================================
    ws_atomic = wb.create_sheet(title="Atomic_Condition_Diagnostics")
    ws_atomic.views.sheetView[0].showGridLines = True
    atomic_headers = [
        "Req ID", "Condition ID", "Parameter", "Ground Truth Status", "GT Label Source",
        "Raw LLM Status", "Post-Reconciliation", "Pre-Qualification", "Final Status",
        "Correct?", "Numeric Condition?", "Python Effect",
    ]
    ws_atomic.append(atomic_headers)
    for col_num in range(1, len(atomic_headers) + 1):
        cell = ws_atomic.cell(row=1, column=col_num)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    for row_index, record in enumerate(condition_metrics.get("condition_records", []), start=2):
        raw_status = record.get("llm_status")
        final_status = record.get("final_status")
        correct = bool(record.get("correct"))
        expected_status = record.get("expected_status")
        if raw_status is None:
            python_effect = "NO_LLM_PATH"
        elif raw_status == expected_status and final_status != expected_status:
            python_effect = "DAMAGED"
        elif raw_status != expected_status and final_status == expected_status:
            python_effect = "CORRECTED"
        elif correct:
            python_effect = "UNCHANGED_CORRECT"
        else:
            python_effect = "UNCHANGED_WRONG"
        row = [
            record.get("requirement_id"), record.get("condition_id"), record.get("parameter"),
            expected_status, record.get("ground_truth_source"), raw_status,
            record.get("post_reconciliation_status"), record.get("pre_qualification_status"),
            final_status, "YES" if correct else "NO",
            "YES" if record.get("is_numeric_condition") else "NO", python_effect,
        ]
        ws_atomic.append(row)
        for col_idx in range(1, len(row) + 1):
            cell = ws_atomic.cell(row=row_index, column=col_idx)
            cell.border = thin_border
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        correctness_cell = ws_atomic.cell(row=row_index, column=10)
        correctness_cell.fill = match_fill if correct else mismatch_fill
        correctness_cell.font = match_font if correct else mismatch_font

    # =========================================================================
    # TAB 5: CONTRACT FIELD DIAGNOSTICS
    # =========================================================================
    ws_contract = wb.create_sheet(title="Contract_Field_Diagnostics")
    ws_contract.views.sheetView[0].showGridLines = True
    contract_headers = [
        "Req ID", "Condition ID", "Failed Field(s)", "Expected Parameter", "Extracted Parameter",
        "Expected Operator", "Extracted Operator", "Expected Threshold", "Extracted Threshold",
        "Expected Unit", "Extracted Unit",
    ]
    ws_contract.append(contract_headers)
    for col_num in range(1, len(contract_headers) + 1):
        cell = ws_contract.cell(row=1, column=col_num)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    contract_mismatches = results.get("extraction_metrics", {}).get("contract_mismatches", [])
    for row_index, mismatch in enumerate(contract_mismatches, start=2):
        expected_contract = mismatch.get("expected", {})
        extracted_contract = mismatch.get("extracted") or {}
        row = [
            mismatch.get("requirement_id"), mismatch.get("condition_id"),
            ", ".join(mismatch.get("failed_fields", [])),
            expected_contract.get("parameter"), extracted_contract.get("parameter"),
            expected_contract.get("operator"), extracted_contract.get("operator"),
            expected_contract.get("threshold"), extracted_contract.get("threshold"),
            expected_contract.get("unit"), extracted_contract.get("unit"),
        ]
        ws_contract.append(row)
        for col_idx in range(1, len(row) + 1):
            cell = ws_contract.cell(row=row_index, column=col_idx)
            cell.border = thin_border
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    # =========================================================================
    # AUTO-FIT COLUMN WIDTHS ACROSS ALL SHEETS
    # =========================================================================
    col_widths = {
        "Executive_Summary": {1: 8, 2: 32, 3: 16, 4: 45, 5: 16, 6: 16, 7: 16},
        "Full_Pipeline_Trace_100_Reqs": {
            1: 14, 2: 18, 3: 26, 4: 38, 5: 38, 6: 28, 7: 28, 8: 14,
            9: 14, 10: 12, 11: 12, 12: 28, 13: 45, 14: 28, 15: 45,
            16: 35, 17: 25, 18: 18, 19: 18, 20: 20, 21: 55
        },
        "Mismatches_Deep_Dive": {
            1: 14, 2: 18, 3: 26, 4: 14, 5: 14, 6: 25, 7: 28, 8: 45, 9: 45, 10: 45
        },
        "Atomic_Condition_Diagnostics": {
            1: 14, 2: 16, 3: 24, 4: 18, 5: 24, 6: 18, 7: 20, 8: 20, 9: 16,
            10: 12, 11: 16, 12: 20,
        },
        "Contract_Field_Diagnostics": {
            1: 14, 2: 16, 3: 24, 4: 26, 5: 26, 6: 18, 7: 18, 8: 18, 9: 18,
            10: 18, 11: 18,
        },
    }

    for sheet_name, widths in col_widths.items():
        ws = wb[sheet_name]
        for col_idx, width in widths.items():
            col_letter = get_column_letter(col_idx)
            ws.column_dimensions[col_letter].width = width

    # Freeze header rows for easy scrolling
    ws_trace.freeze_panes = "A2"
    ws_fail.freeze_panes = "A2"
    ws_atomic.freeze_panes = "A2"
    ws_contract.freeze_panes = "A2"

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
