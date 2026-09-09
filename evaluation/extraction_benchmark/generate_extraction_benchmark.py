"""Generate eight visually varied, natural-language requirement PDFs."""

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

from benchmark_data import DOCUMENTS, materialize_ground_truth


ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "documents"
ASSETS = ROOT / "assets"
PAGE_W, PAGE_H = A4
NAVY = colors.HexColor("#16324F")
BLUE = colors.HexColor("#1769AA")
INK = colors.HexColor("#172033")
MUTED = colors.HexColor("#5E6B7A")
LINE = colors.HexColor("#C9D3DF")
PAPER = colors.HexColor("#F5F8FB")
RED = "#C53030"


def font(size: int, bold: bool = False):
    choices = [
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/calibrib.ttf" if bold else "C:/Windows/Fonts/calibri.ttf"),
    ]
    for path in choices:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def header(pdf: canvas.Canvas, title: str, page: int) -> None:
    pdf.setFillColor(NAVY)
    pdf.rect(0, PAGE_H - 24 * mm, PAGE_W, 24 * mm, fill=1, stroke=0)
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(16 * mm, PAGE_H - 15 * mm, title)
    pdf.setFont("Helvetica", 8)
    pdf.drawRightString(PAGE_W - 16 * mm, PAGE_H - 15 * mm, f"Controlled copy | Page {page} of 4")
    pdf.setStrokeColor(LINE)
    pdf.line(16 * mm, 14 * mm, PAGE_W - 16 * mm, 14 * mm)
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 7)
    pdf.drawString(16 * mm, 9 * mm, "Synthetic benchmark document - no real product or certification claim")


def paragraph(pdf: canvas.Canvas, text: str, x: float, y: float, width: float, style: ParagraphStyle) -> float:
    value = Paragraph(text, style)
    _, height = value.wrap(width, PAGE_H)
    value.drawOn(pdf, x, y - height)
    return y - height


def requirement_block(pdf: canvas.Canvas, requirement: dict, x: float, y: float, width: float) -> float:
    title_style = ParagraphStyle("req-title", fontName="Helvetica-Bold", fontSize=10, leading=13, textColor=NAVY)
    body_style = ParagraphStyle("req-body", fontName="Helvetica", fontSize=9.5, leading=14, textColor=INK)
    pdf.setFillColor(PAPER)
    pdf.roundRect(x, y - 31 * mm, width, 29 * mm, 3 * mm, fill=1, stroke=0)
    y = paragraph(pdf, f"{requirement['requirement_id']}  |  {requirement['title']}", x + 5 * mm, y - 5 * mm, width - 10 * mm, title_style)
    paragraph(pdf, requirement["requirement_text"], x + 5 * mm, y - 2 * mm, width - 10 * mm, body_style)
    return y - 29 * mm


def make_scan(document: dict, output: Path) -> None:
    image = Image.new("RGB", (1654, 2339), "#F7F4EA")
    draw = ImageDraw.Draw(image)
    draw.text((120, 95), document["title"], fill="#26384A", font=font(42, True))
    draw.text((120, 165), "Legacy controlled requirement sheet", fill="#66717D", font=font(26))
    y = 300
    for item in document["requirements"][:3]:
        draw.rounded_rectangle((105, y - 20, 1545, y + 420), radius=18, outline="#7F8C8D", width=3, fill="#FBFAF5")
        draw.text((140, y + 20), f"{item['requirement_id']}  {item['title']}", fill="#1F3448", font=font(31, True))
        wrapped = textwrap.wrap(item["requirement_text"], width=76)
        for line_index, line in enumerate(wrapped):
            draw.text((140, y + 90 + line_index * 48), line, fill="#1B1F23", font=font(29))
        y += 520
    image.save(output, quality=92)


