"""Excel (.xlsx) Audit Report Generator.

Generates an executive-grade, multi-sheet, beautifully styled and color-coded
audit workbook using openpyxl.
"""

from __future__ import annotations

import io
import re
from datetime import datetime
from typing import Any

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.document import Document, EvidenceChunk
from app.models.finding import Finding
from app.models.project import Project
from app.models.requirement import Requirement, RequirementEvidence

# ── Color Palette ─────────────────────────────────────────────────────────────
NAVY_HEADER = "1E293B"       # Slate 800 - table header
BANNER_BG = "0F172A"         # Slate 900 - executive banner
BRAND_ACCENT = "0284C7"      # Sky 600 - highlights
TEXT_LIGHT = "FFFFFF"
ROW_ALT_BG = "F8FAFC"        # Slate 50 - zebra striping
BORDER_COLOR = "E2E8F0"      # Slate 200 - subtle cell borders
BORDER_DARK = "94A3B8"       # Slate 400 - card / section borders

VERDICT_COLORS: dict[str, tuple[str, str]] = {
    # status: (fill_hex, text_hex)
    "Supported": ("DCFCE7", "166534"),
    "Partial": ("FEF3C7", "92400E"),
    "Conflict": ("FEE2E2", "991B1B"),
    "Missing": ("FFEDD5", "9A3412"),
    "Unknown": ("F1F5F9", "475569"),
    "Not applicable": ("F1F5F9", "64748B"),
}

SEVERITY_COLORS: dict[str, tuple[str, str]] = {
    "Critical": ("FEE2E2", "991B1B"),
    "High": ("FFEDD5", "9A3412"),
    "Medium": ("FEF3C7", "92400E"),
    "Low": ("E0F2FE", "0369A1"),
}

REVIEW_STATE_COLORS: dict[str, tuple[str, str]] = {
    "Approved": ("DCFCE7", "166534"),
    "Rejected": ("FEE2E2", "991B1B"),
    "Needs review": ("FEF3C7", "92400E"),
    "Open": ("FEF3C7", "92400E"),
    "Reviewed": ("E0E7FF", "3730A3"),
}

EVIDENCE_STATUS_COLORS: dict[str, tuple[str, str]] = {
    "Supports requirement": ("DCFCE7", "166534"),
    "Potential conflict": ("FEE2E2", "991B1B"),
    "Supporting evidence": ("E0F2FE", "0369A1"),
}


def _thin_border() -> Border:
    s = Side(style="thin", color=BORDER_COLOR)
    return Border(left=s, right=s, top=s, bottom=s)


def _card_border() -> Border:
    s = Side(style="thin", color=BORDER_DARK)
    return Border(left=s, right=s, top=s, bottom=s)


def _double_bottom_border() -> Border:
    thin = Side(style="thin", color=BORDER_COLOR)
    double = Side(style="double", color=NAVY_HEADER)
    return Border(left=thin, right=thin, top=thin, bottom=double)


