"""Layout-aware, vendor-neutral document ingestion.

PDFs are parsed into typed elements before chunking. Docling is preferred when
installed; PyMuPDF provides a deterministic local fallback with table
reconstruction and OCR fallback. Other formats emit the same metadata contract.
"""

from __future__ import annotations

import csv
import hashlib
import logging
import os
import re
import sys
from pathlib import Path
from statistics import median
from typing import Any

from app.config import settings
from app.services.document_ir import BoundingBox, DocumentElement, ParsedChunk, ParsedDocument


logger = logging.getLogger("traceaudit.ingestion")

INGESTION_SCHEMA_VERSION = "3.1"
DEFAULT_MAX_CHUNK_CHARS = 3200
MIN_NATIVE_TEXT_CHARS = 24

_DOCLING_CONVERTER: Any = None
_DOCLING_IMPORT_ERROR: str | None = None


def _docling_converter_class() -> Any:
    """Return a usable converter class, caching platform/import failures."""
    global _DOCLING_CONVERTER, _DOCLING_IMPORT_ERROR
    if _DOCLING_CONVERTER is not None:
        return _DOCLING_CONVERTER
    if _DOCLING_IMPORT_ERROR is not None:
        return None
    # Torch can terminate the interpreter (not merely raise ImportError) while
    # loading Docling DLLs on current Windows/Python 3.13 builds. Keep Docling
    # as the preferred Linux backend and use the native Windows layout adapter.
    if os.name == "nt" and sys.version_info >= (3, 13):
        _DOCLING_IMPORT_ERROR = (
            "Docling is disabled in-process on Windows/Python 3.13 because its "
            "Torch DLL can crash the interpreter; PyMuPDF layout is active."
        )
        logger.info(_DOCLING_IMPORT_ERROR)
        return None
    try:
        from docling.document_converter import DocumentConverter

        _DOCLING_CONVERTER = DocumentConverter
        return _DOCLING_CONVERTER
    except Exception as exc:  # Native Torch/model imports can fail by platform.
        _DOCLING_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
        logger.warning("Docling backend unavailable; using PyMuPDF layout: %s", _DOCLING_IMPORT_ERROR)
        return None