def make_normative_figure(document: dict, output: Path) -> None:
    item = document["requirements"][5]
    image = Image.new("RGB", (1500, 920), "white")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((35, 35, 1465, 885), radius=30, outline="#19324D", width=7, fill="#F8FBFE")
    draw.text((80, 70), f"Installation control - {item['requirement_id']}", fill="#16324F", font=font(38, True))
    # A deliberately meaningful schematic, with the normative statement embedded in the raster.
    draw.rounded_rectangle((100, 240, 500, 570), radius=25, outline="#1769AA", width=8, fill="#DCEEFF")
    draw.rounded_rectangle((1000, 240, 1400, 570), radius=25, outline="#C53030", width=8, fill="#FFE4E4")
    draw.text((220, 375), "INPUT", fill="#1769AA", font=font(42, True))
    draw.text((1100, 375), "OUTPUT", fill="#C53030", font=font(42, True))
    draw.line((500, 405, 990, 405), fill="#27364A", width=16)
    draw.polygon([(990, 405), (925, 365), (925, 445)], fill="#27364A")
    wrapped = textwrap.wrap(item["requirement_text"], width=88)
    y = 660
    for line in wrapped:
        draw.text((85, y), line, fill="#172033", font=font(28, True))
        y += 42
    image.save(output, quality=94)


def make_secondary_figure(document: dict, output: Path) -> None:
    image = Image.new("RGB", (1300, 520), "white")
    draw = ImageDraw.Draw(image)
    draw.text((50, 35), "Interface context (informative)", fill="#16324F", font=font(34, True))
    labels = ["Sensor", "Controller", "Actuator"]
    for index, label in enumerate(labels):
        left = 70 + index * 410
        draw.rounded_rectangle((left, 180, left + 300, 380), radius=22, outline="#1769AA", width=6, fill="#EAF4FC")
        draw.text((left + 65, 255), label, fill="#16324F", font=font(30, True))
        if index < 2:
            draw.line((left + 300, 280, left + 400, 280), fill="#536779", width=8)
    image.save(output, quality=94)


def draw_cover(pdf: canvas.Canvas, document: dict) -> None:
    pdf.setFillColor(NAVY)
    pdf.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 24)
    pdf.drawString(22 * mm, PAGE_H - 65 * mm, "NOVA PROGRAM")
    title_style = ParagraphStyle("cover", fontName="Helvetica-Bold", fontSize=27, leading=34, textColor=colors.white)
    paragraph(pdf, document["title"], 22 * mm, PAGE_H - 90 * mm, PAGE_W - 44 * mm, title_style)
    pdf.setFont("Helvetica", 11)
    pdf.drawString(22 * mm, 55 * mm, f"Domain: {document['domain']}")
    pdf.drawString(22 * mm, 45 * mm, "Revision A | Synthetic extraction benchmark")
    pdf.showPage()


def draw_text_page(pdf: canvas.Canvas, document: dict, scan_path: Path) -> None:
    if document["scanned_page"]:
        pdf.drawImage(str(scan_path), 0, 0, PAGE_W, PAGE_H, preserveAspectRatio=False, mask="auto")
        pdf.showPage()
        return
    header(pdf, document["title"], 2)
    pdf.setFillColor(NAVY)
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(16 * mm, PAGE_H - 39 * mm, "2. Functional requirements")
    y = PAGE_H - 50 * mm
    y = requirement_block(pdf, document["requirements"][0], 16 * mm, y, PAGE_W - 32 * mm)
    gap = 7 * mm
    column_width = (PAGE_W - 38 * mm) / 2
    requirement_block(pdf, document["requirements"][1], 16 * mm, y - gap, column_width)
    requirement_block(pdf, document["requirements"][2], 22 * mm + column_width, y - gap, column_width)
    pdf.showPage()