def _clean_str(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d %H:%M")
    text = str(val).strip()
    return re.sub(r"[\x00-\x08\x0B-\x0C\x0E-\x1F]", "", text)


def _extract_req_details(req: Requirement) -> dict[str, Any]:
    params = req.extracted_parameters or {}
    history = params.get("review_history", []) or []
    human_event = next((item for item in reversed(history) if item.get("human_verdict") or item.get("action")), None)
    human_verdict = human_event.get("human_verdict") if human_event else None
    reviewer = human_event.get("reviewer") if human_event else ""
    comment = human_event.get("comment") if human_event else ""

    contract_issues = params.get("validation_issues", []) or []
    verification = params.get("verification", {}) or {}
    condition_results = verification.get("condition_results", []) or []
    unresolved = sum(
        1 for item in condition_results
        if item.get("validation_state") not in (None, "VALID")
    )

    contract_complete = params.get("contract_complete")
    if contract_complete is True:
        cc_str = "Yes"
    elif contract_complete is False:
        cc_str = "No"
    else:
        cc_str = "Not recorded"

    # Compile key evidence quotes
    evidence_quotes: list[str] = []
    if req.evidence_links:
        for link in req.evidence_links:
            chunk = link.evidence_chunk
            if chunk and chunk.content:
                doc_name = chunk.document.original_filename if chunk.document else "Document"
                page = f"p. {chunk.page_number}" if chunk.page_number else "p. N/A"
                snippet = chunk.content.strip().replace("\r\n", " ").replace("\n", " ")
                if len(snippet) > 180:
                    snippet = snippet[:177] + "..."
                evidence_quotes.append(f"• [{doc_name}, {page}] {snippet}")

    quotes_str = "\n".join(evidence_quotes[:4]) if evidence_quotes else "No evidence linked"

    return {
        "human_verdict": human_verdict,
        "reviewer": reviewer,
        "comment": comment,
        "contract_complete": cc_str,
        "validation_issues_count": len(contract_issues),
        "unresolved_conditions_count": unresolved,
        "quotes_str": quotes_str,
        "assessed_at": verification.get("assessed_at") or (req.updated_at.strftime("%Y-%m-%d %H:%M") if req.updated_at else ""),
    }


async def generate_audit_report_excel(db: AsyncSession, project_id: str) -> bytes:
    """Generate a multi-sheet, executive-grade Excel audit report."""

    # 1. Fetch Project
    proj_res = await db.execute(select(Project).where(Project.id == project_id))
    project = proj_res.scalar_one_or_none()
    if not project:
        raise ValueError(f"Project with ID '{project_id}' not found")

    # 2. Fetch Requirements with evidence and chunks joined
    reqs_res = await db.execute(
        select(Requirement)
        .options(
            selectinload(Requirement.evidence_links)
            .selectinload(RequirementEvidence.evidence_chunk)
            .selectinload(EvidenceChunk.document)
        )
        .where(Requirement.project_id == project_id)
        .order_by(Requirement.req_code.asc())
    )
    requirements = reqs_res.scalars().all()

    # 3. Fetch Findings
    findings_res = await db.execute(
        select(Finding)
        .options(selectinload(Finding.requirement))
        .where(Finding.project_id == project_id)
        .order_by(Finding.finding_code.asc())
    )
    findings = findings_res.scalars().all()

    # 4. Fetch Documents
    docs_res = await db.execute(
        select(Document)
        .where(Document.project_id == project_id)
        .order_by(Document.uploaded_at.desc())
    )
    documents = docs_res.scalars().all()

    # 5. Compute Summary Metrics
    total_reqs = len(requirements)
    verdict_counts: dict[str, int] = {
        "Supported": 0,
        "Partial": 0,
        "Conflict": 0,
        "Missing": 0,
        "Unknown": 0,
        "Not applicable": 0,
    }
    severity_counts: dict[str, int] = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0}
    review_counts: dict[str, int] = {"Approved": 0, "Rejected": 0, "Needs review": 0, "Open": 0, "Reviewed": 0}
    human_reviewed_count = 0
    validation_issues_count = 0

    for req in requirements:
        verdict = req.coverage_status or "Unknown"
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1

        sev = req.severity or "Medium"
        severity_counts[sev] = severity_counts.get(sev, 0) + 1

        rev = req.review_state or "Open"
        review_counts[rev] = review_counts.get(rev, 0) + 1

        if rev in ("Reviewed", "Approved", "Rejected"):
            human_reviewed_count += 1

        meta = _extract_req_details(req)
        if meta["validation_issues_count"] > 0 or meta["unresolved_conditions_count"] > 0:
            validation_issues_count += 1

    supported_count = verdict_counts.get("Supported", 0)
    coverage_rate = (supported_count / total_reqs * 100.0) if total_reqs > 0 else 0.0

    # ── Initialize Workbook ───────────────────────────────────────────────────
    wb = openpyxl.Workbook()
    # Remove default sheet
    wb.remove(wb.active)

    # ═════════════════════════════════════════════════════════════════════════
    # SHEET 1: Executive Summary
    # ═════════════════════════════════════════════════════════════════════════
    ws_summary = wb.create_sheet(title="Executive Summary")
    ws_summary.sheet_properties.tabColor = BANNER_BG
    ws_summary.views.sheetView[0].showGridLines = True

    # Banner Header
    ws_summary.merge_cells("A1:G2")
    banner_cell = ws_summary["A1"]
    banner_cell.value = "  TRACEAUDIT AI — TECHNICAL AUDIT REPORT & REGISTER"
    banner_cell.font = Font(name="Calibri", size=16, bold=True, color=TEXT_LIGHT)
    banner_cell.fill = PatternFill(start_color=BANNER_BG, end_color=BANNER_BG, fill_type="solid")
    banner_cell.alignment = Alignment(horizontal="left", vertical="center")

    # Metadata Row 4-7
    meta_fields = [
        ("Project Name:", project.name, "Audit ID:", project.audit_id),
        ("Product Name:", project.product_name, "Category:", project.product_category or "Unspecified"),
        ("Company / Org:", project.company or "N/A", "Report Date:", datetime.now().strftime("%Y-%m-%d %H:%M")),
        ("Audit Status:", project.status or "Completed", "Audit Scope:", f"{total_reqs} Technical Requirements, {len(documents)} Documents"),
    ]

    for idx, (label1, val1, label2, val2) in enumerate(meta_fields, start=4):
        # Column A, B
        c_a = ws_summary[f"A{idx}"]
        c_a.value = label1
        c_a.font = Font(name="Calibri", size=10, bold=True, color="64748B")
        c_a.alignment = Alignment(horizontal="left", vertical="center")

        c_b = ws_summary[f"B{idx}"]
        c_b.value = val1
        c_b.font = Font(name="Calibri", size=11, bold=True, color="0F172A")
        c_b.alignment = Alignment(horizontal="left", vertical="center")

        # Column D, E
        c_d = ws_summary[f"D{idx}"]
        c_d.value = label2
        c_d.font = Font(name="Calibri", size=10, bold=True, color="64748B")
        c_d.alignment = Alignment(horizontal="left", vertical="center")

        c_e = ws_summary[f"E{idx}"]
        c_e.value = val2
        c_e.font = Font(name="Calibri", size=11, bold=True, color="0F172A")
        c_e.alignment = Alignment(horizontal="left", vertical="center")

    # Key KPI Cards (Row 9-11)
    kpis = [
        ("TOTAL REQUIREMENTS", str(total_reqs), "A9", "A10", "A11", "0284C7"),
        ("FULL SUPPORT RATE", f"{coverage_rate:.1f}%", "B9", "B10", "B11", "16A34A"),
        ("HUMAN REVIEWED", f"{human_reviewed_count} / {total_reqs}", "D9", "D10", "D11", "6366F1"),
        ("UNRESOLVED / ISSUES", str(validation_issues_count), "E9", "E10", "E11", "EA580C"),
        ("LOGGED FINDINGS", str(len(findings)), "G9", "G10", "G11", "DC2626"),
    ]

    for title, val, c_top, c_mid, c_bot, color in kpis:
        cell_t = ws_summary[c_top]
        cell_t.value = title
        cell_t.font = Font(name="Calibri", size=8, bold=True, color="64748B")
        cell_t.fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
        cell_t.alignment = Alignment(horizontal="center", vertical="center")
        cell_t.border = _card_border()

        cell_m = ws_summary[c_mid]
        cell_m.value = val
        cell_m.font = Font(name="Calibri", size=18, bold=True, color=color)
        cell_m.fill = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")
        cell_m.alignment = Alignment(horizontal="center", vertical="center")
        cell_m.border = _card_border()

        cell_b = ws_summary[c_bot]
        cell_b.value = "Verified metric"
        cell_b.font = Font(name="Calibri", size=8, italic=True, color="94A3B8")
        cell_b.fill = PatternFill(start_color="FAFAFA", end_color="FAFAFA", fill_type="solid")
        cell_b.alignment = Alignment(horizontal="center", vertical="center")
        cell_b.border = _card_border()

    # Section Headers (Row 13)
    ws_summary["A13"] = "AI VERDICT DISTRIBUTION"
    ws_summary["A13"].font = Font(name="Calibri", size=11, bold=True, color=NAVY_HEADER)

    ws_summary["D13"] = "REQUIREMENT SEVERITY BREAKDOWN"
    ws_summary["D13"].font = Font(name="Calibri", size=11, bold=True, color=NAVY_HEADER)

    # Table 1: Verdict Distribution (Cols A-B, Rows 14-20)
    ws_summary["A14"] = "Verdict"
    ws_summary["B14"] = "Count"
    ws_summary["C14"] = "Share (%)"
    for col in ("A14", "B14", "C14"):
        ws_summary[col].font = Font(name="Calibri", size=10, bold=True, color=TEXT_LIGHT)
        ws_summary[col].fill = PatternFill(start_color=NAVY_HEADER, end_color=NAVY_HEADER, fill_type="solid")
        ws_summary[col].alignment = Alignment(horizontal="center", vertical="center")
        ws_summary[col].border = _thin_border()

    cur_row = 15
    for verdict, (fill, text_color) in VERDICT_COLORS.items():
        count = verdict_counts.get(verdict, 0)
        pct = (count / total_reqs * 100.0) if total_reqs > 0 else 0.0

        c_v = ws_summary[f"A{cur_row}"]
        c_v.value = verdict
        c_v.font = Font(name="Calibri", size=10, bold=True, color=text_color)
        c_v.fill = PatternFill(start_color=fill, end_color=fill, fill_type="solid")
        c_v.alignment = Alignment(horizontal="left", vertical="center")
        c_v.border = _thin_border()

        c_c = ws_summary[f"B{cur_row}"]
        c_c.value = count
        c_c.font = Font(name="Calibri", size=10, bold=True)
        c_c.alignment = Alignment(horizontal="center", vertical="center")
        c_c.border = _thin_border()

        c_p = ws_summary[f"C{cur_row}"]
        c_p.value = f"{pct:.1f}%"
        c_p.font = Font(name="Calibri", size=10)
        c_p.alignment = Alignment(horizontal="right", vertical="center")
        c_p.border = _thin_border()

        cur_row += 1

    # Total row
    ws_summary[f"A{cur_row}"] = "Total"
    ws_summary[f"B{cur_row}"] = total_reqs
    ws_summary[f"C{cur_row}"] = "100.0%"
    for col in (f"A{cur_row}", f"B{cur_row}", f"C{cur_row}"):
        ws_summary[col].font = Font(name="Calibri", size=10, bold=True)
        ws_summary[col].border = _double_bottom_border()

    # Table 2: Severity Breakdown (Cols D-E, Rows 14-19)
    ws_summary["D14"] = "Severity"
    ws_summary["E14"] = "Count"
    ws_summary["F14"] = "Share (%)"
    for col in ("D14", "E14", "F14"):
        ws_summary[col].font = Font(name="Calibri", size=10, bold=True, color=TEXT_LIGHT)
        ws_summary[col].fill = PatternFill(start_color=NAVY_HEADER, end_color=NAVY_HEADER, fill_type="solid")
        ws_summary[col].alignment = Alignment(horizontal="center", vertical="center")
        ws_summary[col].border = _thin_border()

    cur_sev_row = 15
    for sev, (fill, text_color) in SEVERITY_COLORS.items():
        count = severity_counts.get(sev, 0)
        pct = (count / total_reqs * 100.0) if total_reqs > 0 else 0.0

        c_s = ws_summary[f"D{cur_sev_row}"]
        c_s.value = sev
        c_s.font = Font(name="Calibri", size=10, bold=True, color=text_color)
        c_s.fill = PatternFill(start_color=fill, end_color=fill, fill_type="solid")
        c_s.alignment = Alignment(horizontal="left", vertical="center")
        c_s.border = _thin_border()

        c_c = ws_summary[f"E{cur_sev_row}"]
        c_c.value = count
        c_c.font = Font(name="Calibri", size=10, bold=True)
        c_c.alignment = Alignment(horizontal="center", vertical="center")
        c_c.border = _thin_border()

        c_p = ws_summary[f"F{cur_sev_row}"]
        c_p.value = f"{pct:.1f}%"
        c_p.font = Font(name="Calibri", size=10)
        c_p.alignment = Alignment(horizontal="right", vertical="center")
        c_p.border = _thin_border()

        cur_sev_row += 1

    ws_summary[f"D{cur_sev_row}"] = "Total"
    ws_summary[f"E{cur_sev_row}"] = total_reqs
    ws_summary[f"F{cur_sev_row}"] = "100.0%"
    for col in (f"D{cur_sev_row}", f"E{cur_sev_row}", f"F{cur_sev_row}"):
        ws_summary[col].font = Font(name="Calibri", size=10, bold=True)
        ws_summary[col].border = _double_bottom_border()

    # Section 3: Document Inventory (Row 23)
    doc_start_row = 23
    ws_summary[f"A{doc_start_row}"] = "AUDITED TECHNICAL DOCUMENTATION INVENTORY"
    ws_summary[f"A{doc_start_row}"].font = Font(name="Calibri", size=11, bold=True, color=NAVY_HEADER)

    doc_headers = ["Document Filename", "Document Type", "Version", "Page Count", "File Size", "Requirements Linked", "Status"]
    for idx, header in enumerate(doc_headers, start=1):
        cell = ws_summary.cell(row=doc_start_row + 1, column=idx, value=header)
        cell.font = Font(name="Calibri", size=10, bold=True, color=TEXT_LIGHT)
        cell.fill = PatternFill(start_color=NAVY_HEADER, end_color=NAVY_HEADER, fill_type="solid")
        cell.alignment = Alignment(horizontal="center" if idx > 2 else "left", vertical="center")
        cell.border = _thin_border()

    doc_row = doc_start_row + 2
    for doc in documents:
        size_kb = f"{doc.file_size / 1024:.1f} KB" if doc.file_size else "N/A"
        row_vals = [
            doc.original_filename,
            doc.doc_type or "Specification",
            doc.version or "1.0",
            doc.page_count or 1,
            size_kb,
            doc.requirements_linked or 0,
            doc.processing_status or "Indexed",
        ]
        is_even = (doc_row % 2 == 0)
        row_bg = ROW_ALT_BG if is_even else "FFFFFF"

        for col_idx, val in enumerate(row_vals, start=1):
            cell = ws_summary.cell(row=doc_row, column=col_idx, value=val)
            cell.font = Font(name="Calibri", size=9)
            cell.fill = PatternFill(start_color=row_bg, end_color=row_bg, fill_type="solid")
            cell.alignment = Alignment(horizontal="center" if col_idx in (3, 4, 5, 6, 7) else "left", vertical="center")
            cell.border = _thin_border()

        doc_row += 1

    if not documents:
        ws_summary.cell(row=doc_row, column=1, value="No documents uploaded").font = Font(name="Calibri", italic=True)

    # Set column widths for summary sheet
    summary_col_widths = {"A": 28, "B": 24, "C": 15, "D": 22, "E": 18, "F": 15, "G": 20}
    for col_letter, width in summary_col_widths.items():
        ws_summary.column_dimensions[col_letter].width = width

    # ═════════════════════════════════════════════════════════════════════════
    # SHEET 2: Requirements Register
    # ═════════════════════════════════════════════════════════════════════════
    ws_reqs = wb.create_sheet(title="Requirement Register")
    ws_reqs.sheet_properties.tabColor = "2563EB"  # Blue tab
    ws_reqs.views.sheetView[0].showGridLines = True
    ws_reqs.freeze_panes = "C2"  # Freeze headers and ID/Title

    req_headers = [
        "Requirement ID",
        "Title",
        "Category",
        "Severity",
        "AI Verdict",
        "Confidence",
        "Review State",
        "Human Verdict",
        "Reviewer",
        "Reviewer Notes",
        "Requirement Statement (Full Description)",
        "AI Detailed Analysis & Rationale",
        "AI Recommendation & Actions",
        "Evidence Count",
        "Key Evidence Citations & Quotes",
        "Contract Complete",
        "Validation Issues",
        "Unresolved Conditions",
        "Source Document",
        "Last Assessed / Updated",
    ]

    for col_idx, header in enumerate(req_headers, start=1):
        cell = ws_reqs.cell(row=1, column=col_idx, value=header)
        cell.font = Font(name="Calibri", size=10, bold=True, color=TEXT_LIGHT)
        cell.fill = PatternFill(start_color=NAVY_HEADER, end_color=NAVY_HEADER, fill_type="solid")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = _thin_border()
    ws_reqs.row_dimensions[1].height = 28

    req_data_row = 2
    for req in requirements:
        meta = _extract_req_details(req)
        conf_pct = f"{req.confidence * 100.0:.1f}%" if req.confidence <= 1.0 else f"{req.confidence:.1f}%"
        is_even = (req_data_row % 2 == 0)
        row_bg = ROW_ALT_BG if is_even else "FFFFFF"

        row_data = [
            req.req_code,
            req.title,
            req.category or "Uncategorized",
            req.severity or "Medium",
            req.coverage_status or "Unknown",
            conf_pct,
            req.review_state or "Open",
            meta["human_verdict"] or "",
            meta["reviewer"] or "",
            meta["comment"] or "",
            req.description or "No requirement text recorded.",
            req.ai_analysis or "No analysis recorded.",
            req.ai_recommendation or "No recommendation recorded.",
            req.sources_count or 0,
            meta["quotes_str"],
            meta["contract_complete"],
            meta["validation_issues_count"],
            meta["unresolved_conditions_count"],
            req.source_document or "",
            meta["assessed_at"],
        ]

        for col_idx, val in enumerate(row_data, start=1):
            cell = ws_reqs.cell(row=req_data_row, column=col_idx, value=val)
            cell.font = Font(name="Calibri", size=9)
            cell.border = _thin_border()

            # Default cell fill
            cell.fill = PatternFill(start_color=row_bg, end_color=row_bg, fill_type="solid")

            # Custom alignment & colors for specific columns
            if col_idx == 1:  # Req Code
                cell.alignment = Alignment(horizontal="center", vertical="top")
                cell.font = Font(name="Calibri", size=9, bold=True, color=BRAND_ACCENT)
            elif col_idx in (2, 3):  # Title, Category
                cell.alignment = Alignment(horizontal="left", vertical="top")
            elif col_idx == 4:  # Severity
                sev_color = SEVERITY_COLORS.get(str(val), ("F1F5F9", "475569"))
                cell.fill = PatternFill(start_color=sev_color[0], end_color=sev_color[0], fill_type="solid")
                cell.font = Font(name="Calibri", size=9, bold=True, color=sev_color[1])
                cell.alignment = Alignment(horizontal="center", vertical="top")
            elif col_idx == 5:  # AI Verdict
                v_color = VERDICT_COLORS.get(str(val), ("F1F5F9", "475569"))
                cell.fill = PatternFill(start_color=v_color[0], end_color=v_color[0], fill_type="solid")
                cell.font = Font(name="Calibri", size=9, bold=True, color=v_color[1])
                cell.alignment = Alignment(horizontal="center", vertical="top")
            elif col_idx == 6:  # Confidence
                cell.alignment = Alignment(horizontal="center", vertical="top")
            elif col_idx == 7:  # Review State
                rev_color = REVIEW_STATE_COLORS.get(str(val), ("F1F5F9", "475569"))
                cell.fill = PatternFill(start_color=rev_color[0], end_color=rev_color[0], fill_type="solid")
                cell.font = Font(name="Calibri", size=9, bold=True, color=rev_color[1])
                cell.alignment = Alignment(horizontal="center", vertical="top")
            elif col_idx == 8 and val:  # Human Verdict
                hv_color = VERDICT_COLORS.get(str(val), ("F1F5F9", "475569"))
                cell.fill = PatternFill(start_color=hv_color[0], end_color=hv_color[0], fill_type="solid")
                cell.font = Font(name="Calibri", size=9, bold=True, color=hv_color[1])
                cell.alignment = Alignment(horizontal="center", vertical="top")
            elif col_idx in (11, 12, 13, 15):  # Long text columns
                cell.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
            elif col_idx in (14, 16, 17, 18):  # Counts & flags
                cell.alignment = Alignment(horizontal="center", vertical="top")
            else:
                cell.alignment = Alignment(horizontal="left", vertical="top")

        # Give generous row height for descriptions and AI analyses
        ws_reqs.row_dimensions[req_data_row].height = 45
        req_data_row += 1

    # Auto-filter for requirements register
    last_col_letter = get_column_letter(len(req_headers))
    ws_reqs.auto_filter.ref = f"A1:{last_col_letter}{max(req_data_row - 1, 1)}"

    # Specific column widths
    req_col_widths = {
        "A": 16,  # Req ID
        "B": 32,  # Title
        "C": 18,  # Category
        "D": 14,  # Severity
        "E": 16,  # AI Verdict
        "F": 14,  # Confidence
        "G": 15,  # Review State
        "H": 15,  # Human Verdict
        "I": 16,  # Reviewer
        "J": 26,  # Reviewer Notes
        "K": 55,  # Statement
        "L": 55,  # AI Analysis
        "M": 45,  # Recommendation
        "N": 15,  # Evidence count
        "O": 60,  # Key Evidence quotes
        "P": 18,  # Contract Complete
        "Q": 16,  # Validation Issues
        "R": 20,  # Unresolved Conditions
        "S": 35,  # Source Document
        "T": 20,  # Last Assessed
    }
    for col_letter, width in req_col_widths.items():
        ws_reqs.column_dimensions[col_letter].width = width

    # ═════════════════════════════════════════════════════════════════════════
    # SHEET 3: Evidence & Traceability Matrix
    # ═════════════════════════════════════════════════════════════════════════
    ws_evidence = wb.create_sheet(title="Evidence Traceability")
    ws_evidence.sheet_properties.tabColor = "059669"  # Emerald tab
    ws_evidence.views.sheetView[0].showGridLines = True
    ws_evidence.freeze_panes = "C2"

    ev_headers = [
        "Requirement ID",
        "Requirement Title",
        "Category",
        "Source Document",
        "Page Number",
        "Evidence Assessment",
        "Evidence Label / Section",
        "Verbatim Evidence Excerpt / Quote",
    ]

    for col_idx, header in enumerate(ev_headers, start=1):
        cell = ws_evidence.cell(row=1, column=col_idx, value=header)
        cell.font = Font(name="Calibri", size=10, bold=True, color=TEXT_LIGHT)
        cell.fill = PatternFill(start_color=NAVY_HEADER, end_color=NAVY_HEADER, fill_type="solid")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = _thin_border()
    ws_evidence.row_dimensions[1].height = 26

    ev_row = 2
    for req in requirements:
        if not req.evidence_links:
            continue
        for link in req.evidence_links:
            chunk = link.evidence_chunk
            doc_name = chunk.document.original_filename if chunk and chunk.document else "N/A"
            page = chunk.page_number if chunk and chunk.page_number else "N/A"
            quote = chunk.content.strip() if chunk and chunk.content else "No quote available"

            is_even = (ev_row % 2 == 0)
            row_bg = ROW_ALT_BG if is_even else "FFFFFF"

            row_vals = [
                req.req_code,
                req.title,
                req.category or "Uncategorized",
                doc_name,
                page,
                link.status or "Supporting evidence",
                link.label or "",
                quote,
            ]

            for col_idx, val in enumerate(row_vals, start=1):
                cell = ws_evidence.cell(row=ev_row, column=col_idx, value=val)
                cell.font = Font(name="Calibri", size=9)
                cell.border = _thin_border()
                cell.fill = PatternFill(start_color=row_bg, end_color=row_bg, fill_type="solid")

                if col_idx == 1:
                    cell.alignment = Alignment(horizontal="center", vertical="top")
                    cell.font = Font(name="Calibri", size=9, bold=True, color=BRAND_ACCENT)
                elif col_idx in (2, 3, 4):
                    cell.alignment = Alignment(horizontal="left", vertical="top")
                elif col_idx == 5:
                    cell.alignment = Alignment(horizontal="center", vertical="top")
                elif col_idx == 6:
                    ev_color = EVIDENCE_STATUS_COLORS.get(str(val), ("F1F5F9", "475569"))
                    cell.fill = PatternFill(start_color=ev_color[0], end_color=ev_color[0], fill_type="solid")
                    cell.font = Font(name="Calibri", size=9, bold=True, color=ev_color[1])
                    cell.alignment = Alignment(horizontal="center", vertical="top")
                elif col_idx == 8:
                    cell.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
                else:
                    cell.alignment = Alignment(horizontal="left", vertical="top")

            ws_evidence.row_dimensions[ev_row].height = 36
            ev_row += 1

    if ev_row == 2:
        ws_evidence.cell(row=2, column=1, value="No evidence items found for this audit.").font = Font(name="Calibri", italic=True)

    ev_last_col = get_column_letter(len(ev_headers))
    ws_evidence.auto_filter.ref = f"A1:{ev_last_col}{max(ev_row - 1, 1)}"

    ev_col_widths = {
        "A": 16,
        "B": 30,
        "C": 18,
        "D": 32,
        "E": 14,
        "F": 22,
        "G": 25,
        "H": 75,
    }
    for col_letter, width in ev_col_widths.items():
        ws_evidence.column_dimensions[col_letter].width = width

    # ═════════════════════════════════════════════════════════════════════════
    # SHEET 4: Findings & Deficiencies
    # ═════════════════════════════════════════════════════════════════════════
    ws_findings = wb.create_sheet(title="Findings & Issues")
    ws_findings.sheet_properties.tabColor = "DC2626"  # Red tab
    ws_findings.views.sheetView[0].showGridLines = True
    ws_findings.freeze_panes = "C2"

    find_headers = [
        "Finding Code",
        "Associated Req ID",
        "Requirement Title",
        "Finding Type",
        "Severity",
        "Review State",
        "Category",
        "Description & Audit Notes",
        "Assigned To",
        "Logged Date",
    ]

    for col_idx, header in enumerate(find_headers, start=1):
        cell = ws_findings.cell(row=1, column=col_idx, value=header)
        cell.font = Font(name="Calibri", size=10, bold=True, color=TEXT_LIGHT)
        cell.fill = PatternFill(start_color=NAVY_HEADER, end_color=NAVY_HEADER, fill_type="solid")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = _thin_border()
    ws_findings.row_dimensions[1].height = 26

    find_row = 2
    for finding in findings:
        req_code = finding.requirement.req_code if finding.requirement else "N/A"
        req_title = finding.requirement.title if finding.requirement else "N/A"
        created = finding.created_at.strftime("%Y-%m-%d %H:%M") if finding.created_at else "N/A"

        is_even = (find_row % 2 == 0)
        row_bg = ROW_ALT_BG if is_even else "FFFFFF"

        row_vals = [
            finding.finding_code,
            req_code,
            req_title,
            finding.finding_type or "Audit discrepancy",
            finding.severity or "Medium",
            finding.review_state or "Open",
            finding.category or "Uncategorized",
            finding.description or "No description provided.",
            finding.assigned_to or "Unassigned",
            created,
        ]

        for col_idx, val in enumerate(row_vals, start=1):
            cell = ws_findings.cell(row=find_row, column=col_idx, value=val)
            cell.font = Font(name="Calibri", size=9)
            cell.border = _thin_border()
            cell.fill = PatternFill(start_color=row_bg, end_color=row_bg, fill_type="solid")

            if col_idx == 1:
                cell.alignment = Alignment(horizontal="center", vertical="top")
                cell.font = Font(name="Calibri", size=9, bold=True, color="DC2626")
            elif col_idx == 2:
                cell.alignment = Alignment(horizontal="center", vertical="top")
                cell.font = Font(name="Calibri", size=9, bold=True, color=BRAND_ACCENT)
            elif col_idx in (3, 7):
                cell.alignment = Alignment(horizontal="left", vertical="top")
            elif col_idx == 4:  # Finding type
                cell.alignment = Alignment(horizontal="left", vertical="top")
                cell.font = Font(name="Calibri", size=9, bold=True, color="991B1B")
            elif col_idx == 5:  # Severity
                sev_color = SEVERITY_COLORS.get(str(val), ("F1F5F9", "475569"))
                cell.fill = PatternFill(start_color=sev_color[0], end_color=sev_color[0], fill_type="solid")
                cell.font = Font(name="Calibri", size=9, bold=True, color=sev_color[1])
                cell.alignment = Alignment(horizontal="center", vertical="top")
            elif col_idx == 6:  # Review state
                rev_color = REVIEW_STATE_COLORS.get(str(val), ("F1F5F9", "475569"))
                cell.fill = PatternFill(start_color=rev_color[0], end_color=rev_color[0], fill_type="solid")
                cell.font = Font(name="Calibri", size=9, bold=True, color=rev_color[1])
                cell.alignment = Alignment(horizontal="center", vertical="top")
            elif col_idx == 8:
                cell.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
            elif col_idx in (9, 10):
                cell.alignment = Alignment(horizontal="center", vertical="top")
            else:
                cell.alignment = Alignment(horizontal="left", vertical="top")

        ws_findings.row_dimensions[find_row].height = 36
        find_row += 1

    if find_row == 2:
        ws_findings.cell(row=2, column=1, value="No open findings or issues recorded for this audit.").font = Font(name="Calibri", italic=True)

    find_last_col = get_column_letter(len(find_headers))
    ws_findings.auto_filter.ref = f"A1:{find_last_col}{max(find_row - 1, 1)}"

    find_col_widths = {
        "A": 16,
        "B": 18,
        "C": 30,
        "D": 24,
        "E": 14,
        "F": 15,
        "G": 18,
        "H": 65,
        "I": 18,
        "J": 18,
    }
    for col_letter, width in find_col_widths.items():
        ws_findings.column_dimensions[col_letter].width = width

    # ── Save to buffer and return bytes ───────────────────────────────────────
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()
