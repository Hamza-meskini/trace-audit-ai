"""Generate the four Nova evidence reports and augmented ground truth."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, Table, TableStyle

from end_to_end_data import (
    evidence_asset_name,
    materialize_end_to_end_ground_truth,
    requirements_for_evidence_document,
)


ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "documents"
ASSETS = ROOT / "assets"
PAGE_W, PAGE_H = A4
NAVY = colors.HexColor("#17324D")
BLUE = colors.HexColor("#1872A7")
INK = colors.HexColor("#172033")
MUTED = colors.HexColor("#5E6B7A")
LINE = colors.HexColor("#CAD5DF")
PALE = colors.HexColor("#F2F7FA")


def image_font(size: int, bold: bool = False):
    for path in (
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/calibrib.ttf" if bold else "C:/Windows/Fonts/calibri.ttf"),
    ):
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def header(pdf: canvas.Canvas, title: str, page: int) -> None:
    pdf.setFillColor(NAVY)
    pdf.rect(0, PAGE_H - 24 * mm, PAGE_W, 24 * mm, fill=1, stroke=0)
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(15 * mm, PAGE_H - 15 * mm, title)
    pdf.setFont("Helvetica", 8)
    pdf.drawRightString(PAGE_W - 15 * mm, PAGE_H - 15 * mm, f"Controlled verification record | Page {page} of 5")
    pdf.setStrokeColor(LINE)
    pdf.line(15 * mm, 14 * mm, PAGE_W - 15 * mm, 14 * mm)
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 7)
    pdf.drawString(15 * mm, 9 * mm, "Synthetic benchmark evidence - not a certification or production claim")


def draw_cover(pdf: canvas.Canvas, spec: dict, requirements: list[dict]) -> None:
    pdf.setFillColor(NAVY)
    pdf.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    title_style = ParagraphStyle(
        "cover-title", fontName="Helvetica-Bold", fontSize=24, leading=29,
        textColor=colors.white,
    )
    title = Paragraph(spec["title"], title_style)
    _, title_height = title.wrap(PAGE_W - 44 * mm, 60 * mm)
    title.drawOn(pdf, 22 * mm, PAGE_H - 46 * mm - title_height)
    pdf.setFont("Helvetica", 12)
    pdf.drawString(22 * mm, PAGE_H - 78 * mm, "Executed verification and inspection record")
    pdf.setFillColor(colors.HexColor("#D7EAF3"))
    pdf.roundRect(22 * mm, PAGE_H - 150 * mm, PAGE_W - 44 * mm, 55 * mm, 4 * mm, fill=1, stroke=0)
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(30 * mm, PAGE_H - 110 * mm, "Report scope")
    pdf.setFont("Helvetica", 9)
    lines = [
        f"Requirements evaluated: {len(requirements)}",
        f"Domains: {', '.join(spec['domains'])}",
        "Methods: bench test, environmental exposure, inspection, log review",
        "Result vocabulary: PASS, FAIL, NOT TESTED, INCONCLUSIVE",
    ]
    for idx, line in enumerate(lines):
        pdf.drawString(30 * mm, PAGE_H - (121 + 8 * idx) * mm, line)
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica", 8)
    pdf.drawString(22 * mm, 20 * mm, "NOVA-DV | Revision A | Synthetic controlled copy")
    pdf.showPage()


def evidence_rows(requirements: list[dict], page: int) -> list[list]:
    style = ParagraphStyle("evidence", fontName="Helvetica", fontSize=6.8, leading=8.2, textColor=INK)
    strong = ParagraphStyle("evidence-strong", fontName="Helvetica-Bold", fontSize=7, leading=8.2, textColor=NAVY)
    rows: list[list] = [[
        Paragraph("Requirement", strong), Paragraph("Verification record", strong), Paragraph("Source", strong)
    ]]
    for requirement in requirements:
        page_evidence = []
        for condition in requirement["conditions"]:
            for evidence in condition.get("evidence", []):
                if evidence["page"] == page and evidence["block_type"] == "table":
                    page_evidence.append(evidence["quote"])
        if not page_evidence:
            continue
        rows.append([
            Paragraph(f"{requirement['requirement_id']}<br/>{requirement['title']}", strong),
            Paragraph("<br/><br/>".join(page_evidence), style),
            Paragraph("Bench / inspection record<br/>Reviewed by DV team", style),
        ])
    return rows


def draw_table_page(pdf: canvas.Canvas, spec: dict, requirements: list[dict], page: int) -> None:
    header(pdf, spec["title"], page)
    pdf.setFillColor(NAVY)
    pdf.setFont("Helvetica-Bold", 15)
    pdf.drawString(15 * mm, PAGE_H - 38 * mm, f"{page - 1}. Executed results")
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 8)
    pdf.drawString(15 * mm, PAGE_H - 45 * mm, "Each disposition applies only to the criterion stated in the same record.")
    rows = evidence_rows(requirements, page)
    table = Table(rows, colWidths=[37 * mm, 112 * mm, 31 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 1), (-1, -1), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    _, height = table.wrap(PAGE_W - 30 * mm, PAGE_H - 65 * mm)
    table.drawOn(pdf, 15 * mm, PAGE_H - 52 * mm - height)
    pdf.showPage()


def make_visual_record(requirements: list[dict], output: Path) -> None:
    image = Image.new("RGB", (1800, 2000), "#F7FAFC")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((35, 35, 1765, 1965), radius=28, outline="#17324D", width=7, fill="#FFFFFF")
    draw.text((80, 70), "VISUAL INSPECTION RECORD", fill="#17324D", font=image_font(38, True))
    y = 150
    palette = {"PROVEN": "#16834A", "FAILED": "#C53030", "UNTESTED": "#A15C00", "PENDING": "#B7791F", "INCONCLUSIVE": "#53657A"}
    for requirement in requirements:
        draw.text((80, y), f"{requirement['requirement_id']}  |  {requirement['title']}", fill="#1872A7", font=image_font(29, True))
        y += 55
        for condition in requirement["conditions"]:
            status = condition["expected_status"]
            statement = condition["evidence"][0]["quote"]
            draw.rounded_rectangle((75, y, 1725, y + 125), radius=16, outline=palette[status], width=4, fill="#F9FBFC")
            draw.text((100, y + 14), status.replace("PROVEN", "PASS"), fill=palette[status], font=image_font(24, True))
            wrapped = textwrap.wrap(statement.split("; ", 1)[-1], width=118)
            for line_index, line in enumerate(wrapped[:2]):
                draw.text((100, y + 55 + line_index * 29), line, fill="#172033", font=image_font(20))
            y += 140
        y += 25
    draw.text((80, 1915), "Image record NOVA-DV-VIS | Reviewer: QA-17 | Controlled copy", fill="#5E6B7A", font=image_font(20))
    image.save(output)


def draw_visual_page(pdf: canvas.Canvas, spec: dict, requirements: list[dict], asset: Path) -> None:
    header(pdf, spec["title"], 5)
    pdf.setFillColor(NAVY)
    pdf.setFont("Helvetica-Bold", 15)
    pdf.drawString(15 * mm, PAGE_H - 38 * mm, "4. Image-based inspection result")
    pdf.drawImage(str(asset), 15 * mm, 32 * mm, PAGE_W - 30 * mm, PAGE_H - 80 * mm, preserveAspectRatio=True, anchor="c", mask="auto")
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 8)
    identifiers = ", ".join(item["requirement_id"] for item in requirements)
    pdf.drawString(15 * mm, 22 * mm, f"Figure 4-1. Recorded inspections for {identifiers}; interpretation requires the raster content.")
    pdf.showPage()


def generate() -> dict:
    DOCS.mkdir(parents=True, exist_ok=True)
    ASSETS.mkdir(parents=True, exist_ok=True)
    dataset = materialize_end_to_end_ground_truth()
    for spec in dataset["evidence_documents"]:
        requirements = requirements_for_evidence_document(dataset, spec["filename"])
        visual_requirements = [item for item in requirements if item["source_modality"] == "figure"]
        asset = ASSETS / evidence_asset_name(spec["filename"])
        make_visual_record(visual_requirements, asset)
        pdf = canvas.Canvas(str(DOCS / spec["filename"]), pagesize=A4, pageCompression=1)
        pdf.setTitle(spec["title"])
        pdf.setAuthor("TraceAudit synthetic benchmark")
        draw_cover(pdf, spec, requirements)
        for page in (2, 3, 4):
            draw_table_page(pdf, spec, requirements, page)
        draw_visual_page(pdf, spec, visual_requirements, asset)
        pdf.save()
    (ROOT / "ground_truth.json").write_text(json.dumps(dataset, indent=2), encoding="utf-8")
    return dataset


if __name__ == "__main__":
    data = generate()
    print(
        f"Generated {len(data['evidence_documents'])} evidence PDFs for "
        f"{len(data['requirements'])} requirements and "
        f"{sum(len(item['conditions']) for item in data['requirements'])} atomic conditions."
    )
