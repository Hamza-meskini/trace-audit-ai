from __future__ import annotations

import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.document_ir import DocumentElement
from app.schemas.claim import _isolate_relevant_passage
from app.schemas.contract import parse_requirement_contract
from app.services.ingestion import (
    INGESTION_SCHEMA_VERSION,
    build_structure_aware_chunks,
    parse_pdf_document,
)


def _make_layout_pdf(path: Path) -> None:
    import pymupdf

    document = pymupdf.open()
    page = document.new_page(width=612, height=792)
    page.insert_text((60, 70), "4 TEST RESULTS", fontsize=18, fontname="helv")
    page.insert_text((60, 105), "Measured electrical isolation results follow.", fontsize=10)

    xs = [60, 250, 400, 550]
    ys = [140, 175, 210]
    for x in xs:
        page.draw_line((x, ys[0]), (x, ys[-1]))
    for y in ys:
        page.draw_line((xs[0], y), (xs[-1], y))
    labels = [
        (70, 162, "Parameter"), (260, 162, "Result"), (410, 162, "Requirement"),
        (70, 197, "Isolation"), (260, 197, "1200 Ohm/V"), (410, 197, ">= 500 Ohm/V"),
    ]
    for x, y, text in labels:
        page.insert_text((x, y), text, fontsize=9)

    page2 = document.new_page(width=612, height=792)
    page2.insert_text((60, 70), "APPENDIX A PHOTOGRAPHS", fontsize=18)
    page2.insert_text((60, 105), "Figure A-1: Post-impact battery enclosure", fontsize=10)
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 120), False)
    pixmap.clear_with(0x80A0C0)
    page2.insert_image(pymupdf.Rect(60, 125, 460, 365), pixmap=pixmap)
    document.save(path)
    document.close()


def test_pdf_ingestion_preserves_tables_figures_and_provenance(tmp_path: Path) -> None:
    pdf_path = tmp_path / "layout.pdf"
    _make_layout_pdf(pdf_path)

    parsed = parse_pdf_document(str(pdf_path), backend="pymupdf")

    assert parsed.page_count == 2
    assert parsed.diagnostics["tables_detected"] >= 1
    assert parsed.diagnostics["figures_detected"] >= 1
    assert {chunk.page_number for chunk in parsed.chunks} == {1, 2}

    table = next(chunk for chunk in parsed.chunks if chunk.metadata["block_type"] == "table")
    assert "| Parameter | Result | Requirement |" in table.content
    assert "1200 Ohm/V" in table.content
    assert table.metadata["table"]["headers"] == ["Parameter", "Result", "Requirement"]
    assert table.metadata["bounding_boxes"]
    assert table.metadata["source_sha256"] == parsed.source_sha256
    assert table.metadata["ingestion_schema_version"] == INGESTION_SCHEMA_VERSION

    figure = next(chunk for chunk in parsed.chunks if chunk.metadata["block_type"] == "figure")
    assert "Figure A-1" in figure.content
    assert figure.metadata["visual_analysis"]["status"] == "pending"
    assert figure.metadata["bounding_boxes"]


def test_long_tables_repeat_headers_in_every_chunk() -> None:
    rows = [["Parameter", "Result", "Limit"]]
    rows.extend([[f"Measurement {index}", str(index), ">= 0"] for index in range(40)])
    element = DocumentElement(
        element_id="table-1",
        element_type="table",
        text="",
        page_number=7,
        section_path=["Results"],
        metadata={"rows": rows, "caption": "Measurements"},
    )

    chunks = build_structure_aware_chunks(
        [element],
        source_sha256="abc",
        parser_backend="test",
        max_chars=350,
    )

    assert len(chunks) > 1
    assert all("| Parameter | Result | Limit |" in chunk.content for chunk in chunks)
    assert all(chunk.metadata["block_type"] == "table" for chunk in chunks)
    assert chunks[-1].metadata["table"]["row_end"] == 40


def test_relevance_isolation_preserves_complete_table_rows_and_headers() -> None:
    content = (
        "TABLE\n| Parameter | Result | Requirement |\n"
        "| Isolation | 1200 Ohm/V | >= 500 Ohm/V |\n"
        "| Voltage | 350 V | <= 400 V |"
    )
    contract = parse_requirement_contract(
        req_code="R-ISO",
        title="Electrical isolation",
        description="Electrical isolation shall be at least 500 Ohm/V.",
        structured_conditions=[{
            "condition_id": "C1",
            "description": "Isolation is at least 500 Ohm/V",
            "parameter": "isolation",
            "operator": ">=",
            "threshold": 500,
            "unit": "Ohm/V",
        }],
    )
    isolated = _isolate_relevant_passage(content, contract, {"block_type": "table"})
    assert isolated == content
    assert "| Parameter |" in isolated
    assert "| Voltage |" in isolated