def draw_table_page(pdf: canvas.Canvas, document: dict) -> None:
    header(pdf, document["title"], 3)
    pdf.setFillColor(NAVY)
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(16 * mm, PAGE_H - 39 * mm, "3. Verification-oriented requirements")
    style = ParagraphStyle("cell", fontName="Helvetica", fontSize=8.2, leading=10.5, textColor=INK)
    heading = ParagraphStyle("cell-head", fontName="Helvetica-Bold", fontSize=8.2, leading=10, textColor=colors.white)
    rows = [[Paragraph("Identifier", heading), Paragraph("Requirement title", heading), Paragraph("Normative requirement", heading)]]
    for item in document["requirements"][3:5]:
        rows.append([Paragraph(item["requirement_id"], style), Paragraph(item["title"], style), Paragraph(item["requirement_text"], style)])
    table = Table(rows, colWidths=[31 * mm, 39 * mm, 108 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY), ("GRID", (0, 0), (-1, -1), 0.6, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("BACKGROUND", (0, 1), (-1, -1), colors.white),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    width, height = table.wrap(PAGE_W - 32 * mm, PAGE_H)
    table.drawOn(pdf, 16 * mm, PAGE_H - 50 * mm - height)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.setFillColor(NAVY)
    pdf.drawString(16 * mm, PAGE_H - 60 * mm - height, "Applicability notes")
    note_rows = [
        ["Context", "Interpretation"],
        ["Unless a requirement states otherwise", "Limits apply to the complete production-intent assembly."],
        ["Use of shall", "The complete sentence is normative; table columns do not split obligations."],
    ]
    notes = Table(note_rows, colWidths=[62 * mm, 116 * mm])
    notes.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCE8F2")), ("GRID", (0, 0), (-1, -1), 0.5, LINE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8), ("LEADING", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    _, notes_height = notes.wrap(PAGE_W - 32 * mm, PAGE_H)
    notes.drawOn(pdf, 16 * mm, PAGE_H - 72 * mm - height - notes_height)
    pdf.showPage()


def draw_figure_page(pdf: canvas.Canvas, document: dict, figure: Path, secondary: Path | None) -> None:
    header(pdf, document["title"], 4)
    pdf.setFillColor(NAVY)
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(16 * mm, PAGE_H - 39 * mm, "4. Normative installation figure")
    pdf.drawImage(str(figure), 16 * mm, PAGE_H - 155 * mm, PAGE_W - 32 * mm, 101 * mm, preserveAspectRatio=True, anchor="c", mask="auto")
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 8)
    pdf.drawString(16 * mm, PAGE_H - 160 * mm, "Figure 4-1. Colored geometry and embedded annotation are normative.")
    if secondary is not None:
        pdf.drawImage(str(secondary), 25 * mm, 28 * mm, PAGE_W - 50 * mm, 56 * mm, preserveAspectRatio=True, anchor="c", mask="auto")
    else:
        pdf.setFillColor(PAPER)
        pdf.roundRect(16 * mm, 28 * mm, PAGE_W - 32 * mm, 50 * mm, 3 * mm, fill=1, stroke=0)
        pdf.setFillColor(INK)
        pdf.setFont("Helvetica", 9)
        pdf.drawString(22 * mm, 64 * mm, "Engineering note")
        pdf.drawString(22 * mm, 54 * mm, "The figure annotation forms part of the controlled requirement baseline.")
        pdf.drawString(22 * mm, 44 * mm, "Color, direction, sequence, and geometric relationships must be interpreted visually.")
    pdf.showPage()


def generate() -> dict:
    DOCS.mkdir(parents=True, exist_ok=True)
    ASSETS.mkdir(parents=True, exist_ok=True)
    for doc_index, document in enumerate(DOCUMENTS, 1):
        stem = Path(document["filename"]).stem
        scan_path = ASSETS / f"{stem}_scan.jpg"
        figure_path = ASSETS / f"{stem}_normative.jpg"
        secondary_path = ASSETS / f"{stem}_context.jpg"
        if document["scanned_page"]:
            make_scan(document, scan_path)
        make_normative_figure(document, figure_path)
        if document["secondary_figure"]:
            make_secondary_figure(document, secondary_path)

        pdf = canvas.Canvas(str(DOCS / document["filename"]), pagesize=A4, pageCompression=1)
        pdf.setTitle(document["title"])
        pdf.setAuthor("TraceAudit synthetic benchmark")
        draw_cover(pdf, document)
        draw_text_page(pdf, document, scan_path)
        draw_table_page(pdf, document)
        draw_figure_page(pdf, document, figure_path, secondary_path if document["secondary_figure"] else None)
        pdf.save()

    truth = materialize_ground_truth()
    (ROOT / "ground_truth.json").write_text(json.dumps(truth, indent=2), encoding="utf-8")
    return truth


if __name__ == "__main__":
    dataset = generate()
    print(f"Generated {len(dataset['documents'])} PDFs, {len(dataset['requirements'])} requirements, and {sum(len(item['conditions']) for item in dataset['requirements'])} atomic conditions.")