def file_sha256(file_path: str) -> str:
    digest = hashlib.sha256()
    with open(file_path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _bbox(value: Any) -> BoundingBox | None:
    if value is None:
        return None
    try:
        x0, y0, x1, y1 = value
        if None in (x0, y0, x1, y1):
            return None
        return BoundingBox(float(x0), float(y0), float(x1), float(y1))
    except (TypeError, ValueError):
        return None


def _bbox_overlap_ratio(left: BoundingBox | None, right: BoundingBox | None) -> float:
    if left is None or right is None:
        return 0.0
    ix0, iy0 = max(left.x0, right.x0), max(left.y0, right.y0)
    ix1, iy1 = min(left.x1, right.x1), min(left.y1, right.y1)
    intersection = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    left_area = max(1.0, (left.x1 - left.x0) * (left.y1 - left.y0))
    return intersection / left_area


def _normalize_cell(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _markdown_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]

    def render(row: list[str]) -> str:
        return "| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |"

    return "\n".join([
        render(padded[0]),
        render(["---"] * width),
        *(render(row) for row in padded[1:]),
    ])


def _serialize_table_parts(
    element: DocumentElement,
    max_chars: int,
) -> list[tuple[str, dict[str, Any]]]:
    rows = [
        [_normalize_cell(cell) for cell in row]
        for row in (element.metadata.get("rows") or [])
    ]
    caption = _normalize_cell(element.metadata.get("caption"))
    context = " > ".join(element.section_path)
    prefix = "\n".join(part for part in (
        f"SECTION: {context}" if context else "",
        f"TABLE: {caption}" if caption else "TABLE",
    ) if part)
    if not rows:
        return [("\n".join(part for part in (prefix, element.text) if part), {})]

    header = rows[0]
    parts: list[tuple[str, dict[str, Any]]] = []
    current = [header]
    row_start = 1
    for row_index, row in enumerate(rows[1:], start=1):
        candidate = current + [row]
        rendered = "\n".join((prefix, _markdown_table(candidate)))
        if len(rendered) > max_chars and len(current) > 1:
            parts.append((
                "\n".join((prefix, _markdown_table(current))),
                {"row_start": row_start, "row_end": row_index - 1},
            ))
            current = [header, row]
            row_start = row_index
        else:
            current = candidate
    if len(current) > 1 or not parts:
        parts.append((
            "\n".join((prefix, _markdown_table(current))),
            {"row_start": row_start, "row_end": max(len(rows) - 1, row_start)},
        ))
    return parts


def build_structure_aware_chunks(
    elements: list[DocumentElement],
    *,
    source_sha256: str,
    parser_backend: str,
    max_chars: int = DEFAULT_MAX_CHUNK_CHARS,
) -> list[ParsedChunk]:
    """Build section, table, and figure chunks without flattening layout."""

    chunks: list[ParsedChunk] = []
    text_elements: list[DocumentElement] = []

    def metadata_for(items: list[DocumentElement], block_type: str) -> dict[str, Any]:
        pages = sorted({item.page_number for item in items if item.page_number is not None})
        return {
            "ingestion_schema_version": INGESTION_SCHEMA_VERSION,
            "source_sha256": source_sha256,
            "parser_backend": parser_backend,
            "block_type": block_type,
            "element_ids": [item.element_id for item in items],
            "page_numbers": pages,
            "section_path": items[-1].section_path if items else [],
            "bounding_boxes": [
                {"page_number": item.page_number, **item.bbox.as_dict()}
                for item in items if item.bbox is not None
            ],
        }

    def flush_text() -> None:
        nonlocal text_elements
        if not text_elements:
            return
        section = " > ".join(text_elements[-1].section_path)
        body = "\n".join(item.text for item in text_elements if item.text.strip())
        chunks.append(ParsedChunk(
            content="\n".join(part for part in (
                f"SECTION: {section}" if section else "",
                body,
            ) if part),
            page_number=text_elements[0].page_number,
            chunk_index=len(chunks),
            metadata=metadata_for(text_elements, "text_section"),
        ))
        text_elements = []

    for element in elements:
        if element.element_type == "table":
            flush_text()
            parts = _serialize_table_parts(element, max_chars)
            for part_index, (content, row_range) in enumerate(parts):
                metadata = metadata_for([element], "table")
                rows = element.metadata.get("rows") or []
                metadata.update({
                    "table": {
                        "caption": element.metadata.get("caption", ""),
                        "headers": rows[0] if rows else [],
                        "row_count": len(rows),
                        **row_range,
                    },
                    "table_part": part_index + 1,
                    "table_parts": len(parts),
                })
                chunks.append(ParsedChunk(
                    content=content,
                    page_number=element.page_number,
                    chunk_index=len(chunks),
                    metadata=metadata,
                ))
            continue

        if element.element_type == "figure":
            flush_text()
            caption = _normalize_cell(element.metadata.get("caption"))
            section = " > ".join(element.section_path)
            metadata = metadata_for([element], "figure")
            parsed_description = _normalize_cell(element.metadata.get("visual_description"))
            metadata["visual_analysis"] = {
                "status": "complete" if parsed_description else "pending",
                "description": parsed_description,
                "caption": caption,
                "source": "databricks-ai-parse" if parsed_description else "",
            }
            chunks.append(ParsedChunk(
                content="\n".join(part for part in (
                    f"SECTION: {section}" if section else "",
                    f"FIGURE: {caption}" if caption else "FIGURE: visual content",
                    element.text,
                    f"VISUAL DESCRIPTION: {parsed_description}" if parsed_description else "",
                ) if part),
                page_number=element.page_number,
                chunk_index=len(chunks),
                metadata=metadata,
            ))
            continue

        if element.element_type in {"heading", "title"} and text_elements:
            flush_text()
        prospective = "\n".join(item.text for item in text_elements + [element])
        section_changed = bool(
            text_elements and text_elements[-1].section_path != element.section_path
        )
        page_changed = bool(
            text_elements
            and text_elements[-1].page_number is not None
            and element.page_number is not None
            and text_elements[-1].page_number != element.page_number
        )
        if text_elements and (len(prospective) > max_chars or section_changed or page_changed):
            flush_text()
        text_elements.append(element)

    flush_text()
    for index, chunk in enumerate(chunks):
        chunk.chunk_index = index
    return chunks


def _heading_level(text: str, max_font: float, body_font: float, is_bold: bool) -> int | None:
    compact = " ".join(text.split())
    if not compact or len(compact) > 180:
        return None
    numbered = bool(re.match(r"^(?:\d+(?:\.\d+)*|[A-Z]\.)\s+\S", compact))
    heading_words = bool(re.match(
        r"^(?:scope|purpose|definitions?|requirements?|test procedure|results?|conclusions?|appendix|section)\b",
        compact,
        re.IGNORECASE,
    ))
    if max_font >= body_font * 1.45:
        return 1
    if max_font >= body_font * 1.18 and (is_bold or numbered or heading_words):
        return 2
    if is_bold and (numbered or heading_words):
        return 3
    return None


def _nearest_caption(
    target: BoundingBox | None,
    text_blocks: list[dict[str, Any]],
    *,
    label: str,
) -> str:
    if target is None:
        return ""
    candidates: list[tuple[float, str]] = []
    pattern = re.compile(rf"^(?:{label}|fig\.?|table)\s*[A-Za-z0-9.-]*[:.-]?\s*", re.I)
    for block in text_blocks:
        text = block["text"].strip()
        box = block["bbox"]
        if not text or box is None:
            continue
        distance = min(abs(box.y0 - target.y1), abs(target.y0 - box.y1))
        overlap = max(0.0, min(box.x1, target.x1) - max(box.x0, target.x0))
        if distance <= 72 and overlap > 0 and pattern.match(text):
            candidates.append((distance, text))
    return min(candidates, default=(0.0, ""), key=lambda item: item[0])[1]


def _pymupdf_elements(file_path: str) -> tuple[list[DocumentElement], int, dict[str, Any]]:
    try:
        import pymupdf
    except ImportError:  # pragma: no cover - old PyMuPDF compatibility
        import fitz as pymupdf

    elements: list[DocumentElement] = []
    diagnostics: dict[str, Any] = {
        "native_text_pages": 0,
        "ocr_pages": 0,
        "ocr_failed_pages": 0,
        "ocr_page_numbers": [],
        "ocr_failed_page_numbers": [],
        "low_text_page_numbers": [],
        "page_native_text_chars": {},
        "tables_detected": 0,
        "figures_detected": 0,
        "decorative_images_filtered": 0,
        "elements_by_type": {},
    }
    section_path: list[str] = []

    with pymupdf.open(file_path) as document:
        for page_index, page in enumerate(document):
            page_number = page_index + 1
            native_text = page.get_text("text").strip()
            diagnostics["page_native_text_chars"][str(page_number)] = len(native_text)
            text_page = None
            used_ocr = False
            if len(native_text) < MIN_NATIVE_TEXT_CHARS:
                diagnostics["low_text_page_numbers"].append(page_number)
                try:
                    text_page = page.get_textpage_ocr(dpi=220, full=True)
                    used_ocr = True
                    diagnostics["ocr_pages"] += 1
                    diagnostics["ocr_page_numbers"].append(page_number)
                except Exception as exc:
                    diagnostics["ocr_failed_pages"] += 1
                    diagnostics["ocr_failed_page_numbers"].append(page_number)
                    logger.info("OCR unavailable for page %s of %s: %s", page_number, file_path, exc)
            else:
                diagnostics["native_text_pages"] += 1

            page_dict = page.get_text("dict", sort=True, textpage=text_page)
            text_blocks: list[dict[str, Any]] = []
            image_blocks: list[dict[str, Any]] = []
            font_sizes: list[float] = []
            for block in page_dict.get("blocks", []):
                block_box = _bbox(block.get("bbox"))
                if block.get("type") == 1:
                    image_blocks.append({"bbox": block_box, "block": block})
                    continue
                if block.get("type") != 0:
                    continue
                lines: list[str] = []
                block_fonts: list[float] = []
                bold = False
                for line in block.get("lines", []):
                    spans = line.get("spans", [])
                    line_text = "".join(str(span.get("text", "")) for span in spans).strip()
                    if line_text:
                        lines.append(line_text)
                    for span in spans:
                        size = float(span.get("size") or 0)
                        if size:
                            block_fonts.append(size)
                            font_sizes.append(size)
                        font = str(span.get("font", "")).lower()
                        bold = bold or "bold" in font or bool(int(span.get("flags") or 0) & 16)
                text = "\n".join(lines).strip()
                if text:
                    text_blocks.append({
                        "text": text,
                        "bbox": block_box,
                        "max_font": max(block_fonts, default=0.0),
                        "bold": bold,
                    })

            body_font = median(font_sizes) if font_sizes else 10.0
            tables: list[dict[str, Any]] = []
            try:
                for table_index, table in enumerate(page.find_tables().tables):
                    rows = [[_normalize_cell(cell) for cell in row] for row in (table.extract() or [])]
                    if not rows or not any(any(cell for cell in row) for row in rows):
                        continue
                    table_box = _bbox(table.bbox)
                    tables.append({
                        "bbox": table_box,
                        "rows": rows,
                        "caption": _nearest_caption(table_box, text_blocks, label="table"),
                        "index": table_index,
                    })
                    diagnostics["tables_detected"] += 1
            except Exception as exc:
                logger.info("Table detection failed on page %s of %s: %s", page_number, file_path, exc)

            page_items: list[tuple[float, int, DocumentElement]] = []
            for block_index, block in enumerate(text_blocks):
                if any(_bbox_overlap_ratio(block["bbox"], table["bbox"]) >= 0.45 for table in tables):
                    continue
                level = _heading_level(block["text"], block["max_font"], body_font, block["bold"])
                if level:
                    section_path = section_path[: level - 1]
                    section_path.append(" ".join(block["text"].split()))
                item = DocumentElement(
                    element_id=f"p{page_number}-text-{block_index}",
                    element_type="heading" if level else "paragraph",
                    text=block["text"],
                    page_number=page_number,
                    bbox=block["bbox"],
                    section_path=list(section_path),
                    metadata={
                        "heading_level": level,
                        "ocr": used_ocr,
                        "max_font_size": round(block["max_font"], 2),
                    },
                )
                page_items.append((block["bbox"].y0 if block["bbox"] else 0.0, 0, item))

            for table in tables:
                item = DocumentElement(
                    element_id=f"p{page_number}-table-{table['index']}",
                    element_type="table",
                    text=_markdown_table(table["rows"]),
                    page_number=page_number,
                    bbox=table["bbox"],
                    section_path=list(section_path),
                    metadata={"rows": table["rows"], "caption": table["caption"]},
                )
                page_items.append((table["bbox"].y0 if table["bbox"] else 0.0, 1, item))

            for figure_index, image in enumerate(image_blocks):
                image_box = image["bbox"]
                page_area = max(1.0, float(page.rect.width * page.rect.height))
                image_area = (
                    max(0.0, image_box.x1 - image_box.x0)
                    * max(0.0, image_box.y1 - image_box.y0)
                    if image_box else 0.0
                )
                if image_area / page_area < 0.01:
                    diagnostics["decorative_images_filtered"] += 1
                    continue
                image_bytes = image["block"].get("image") or b""
                image_hash = hashlib.sha1(image_bytes).hexdigest() if image_bytes else ""
                item = DocumentElement(
                    element_id=f"p{page_number}-figure-{figure_index}",
                    element_type="figure",
                    text="",
                    page_number=page_number,
                    bbox=image_box,
                    section_path=list(section_path),
                    metadata={
                        "caption": _nearest_caption(image_box, text_blocks, label="figure"),
                        "width": image["block"].get("width"),
                        "height": image["block"].get("height"),
                        "extension": image["block"].get("ext"),
                        "image_hash": image_hash,
                        "page_area_fraction": round(image_area / page_area, 4),
                    },
                )
                page_items.append((image_box.y0 if image_box else 0.0, 2, item))
                diagnostics["figures_detected"] += 1

            page_items.sort(key=lambda item: (item[0], item[1]))
            elements.extend(item[2] for item in page_items)
        page_count = len(document)

    # Repeated full-page assets are commonly templates, watermarks, or blank
    # placeholders. Retain unique technical figures while suppressing these
    # document decorations from retrieval and vision calls.
    figure_hashes: dict[str, int] = {}
    for element in elements:
        if element.element_type == "figure" and element.metadata.get("image_hash"):
            image_hash = element.metadata["image_hash"]
            figure_hashes[image_hash] = figure_hashes.get(image_hash, 0) + 1
    repeated_threshold = max(3, page_count // 4)
    retained: list[DocumentElement] = []
    for element in elements:
        image_hash = element.metadata.get("image_hash") if element.element_type == "figure" else ""
        if image_hash and figure_hashes.get(image_hash, 0) > repeated_threshold:
            diagnostics["decorative_images_filtered"] += 1
            diagnostics["figures_detected"] -= 1
            continue
        retained.append(element)
    elements = retained

    for element in elements:
        counts = diagnostics["elements_by_type"]
        counts[element.element_type] = counts.get(element.element_type, 0) + 1
    diagnostics.update({
        "page_count": page_count,
        "ocr_coverage": diagnostics["ocr_pages"] / page_count if page_count else 0.0,
        "table_structure_coverage": 1.0 if diagnostics["tables_detected"] else None,
    })
    return elements, page_count, diagnostics


def _docling_elements(file_path: str) -> tuple[list[DocumentElement], int, dict[str, Any]]:
    """Adapt Docling output without leaking its classes beyond this boundary."""

    converter_class = _docling_converter_class()
    if converter_class is None:
        raise RuntimeError(_DOCLING_IMPORT_ERROR or "Docling is unavailable")
    document = converter_class().convert(file_path).document
    elements: list[DocumentElement] = []
    section_path: list[str] = []
    counts: dict[str, int] = {}
    for index, (item, level) in enumerate(document.iterate_items()):
        label = str(getattr(item, "label", "text")).lower()
        if "table" in label:
            element_type = "table"
        elif "picture" in label or "figure" in label:
            element_type = "figure"
        elif "title" in label:
            element_type = "title"
        elif "heading" in label or "section_header" in label:
            element_type = "heading"
        elif "list" in label:
            element_type = "list_item"
        elif "formula" in label:
            element_type = "formula"
        else:
            element_type = "paragraph"

        text = str(getattr(item, "text", "") or "").strip()
        rows: list[list[str]] = []
        if element_type == "table":
            try:
                frame = item.export_to_dataframe(document)
                rows = [list(map(_normalize_cell, frame.columns.tolist()))]
                rows.extend([
                    list(map(_normalize_cell, row))
                    for row in frame.itertuples(index=False, name=None)
                ])
                text = _markdown_table(rows)
            except Exception:
                try:
                    text = item.export_to_markdown(document).strip()
                except Exception:
                    pass

        if element_type in {"title", "heading"} and text:
            heading_level = max(1, min(int(level or 1), 6))
            section_path = section_path[: heading_level - 1]
            section_path.append(" ".join(text.split()))

        provenance = list(getattr(item, "prov", None) or [])
        first_prov = provenance[0] if provenance else None
        page_number = getattr(first_prov, "page_no", None)
        raw_box = getattr(first_prov, "bbox", None)
        box = _bbox((
            getattr(raw_box, "l", None),
            getattr(raw_box, "t", None),
            getattr(raw_box, "r", None),
            getattr(raw_box, "b", None),
        ) if raw_box is not None else None)
        caption = ""
        if element_type in {"table", "figure"}:
            try:
                caption = str(item.caption_text(document) or "").strip()
            except Exception:
                pass
        metadata: dict[str, Any] = {
            "docling_label": label,
            "hierarchy_level": level,
            "caption": caption,
        }
        if rows:
            metadata["rows"] = rows
        elements.append(DocumentElement(
            element_id=f"docling-{index}",
            element_type=element_type,  # type: ignore[arg-type]
            text=text,
            page_number=int(page_number) if page_number is not None else None,
            bbox=box,
            section_path=list(section_path),
            metadata=metadata,
        ))
        counts[element_type] = counts.get(element_type, 0) + 1

    pages = getattr(document, "pages", {}) or {}
    page_count = len(pages) or max((item.page_number or 0 for item in elements), default=0)
    return elements, page_count, {
        "page_count": page_count,
        "elements_by_type": counts,
        "tables_detected": counts.get("table", 0),
        "figures_detected": counts.get("figure", 0),
        "ocr_pages": None,
        "ocr_coverage": None,
        "table_structure_coverage": 1.0 if counts.get("table") else None,
    }


def parse_pdf_document(file_path: str, backend: str | None = None) -> ParsedDocument:
    requested = (backend or settings.TRACEAUDIT_DOCUMENT_PARSER).strip().lower()
    if requested in {"databricks", "databricks-auto"}:
        try:
            from app.services.databricks_document_ai import parse_document as parse_with_databricks

            return parse_with_databricks(file_path)
        except Exception:
            if requested == "databricks":
                raise
            logger.exception(
                "Databricks document parsing failed for %s; falling back to local layout parsing",
                file_path,
            )
            requested = "auto"
    sha256 = file_sha256(file_path)
    converter_class = _docling_converter_class() if requested in {"auto", "docling"} else None
    use_docling = converter_class is not None
    if requested == "docling" and not use_docling:
        raise RuntimeError(
            "TRACEAUDIT_DOCUMENT_PARSER=docling but Docling is not usable in this runtime. "
            f"{_DOCLING_IMPORT_ERROR or 'Install backend/requirements-document-ai.txt.'}"
        )

    parser_backend = "docling" if use_docling else "pymupdf-layout"
    try:
        if use_docling:
            elements, page_count, diagnostics = _docling_elements(file_path)
        else:
            elements, page_count, diagnostics = _pymupdf_elements(file_path)
    except Exception:
        if requested != "auto" or not use_docling:
            raise
        logger.exception("Docling failed for %s; falling back to PyMuPDF", file_path)
        parser_backend = "pymupdf-layout"
        elements, page_count, diagnostics = _pymupdf_elements(file_path)
        diagnostics["fallback_from"] = "docling"

    chunks = build_structure_aware_chunks(
        elements,
        source_sha256=sha256,
        parser_backend=parser_backend,
    )
    diagnostics.update({
        "parser_backend": parser_backend,
        "ingestion_schema_version": INGESTION_SCHEMA_VERSION,
        "source_sha256": sha256,
        "chunk_count": len(chunks),
    })
    for chunk in chunks:
        chunk.metadata["document_diagnostics"] = diagnostics
    return ParsedDocument(
        source_path=file_path,
        source_sha256=sha256,
        parser_backend=parser_backend,
        page_count=page_count,
        elements=elements,
        chunks=chunks,
        diagnostics=diagnostics,
    )


def _plain_document(
    file_path: str,
    elements: list[DocumentElement],
    *,
    parser_backend: str,
) -> ParsedDocument:
    sha256 = file_sha256(file_path)
    chunks = build_structure_aware_chunks(
        elements,
        source_sha256=sha256,
        parser_backend=parser_backend,
    )
    counts = {
        kind: sum(item.element_type == kind for item in elements)
        for kind in sorted({item.element_type for item in elements})
    }
    diagnostics = {
        "parser_backend": parser_backend,
        "ingestion_schema_version": INGESTION_SCHEMA_VERSION,
        "source_sha256": sha256,
        "page_count": 0,
        "chunk_count": len(chunks),
        "elements_by_type": counts,
        "tables_detected": counts.get("table", 0),
        "figures_detected": counts.get("figure", 0),
    }
    for chunk in chunks:
        chunk.metadata["document_diagnostics"] = diagnostics
    return ParsedDocument(file_path, sha256, parser_backend, 0, elements, chunks, diagnostics)


def parse_docx_document(file_path: str) -> ParsedDocument:
    from docx import Document

    document = Document(file_path)
    elements: list[DocumentElement] = []
    section_path: list[str] = []
    for index, paragraph in enumerate(document.paragraphs):
        text = paragraph.text.strip()
        if not text:
            continue
        style = paragraph.style.name if paragraph.style else ""
        is_heading = style.startswith("Heading")
        level_match = re.search(r"(\d+)$", style)
        level = int(level_match.group(1)) if level_match else 1
        if is_heading:
            section_path = section_path[: level - 1]
            section_path.append(text)
        elements.append(DocumentElement(
            element_id=f"paragraph-{index}",
            element_type="heading" if is_heading else "paragraph",
            text=text,
            page_number=None,
            section_path=list(section_path),
            metadata={"style": style, "heading_level": level if is_heading else None},
        ))
    for index, table in enumerate(document.tables):
        rows = [[_normalize_cell(cell.text) for cell in row.cells] for row in table.rows]
        elements.append(DocumentElement(
            element_id=f"table-{index}",
            element_type="table",
            text=_markdown_table(rows),
            page_number=None,
            section_path=list(section_path),
            metadata={"rows": rows, "caption": ""},
        ))
    return _plain_document(file_path, elements, parser_backend="python-docx-layout")


def parse_xlsx_document(file_path: str) -> ParsedDocument:
    from openpyxl import load_workbook

    workbook = load_workbook(file_path, read_only=True, data_only=True)
    elements: list[DocumentElement] = []
    for sheet_name in workbook.sheetnames:
        sheet = workbook[sheet_name]
        rows = [[_normalize_cell(cell) for cell in row] for row in sheet.iter_rows(values_only=True)]
        rows = [row for row in rows if any(row)]
        if not rows:
            continue
        elements.append(DocumentElement(
            element_id=f"sheet-{len(elements)}",
            element_type="table",
            text=_markdown_table(rows),
            page_number=None,
            section_path=[sheet_name],
            metadata={"rows": rows, "caption": sheet_name, "sheet": sheet_name},
        ))
    workbook.close()
    return _plain_document(file_path, elements, parser_backend="openpyxl-layout")


def parse_csv_document(file_path: str) -> ParsedDocument:
    with open(file_path, "r", encoding="utf-8-sig", newline="") as stream:
        rows = [[_normalize_cell(cell) for cell in row] for row in csv.reader(stream)]
    rows = [row for row in rows if any(row)]
    elements = [DocumentElement(
        element_id="csv-table-0",
        element_type="table",
        text=_markdown_table(rows),
        page_number=None,
        metadata={"rows": rows, "caption": Path(file_path).stem},
    )] if rows else []
    return _plain_document(file_path, elements, parser_backend="csv-layout")


def parse_document_with_metadata(file_path: str) -> ParsedDocument:
    ext = Path(file_path).suffix.lower()
    requested = settings.TRACEAUDIT_DOCUMENT_PARSER.strip().lower()
    if requested in {"databricks", "databricks-auto"} and ext != ".pdf":
        from app.services.databricks_document_ai import SUPPORTED_SUFFIXES, parse_document as parse_with_databricks

        if ext in SUPPORTED_SUFFIXES:
            try:
                return parse_with_databricks(file_path)
            except Exception:
                if requested == "databricks":
                    raise
                logger.exception(
                    "Databricks document parsing failed for %s; falling back to a local parser",
                    file_path,
                )
    parsers = {
        ".pdf": parse_pdf_document,
        ".docx": parse_docx_document,
        ".xlsx": parse_xlsx_document,
        ".csv": parse_csv_document,
    }
    parser = parsers.get(ext)
    if parser is None:
        raise ValueError(f"Unsupported file type: {ext}")
    return parser(file_path)


# Backward-compatible APIs used by the benchmark runners.
def parse_pdf(file_path: str) -> list[ParsedChunk]:
    return parse_pdf_document(file_path).chunks


def parse_docx(file_path: str) -> list[ParsedChunk]:
    return parse_docx_document(file_path).chunks


def parse_xlsx(file_path: str) -> list[ParsedChunk]:
    return parse_xlsx_document(file_path).chunks


def parse_csv(file_path: str) -> list[ParsedChunk]:
    return parse_csv_document(file_path).chunks


def parse_document(file_path: str) -> list[ParsedChunk]:
    return parse_document_with_metadata(file_path).chunks
