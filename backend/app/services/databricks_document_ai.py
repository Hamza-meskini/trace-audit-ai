"""Databricks document parsing and focused evidence extraction adapters.

This module is deliberately optional. Imports of the Databricks clients happen
inside call sites so local parsing remains usable without those dependencies.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
from html.parser import HTMLParser
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.config import settings
from app.services.document_ir import BoundingBox, DocumentElement, ParsedDocument


logger = logging.getLogger("traceaudit.databricks_document_ai")
PARSE_VERSION = "2.0"
EXTRACT_VERSION = "2.1"
PREP_SEARCH_VERSION = "2.0"
PARSER_BACKEND = f"databricks-ai-parse-{PARSE_VERSION}"
SUPPORTED_SUFFIXES = {
    ".pdf", ".doc", ".docx", ".ppt", ".pptx",
    ".jpg", ".jpeg", ".png", ".tif", ".tiff",
}
VOLUME_PATH_RE = re.compile(r"^/Volumes/[^/]+/[^/]+/[^/]+(?:/.*)?$")


class _TableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._row is not None and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(self._row):
                self.rows.append(self._row)
            self._row = None


def _table_rows(value: str) -> list[list[str]]:
    parser = _TableHTMLParser()
    try:
        parser.feed(value)
    except Exception:
        return []
    return parser.rows


def _sha256(file_path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(file_path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _workspace_hostname() -> str:
    raw = (
        settings.DATABRICKS_HOST
        or os.getenv("DATABRICKS_HOST", "")
        or settings.DATABRICKS_BASE_URL
        or os.getenv("DATABRICKS_BASE_URL", "")
    ).strip()
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if not parsed.hostname:
        raise RuntimeError("Databricks document parsing requires DATABRICKS_HOST or DATABRICKS_BASE_URL")
    return parsed.hostname


def _sql_config() -> tuple[str, str, str]:
    host = _workspace_hostname()
    token = settings.effective_databricks_token
    warehouse_id = settings.DATABRICKS_SQL_WAREHOUSE_ID or os.getenv("DATABRICKS_SQL_WAREHOUSE_ID", "")
    if not token:
        raise RuntimeError("Databricks document AI requires DATABRICKS_TOKEN")
    if not warehouse_id:
        raise RuntimeError("Databricks document AI requires DATABRICKS_SQL_WAREHOUSE_ID")
    return host, token, warehouse_id


def _document_ai_config() -> tuple[str, str, str, str]:
    host, token, warehouse_id = _sql_config()
    volume = (settings.DATABRICKS_DOCUMENT_VOLUME or os.getenv("DATABRICKS_DOCUMENT_VOLUME", "")).rstrip("/")
    if not VOLUME_PATH_RE.fullmatch(volume) or ".." in volume.split("/"):
        raise RuntimeError(
            "DATABRICKS_DOCUMENT_VOLUME must look like /Volumes/<catalog>/<schema>/<volume>[/folder]"
        )
    return host, token, warehouse_id, volume


def _cache_path(source_sha256: str) -> Path:
    configured = settings.DATABRICKS_DOCUMENT_CACHE_DIR or os.getenv("DATABRICKS_DOCUMENT_CACHE_DIR", "")
    root = Path(configured).expanduser() if configured else Path(__file__).resolve().parents[2] / ".cache" / "databricks_document_ai"
    return root.resolve() / f"{source_sha256}-parse-{PARSE_VERSION}-figures.json"


def _prep_cache_path(source_sha256: str) -> Path:
    configured = settings.DATABRICKS_DOCUMENT_CACHE_DIR or os.getenv("DATABRICKS_DOCUMENT_CACHE_DIR", "")
    root = Path(configured).expanduser() if configured else Path(__file__).resolve().parents[2] / ".cache" / "databricks_document_ai"
    return root.resolve() / f"{source_sha256}-prep-search-{PREP_SEARCH_VERSION}.json"


def _load_cached_parse(path: Path, source_sha256: str) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("source_sha256") != source_sha256 or payload.get("parse_version") != PARSE_VERSION:
        return None
    result = payload.get("result")
    return result if isinstance(result, dict) else None


def _save_cached_parse(path: Path, source_sha256: str, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps({
        "source_sha256": source_sha256,
        "parse_version": PARSE_VERSION,
        "figure_descriptions": True,
        "result": result,
    }, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _load_cached_prep(path: Path, source_sha256: str) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("source_sha256") != source_sha256 or payload.get("version") != PREP_SEARCH_VERSION:
        return None
    result = payload.get("result")
    return result if isinstance(result, dict) else None


def _save_cached_prep(path: Path, source_sha256: str, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps({
        "source_sha256": source_sha256,
        "version": PREP_SEARCH_VERSION,
        "result": result,
    }, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _databricks_clients() -> tuple[Any, Any]:
    try:
        from databricks import sql
        from databricks.sdk import WorkspaceClient
    except ImportError as exc:
        raise RuntimeError(
            "Databricks document parsing dependencies are missing; install backend/requirements.txt"
        ) from exc
    return sql, WorkspaceClient


def _run_remote_parse(file_path: str, source_sha256: str) -> dict[str, Any]:
    sql_module, workspace_client_cls = _databricks_clients()
    host, token, warehouse_id, volume = _document_ai_config()
    suffix = Path(file_path).suffix.lower()
    remote_input_dir = f"{volume}/auditrace-document-ai-inputs"
    remote_image_dir = f"{volume}/auditrace-document-ai-images/{source_sha256}"
    remote_file = f"{remote_input_dir}/{source_sha256}{suffix}"
    workspace = workspace_client_cls(host=f"https://{host}", token=token)
    uploaded = False
    try:
        workspace.files.create_directory(remote_input_dir)
        workspace.files.create_directory(remote_image_dir)
        workspace.files.upload_from(remote_file, file_path, overwrite=True)
        uploaded = True
        query = """
        WITH source_document AS (
          SELECT content FROM read_files(?, format => 'binaryFile')
        )
        SELECT to_json(ai_parse_document(
          content,
          map(
            'version', '2.0',
            'imageOutputPath', ?,
            'descriptionElementTypes', '*'
          )
        )) AS parsed_json
        FROM source_document
        """
        with sql_module.connect(
            server_hostname=host,
            http_path=f"/sql/1.0/warehouses/{warehouse_id}",
            access_token=token,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, [remote_file, remote_image_dir])
                row = cursor.fetchone()
        if not row or not row[0]:
            raise RuntimeError("Databricks ai_parse_document returned no document")
        result = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        if not isinstance(result, dict) or not isinstance(result.get("document"), dict):
            raise RuntimeError("Databricks ai_parse_document returned an unexpected schema")
        return result
    finally:
        if uploaded:
            try:
                workspace.files.delete(remote_file)
            except Exception as exc:
                logger.warning("Could not remove temporary Databricks upload %s: %s", remote_file, exc)


def _run_remote_prep_search(parsed_result: dict[str, Any]) -> dict[str, Any]:
    """Turn ai_parse_document VARIANT output into Databricks search chunks."""
    sql_module, _ = _databricks_clients()
    host, token, warehouse_id = _sql_config()
    query = """
    SELECT to_json(ai_prep_search(
      parse_json(?),
      map('version', '2.0')
    )) AS prepared_json
    """
    with sql_module.connect(
        server_hostname=host,
        http_path=f"/sql/1.0/warehouses/{warehouse_id}",
        access_token=token,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, [json.dumps(parsed_result, ensure_ascii=False)])
            row = cursor.fetchone()
    if not row or not row[0]:
        raise RuntimeError("Databricks ai_prep_search returned no document")
    result = json.loads(row[0]) if isinstance(row[0], str) else row[0]
    if not isinstance(result, dict):
        raise RuntimeError("Databricks ai_prep_search returned an unexpected schema")
    return result


def _adapt_prep_search_result(
    result: dict[str, Any],
    *,
    source_sha256: str,
    ingestion_schema_version: str,
) -> list[Any]:
    """Map ai_prep_search contents to the vendor-neutral ParsedChunk shape."""
    from app.services.document_ir import ParsedChunk

    document = result.get("document") if isinstance(result.get("document"), dict) else {}
    raw_contents = document.get("contents") or result.get("contents") or []
    chunks: list[ParsedChunk] = []
    for position, raw in enumerate(raw_contents):
        if not isinstance(raw, dict):
            continue
        retrieve_text = str(raw.get("chunk_to_retrieve") or "").strip()
        embed_text = str(raw.get("chunk_to_embed") or retrieve_text).strip()
        if not retrieve_text:
            continue
        raw_pages = raw.get("pages") if isinstance(raw.get("pages"), list) else []
        page_numbers: list[int] = []
        image_uris: list[str] = []
        for page in raw_pages:
            if not isinstance(page, dict):
                continue
            page_id = page.get("page_id")
            if isinstance(page_id, int):
                page_numbers.append(page_id + 1)
            image_uri = page.get("image_uri")
            if image_uri:
                image_uris.append(str(image_uri))
        raw_metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        chunks.append(ParsedChunk(
            content=retrieve_text,
            page_number=min(page_numbers) if page_numbers else None,
            chunk_index=len(chunks),
            metadata={
                "ingestion_schema_version": ingestion_schema_version,
                "source_sha256": source_sha256,
                "parser_backend": f"databricks-ai-prep-search-{PREP_SEARCH_VERSION}",
                "block_type": "semantic_search",
                "databricks_prep_chunk_id": str(raw.get("chunk_id") or f"prep-{position}"),
                "databricks_chunk_position": raw.get("chunk_position", position),
                "chunk_to_embed": embed_text,
                "page_numbers": sorted(set(page_numbers)),
                "image_uris": list(dict.fromkeys(image_uris)),
                "databricks_search_metadata": raw_metadata,
                "source_uri": document.get("source_uri"),
                "ai_prep_search_version": PREP_SEARCH_VERSION,
            },
        ))
    return chunks


def _bbox(raw_bbox: Any) -> tuple[int | None, BoundingBox | None]:
    entries = raw_bbox if isinstance(raw_bbox, list) else []
    first = entries[0] if entries else {}
    coord = first.get("coord") if isinstance(first, dict) else None
    page_id = first.get("page_id") if isinstance(first, dict) else None
    if not isinstance(coord, list) or len(coord) != 4:
        return (int(page_id) + 1 if isinstance(page_id, int) else None), None
    try:
        box = BoundingBox(*(float(value) for value in coord))
    except (TypeError, ValueError):
        box = None
    return (int(page_id) + 1 if isinstance(page_id, int) else None), box


def _adapt_parse_result(
    result: dict[str, Any],
    *,
    file_path: str,
    source_sha256: str,
    cache_path: Path,
    cache_hit: bool,
) -> tuple[list[DocumentElement], int, dict[str, Any]]:
    document = result.get("document") or {}
    raw_elements = document.get("elements") or []
    raw_pages = document.get("pages") or []
    page_images = {
        int(page.get("id")): page.get("image_uri")
        for page in raw_pages
        if isinstance(page, dict) and isinstance(page.get("id"), int)
    }
    section_path: list[str] = []
    elements: list[DocumentElement] = []
    counts: dict[str, int] = {}
    skipped_layout_markers = 0
    type_map = {
        "title": "title",
        "section_header": "heading",
        "text": "paragraph",
        "table": "table",
        "figure": "figure",
        "caption": "caption",
        "footnote": "paragraph",
    }
    for index, raw in enumerate(raw_elements):
        if not isinstance(raw, dict):
            continue
        raw_type = str(raw.get("type") or "text").lower()
        element_type = type_map.get(raw_type)
        if element_type is None:
            skipped_layout_markers += 1
            continue
        content = html.unescape(str(raw.get("content") or "")).strip()
        description = str(raw.get("description") or "").strip()
        if element_type == "figure" and not content and not description:
            continue
        if element_type != "figure" and not content:
            continue
        page_number, box = _bbox(raw.get("bbox"))
        page_id = page_number - 1 if page_number else None
        if element_type in {"title", "heading"} and content:
            if element_type == "title":
                section_path = [" ".join(content.split())]
            else:
                section_path = section_path[:1]
                section_path.append(" ".join(content.split()))
        metadata: dict[str, Any] = {
            "databricks_element_type": raw_type,
            "databricks_confidence": raw.get("confidence"),
            "raw_element_id": raw.get("id", index),
            "image_uri": page_images.get(page_id),
        }
        if element_type == "table":
            metadata["raw_html"] = content
            metadata["rows"] = _table_rows(content)
        if element_type == "figure":
            metadata["visual_description"] = description

        # Databricks emits figure captions as the following element. Bind a
        # nearby same-page caption to its figure before chunking so lexical and
        # semantic retrieval see the figure's actual identity. Keep the caption
        # element too, preserving its independent provenance.
        if element_type == "caption" and content:
            for prior in reversed(elements):
                if prior.page_number != page_number:
                    break
                if prior.element_type != "figure" or prior.metadata.get("caption"):
                    continue
                close_below = (
                    prior.bbox is not None
                    and box is not None
                    and -8 <= box.y0 - prior.bbox.y1 <= 180
                )
                immediately_follows = prior is elements[-1] and (prior.bbox is None or box is None)
                if close_below or immediately_follows:
                    prior.metadata["caption"] = content
                break
        elements.append(DocumentElement(
            element_id=f"databricks-{raw.get('id', index)}",
            element_type=element_type,  # type: ignore[arg-type]
            text=content,
            page_number=page_number,
            bbox=box,
            section_path=list(section_path),
            metadata=metadata,
        ))
        counts[element_type] = counts.get(element_type, 0) + 1

    page_count = len(raw_pages)
    if not page_count:
        page_count = max((element.page_number or 0 for element in elements), default=0)
    errors = result.get("error_status") or []
    diagnostics = {
        "parser_backend": PARSER_BACKEND,
        "parse_version": PARSE_VERSION,
        "source_sha256": source_sha256,
        "page_count": page_count,
        "elements_by_type": counts,
        "tables_detected": counts.get("table", 0),
        "figures_detected": counts.get("figure", 0),
        "figure_descriptions": sum(
            bool(element.metadata.get("visual_description"))
            for element in elements if element.element_type == "figure"
        ),
        "page_parse_errors": errors,
        "skipped_layout_markers": skipped_layout_markers,
        "databricks_parse_cache_path": str(cache_path),
        "databricks_cache_hit": cache_hit,
    }
    return elements, page_count, diagnostics


def parse_document(file_path: str) -> ParsedDocument:
    """Parse one supported document through Databricks with a local hash cache."""
    suffix = Path(file_path).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"Databricks ai_parse_document does not support {suffix or '(no extension)'}")
    source_sha256 = _sha256(file_path)
    cache_path = _cache_path(source_sha256)
    result = _load_cached_parse(cache_path, source_sha256)
    cache_hit = result is not None
    started = time.perf_counter()
    if result is None:
        result = _run_remote_parse(file_path, source_sha256)
        _save_cached_parse(cache_path, source_sha256, result)
    elements, page_count, diagnostics = _adapt_parse_result(
        result,
        file_path=file_path,
        source_sha256=source_sha256,
        cache_path=cache_path,
        cache_hit=cache_hit,
    )
    if not elements:
        raise RuntimeError("Databricks parsed the document but produced no usable elements")
    diagnostics["parse_seconds"] = round(time.perf_counter() - started, 3)

    # Imported lazily to avoid a module cycle during ingestion initialization.
    from app.services.ingestion import INGESTION_SCHEMA_VERSION, build_structure_aware_chunks

    chunks = build_structure_aware_chunks(
        elements,
        source_sha256=source_sha256,
        parser_backend=PARSER_BACKEND,
    )
    prep_enabled = bool(settings.DATABRICKS_AI_PREP_SEARCH_ENABLED)
    prep_cache_hit = False
    if prep_enabled:
        prep_path = _prep_cache_path(source_sha256)
        prepared_result = _load_cached_prep(prep_path, source_sha256)
        prep_cache_hit = prepared_result is not None
        try:
            if prepared_result is None:
                prepared_result = _run_remote_prep_search(result)
                _save_cached_prep(prep_path, source_sha256, prepared_result)
            prepared_chunks = _adapt_prep_search_result(
                prepared_result,
                source_sha256=source_sha256,
                ingestion_schema_version=INGESTION_SCHEMA_VERSION,
            )
            if not prepared_chunks:
                raise RuntimeError("Databricks ai_prep_search produced no usable chunks")

            # Preserve source-addressable figures for the visual pipeline. The
            # semantic chunks become the text retrieval units; figure chunks
            # remain available for layout/vision evidence and local fallbacks.
            figure_chunks = [
                chunk for chunk in chunks
                if str(chunk.metadata.get("block_type") or "").lower() == "figure"
            ]
            chunks = [*prepared_chunks, *figure_chunks]
            for index, chunk in enumerate(chunks):
                chunk.chunk_index = index
            diagnostics.update({
                "search_prep_backend": f"databricks-ai-prep-search-{PREP_SEARCH_VERSION}",
                "search_prep_chunks": len(prepared_chunks),
                "search_prep_cache_path": str(prep_path),
                "search_prep_cache_hit": prep_cache_hit,
            })
        except Exception as exc:
            diagnostics.update({
                "search_prep_backend": "local-structure-aware-fallback",
                "search_prep_error": str(exc),
                "search_prep_cache_hit": prep_cache_hit,
            })
            if settings.DATABRICKS_AI_PREP_SEARCH_REQUIRED:
                raise
            logger.warning("ai_prep_search unavailable; retaining local chunks: %s", exc)
    else:
        diagnostics["search_prep_backend"] = "disabled"
    diagnostics["ingestion_schema_version"] = INGESTION_SCHEMA_VERSION
    diagnostics["chunk_count"] = len(chunks)
    for chunk in chunks:
        chunk.metadata["document_diagnostics"] = diagnostics
        chunk.metadata["databricks_parse_cache_path"] = str(cache_path)
    return ParsedDocument(
        source_path=file_path,
        source_sha256=source_sha256,
        parser_backend=PARSER_BACKEND,
        page_count=page_count,
        elements=elements,
        chunks=chunks,
        diagnostics=diagnostics,
    )


def _condition_extraction_schema(
    conditions: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Build one independently citable extraction field per atomic condition."""
    schema: dict[str, Any] = {}
    field_to_condition: dict[str, str] = {}
    for index, condition in enumerate(conditions, 1):
        condition_id = str(condition.get("condition_id") or f"C{index}")
        field_name = f"condition_{index}"
        details = " | ".join(
            str(value)
            for value in (
                condition.get("description"),
                condition.get("parameter"),
                condition.get("operator"),
                condition.get("threshold"),
                condition.get("unit"),
            )
            if value not in (None, "")
        )
        schema[field_name] = {
            "type": "string",
            "description": (
                f"Atomic condition {condition_id}: {details}. Extract one concise, direct observed fact "
                "that addresses this exact condition. Preserve values, units, execution state, subject, "
                "scope, selected form option, and material visual attributes. Return null when the excerpts "
                "do not directly establish a fact for this condition; never infer a compliance verdict."
            ),
        }
        field_to_condition[field_name] = condition_id

    if not schema:
        schema["requirement_fact"] = {
            "type": "string",
            "description": (
                "One concise, direct observed fact that addresses the requirement. Preserve values and units. "
                "Return null when no direct fact appears; never infer a compliance verdict."
            ),
        }
        field_to_condition["requirement_fact"] = "REQUIREMENT"
    return schema, field_to_condition


def _validated_targeted_facts(
    result: dict[str, Any],
    *,
    field_to_condition: dict[str, str],
    excerpt_ranges: list[dict[str, Any]],
    content: str,
    min_confidence: float,
) -> list[dict[str, Any]]:
    """Keep only high-confidence fields with valid citations to retrieved text."""
    response = result.get("response")
    metadata = result.get("metadata") or {}
    citations = metadata.get("citations") or []
    citations_by_id = {
        str(citation.get("id")): citation
        for citation in citations
        if isinstance(citation, dict)
    }
    facts: list[dict[str, Any]] = []
    for field_name, condition_id in field_to_condition.items():
        field = response.get(field_name) if isinstance(response, dict) else None
        if not isinstance(field, dict) or field.get("value") in (None, "", [], {}):
            continue
        try:
            confidence = float(field.get("confidence_score"))
        except (TypeError, ValueError):
            continue
        if confidence < min_confidence:
            continue

        rendered_citations: list[dict[str, Any]] = []
        evidence_ids: list[str] = []
        source_chunk_ids: list[str] = []
        for citation_id in field.get("citation_ids") or []:
            citation = citations_by_id.get(str(citation_id))
            if not citation:
                continue
            try:
                start, stop = int(citation["start"]), int(citation["stop"])
            except (KeyError, TypeError, ValueError):
                continue
            if start < 0 or stop <= start or stop > len(content):
                continue
            mapped = [
                item["evidence_id"]
                for item in excerpt_ranges
                if start < item["stop"] and stop > item["start"]
            ]
            if not mapped:
                continue
            evidence_ids.extend(mapped)
            mapped_chunk_ids = [
                str(item.get("source_chunk_id") or "")
                for item in excerpt_ranges
                if start < item["stop"] and stop > item["start"]
                and item.get("source_chunk_id")
            ]
            source_chunk_ids.extend(mapped_chunk_ids)
            rendered_citations.append({
                "citation_id": citation.get("id"),
                "start": start,
                "stop": stop,
                "quote": content[start:stop],
                "evidence_ids": list(dict.fromkeys(mapped)),
                "source_chunk_ids": list(dict.fromkeys(mapped_chunk_ids)),
            })
        if not rendered_citations:
            continue
        facts.append({
            "condition_id": condition_id,
            "fact": field["value"],
            "confidence_score": confidence,
            "evidence_ids": list(dict.fromkeys(evidence_ids)),
            "source_chunk_ids": list(dict.fromkeys(source_chunk_ids)),
            "citations": rendered_citations,
        })
    return facts


def extract_targeted_evidence(
    requirement_text: str,
    candidate_chunks: list[dict[str, Any]],
    conditions: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Extract citation-checked advisory facts for individual conditions."""
    if not settings.DATABRICKS_TARGETED_EXTRACTION_ENABLED or not candidate_chunks:
        return None
    sql_module, _ = _databricks_clients()
    host, token, warehouse_id = _sql_config()
    excerpts: list[str] = []
    excerpt_ranges: list[dict[str, Any]] = []
    for index, chunk in enumerate(candidate_chunks, 1):
        metadata = chunk.get("metadata") or {}
        if metadata.get("context_only") and len(excerpts) >= 10:
            continue
        excerpt = (
            f"[E{index}] {chunk.get('document_name', 'Document')}, page {chunk.get('page_number') or 'unknown'}, "
            f"block={metadata.get('block_type', 'text')}\n{(chunk.get('content') or '')[:6000]}"
        )
        start = sum(len(value) for value in excerpts) + 2 * len(excerpts)
        excerpts.append(excerpt)
        excerpt_ranges.append({
            "evidence_id": f"E{index}",
            "source_chunk_id": str(chunk.get("id") or chunk.get("chunk_id") or ""),
            "start": start,
            "stop": start + len(excerpt),
        })
    content = "\n\n".join(excerpts)
    schema, field_to_condition = _condition_extraction_schema(conditions or [])
    instructions = (
        "Extract only facts relevant to this requirement: " + requirement_text[:6000] + "\n"
        "Extract each atomic condition independently. The output is advisory context for a separate "
        "compliance reasoner. Use only explicit observations in the supplied excerpts. Do not decide "
        "compliance, combine unrelated excerpts, or replace missing facts with assumptions."
    )
    query = """
    SELECT to_json(ai_extract(
      ?, ?, map(
        'version', '2.1',
        'mode', 'precision',
        'instructions', ?,
        'enableCitations', 'true',
        'enableConfidenceScores', 'true'
      )
    )) AS extracted_json
    """
    with sql_module.connect(
        server_hostname=host,
        http_path=f"/sql/1.0/warehouses/{warehouse_id}",
        access_token=token,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, [content, json.dumps(schema), instructions])
            row = cursor.fetchone()
    if not row or not row[0]:
        return None
    result = json.loads(row[0]) if isinstance(row[0], str) else row[0]
    if not isinstance(result, dict) or result.get("error_message"):
        logger.warning("Databricks targeted evidence extraction returned an error")
        return None
    minimum = max(0.0, min(1.0, settings.DATABRICKS_TARGETED_EXTRACTION_MIN_CONFIDENCE))
    facts = _validated_targeted_facts(
        result,
        field_to_condition=field_to_condition,
        excerpt_ranges=excerpt_ranges,
        content=content,
        min_confidence=minimum,
    )
    if not facts:
        logger.warning(
            "Databricks targeted evidence extraction returned no citation-backed fields at confidence >= %.2f",
            minimum,
        )
        return None
    return {
        "condition_facts": facts,
        "metadata": {
            "version": (result.get("metadata") or {}).get("version", EXTRACT_VERSION),
            "minimum_confidence": minimum,
            "input_evidence_count": len(excerpts),
        },
    }


def _chunk_identity(chunk: dict[str, Any]) -> str:
    """Return the persisted identity used to join ai_extract citations back to evidence."""
    return str(chunk.get("id") or chunk.get("chunk_id") or "")


def _targeted_extraction_batches(
    chunks: list[dict[str, Any]],
    *,
    max_input_chars: int,
) -> list[list[dict[str, Any]]]:
    """Split a large corpus without silently dropping later pages.

    Batches preserve document/chunk order. The size estimate mirrors the
    rendered excerpt cap in ``extract_targeted_evidence`` and deliberately
    allows an oversized single chunk to stand alone.
    """
    limit = max(20_000, int(max_input_chars))
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0
    for chunk in chunks:
        if not (chunk.get("content") or chunk.get("quote")):
            continue
        estimated = min(len(str(chunk.get("content") or chunk.get("quote") or "")), 6000) + 180
        if current and current_chars + estimated > limit:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(chunk)
        current_chars += estimated
    if current:
        batches.append(current)
    return batches


def _merge_targeted_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate repeated extraction passes while retaining distinct citations."""
    merged: dict[tuple[str, str, tuple[str, ...]], dict[str, Any]] = {}
    for fact in facts:
        source_ids = tuple(sorted(str(value) for value in fact.get("source_chunk_ids") or [] if value))
        key = (
            str(fact.get("condition_id") or ""),
            " ".join(str(fact.get("fact") or "").lower().split()),
            source_ids,
        )
        existing = merged.get(key)
        if existing is None or float(fact.get("confidence_score") or 0.0) > float(existing.get("confidence_score") or 0.0):
            merged[key] = fact
    return sorted(
        merged.values(),
        key=lambda item: (
            str(item.get("condition_id") or ""),
            -float(item.get("confidence_score") or 0.0),
        ),
    )


def discover_targeted_evidence(
    requirement_text: str,
    evidence_corpus: list[dict[str, Any]],
    conditions: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Locate citation-backed facts across the complete independent-evidence corpus.

    This is the recall stage. Unlike the legacy targeted extractor, it is not
    limited to chunks already selected by similarity search. One schema covers
    every atom in a normal pass. Large corpora are scanned in bounded batches,
    and at most one focused second pass is made for still-uncovered atoms.
    """
    if not settings.DATABRICKS_TARGETED_EXTRACTION_ENABLED or not evidence_corpus:
        return None
    declared_conditions = list(conditions or [])
    batches = _targeted_extraction_batches(
        evidence_corpus,
        max_input_chars=settings.DATABRICKS_TARGETED_EXTRACTION_MAX_INPUT_CHARS,
    )
    if not batches:
        return None

    facts: list[dict[str, Any]] = []
    calls = 0
    for batch in batches:
        calls += 1
        result = extract_targeted_evidence(requirement_text, batch, declared_conditions)
        if result:
            facts.extend(result.get("condition_facts") or [])

    covered = {str(item.get("condition_id") or "") for item in facts}
    missing_conditions = [
        condition for condition in declared_conditions
        if str(condition.get("condition_id") or "") not in covered
    ]
    retried_ids: list[str] = []
    if missing_conditions and settings.DATABRICKS_TARGETED_EXTRACTION_RETRY_UNCOVERED:
        retried_ids = [str(item.get("condition_id") or "") for item in missing_conditions]
        focused_requirement = (
            requirement_text
            + "\nFOCUSED RETRY: locate explicit evidence only for atomic conditions "
            + ", ".join(retried_ids)
            + ". Search every supplied page; return null rather than infer."
        )
        for batch in batches:
            calls += 1
            result = extract_targeted_evidence(focused_requirement, batch, missing_conditions)
            if result:
                facts.extend(result.get("condition_facts") or [])

    facts = _merge_targeted_facts(facts)
    if not facts:
        return None
    covered = sorted({str(item.get("condition_id") or "") for item in facts})
    cited_chunk_ids = list(dict.fromkeys(
        str(chunk_id)
        for fact in facts
        for chunk_id in fact.get("source_chunk_ids") or []
        if chunk_id
    ))
    return {
        "condition_facts": facts,
        "metadata": {
            "version": EXTRACT_VERSION,
            "mode": "corpus_evidence_discovery",
            "minimum_confidence": settings.DATABRICKS_TARGETED_EXTRACTION_MIN_CONFIDENCE,
            "corpus_chunks": len(evidence_corpus),
            "batches": len(batches),
            "ai_extract_calls": calls,
            "covered_condition_ids": covered,
            "uncovered_condition_ids": sorted(
                str(item.get("condition_id") or "")
                for item in declared_conditions
                if str(item.get("condition_id") or "") not in set(covered)
            ),
            "retried_condition_ids": retried_ids,
            "cited_source_chunk_ids": cited_chunk_ids,
        },
    }


def _augment_candidates_from_discovery(
    candidates: list[dict[str, Any]],
    evidence_corpus: list[dict[str, Any]],
    discovery: dict[str, Any],
    *,
    max_candidates: int,
) -> list[dict[str, Any]]:
    """Put citation-backed raw chunks first, then retain ordinary retrieval and context."""
    corpus_by_id = {
        _chunk_identity(chunk): chunk for chunk in evidence_corpus if _chunk_identity(chunk)
    }
    cited_ids = list(dict.fromkeys(
        str(chunk_id)
        for fact in discovery.get("condition_facts") or []
        for chunk_id in fact.get("source_chunk_ids") or []
        if chunk_id
    ))
    # Never discard a validated citation merely because the ordinary prompt
    # budget was configured below the number of discovered evidence chunks.
    candidate_limit = max(1, max_candidates, len(cited_ids))
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()
    represented_pages: set[tuple[str, Any]] = set()

    def page_key(chunk: dict[str, Any]) -> tuple[str, Any]:
        return (
            str(chunk.get("document_id") or chunk.get("document_name") or ""),
            chunk.get("page_number"),
        )

    def add(chunk: dict[str, Any] | None) -> None:
        if chunk is None or len(ordered) >= candidate_limit:
            return
        identity = _chunk_identity(chunk)
        if not identity or identity in seen:
            return
        seen.add(identity)
        ordered.append(dict(chunk))

    cited_chunks = [corpus_by_id[chunk_id] for chunk_id in cited_ids if chunk_id in corpus_by_id]

    # First cover distinct cited pages. Several conditions commonly cite
    # different blocks from one results table; those blocks remain available
    # later, but cannot crowd every other evidence page out of the leading set.
    for chunk in cited_chunks:
        key = page_key(chunk)
        if key in represented_pages:
            continue
        add(chunk)
        represented_pages.add(key)

    # Then preserve one similarity-retrieved leader from every additional page.
    # This retains evidence that ai_extract did not confidently structure.
    for chunk in candidates:
        if (chunk.get("metadata") or {}).get("context_only"):
            continue
        key = page_key(chunk)
        if key in represented_pages:
            continue
        add(chunk)
        represented_pages.add(key)

    # Finally include all remaining cited details, ordinary ranked chunks and
    # their existing structural context. Valid citations are never discarded.
    for chunk in cited_chunks:
        add(chunk)
    for chunk in candidates:
        add(chunk)

    # Add bounded same-page structural context only after all primary evidence,
    # so table headers cannot displace another condition's cited page.
    positions = {
        _chunk_identity(chunk): index for index, chunk in enumerate(evidence_corpus)
        if _chunk_identity(chunk)
    }
    for chunk_id in cited_ids:
        position = positions.get(chunk_id)
        cited = corpus_by_id.get(chunk_id)
        if position is None or cited is None:
            continue
        for offset in (-1, 1):
            neighbor_index = position + offset
            if not 0 <= neighbor_index < len(evidence_corpus):
                continue
            neighbor = evidence_corpus[neighbor_index]
            if (
                neighbor.get("document_id") != cited.get("document_id")
                or neighbor.get("page_number") != cited.get("page_number")
            ):
                continue
            contextual = dict(neighbor)
            contextual["metadata"] = {
                **dict(neighbor.get("metadata") or {}),
                "context_only": True,
                "context_source": "ai_extract_citation",
            }
            add(contextual)
    return ordered


def _remap_discovery_evidence_ids(
    discovery: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> None:
    """Translate stable source chunk IDs into the E1..En catalog seen by the verifier."""
    by_chunk_id = {
        _chunk_identity(chunk): f"E{index}"
        for index, chunk in enumerate(candidates, 1)
        if _chunk_identity(chunk)
    }
    for fact in discovery.get("condition_facts") or []:
        fact["evidence_ids"] = list(dict.fromkeys(
            by_chunk_id[chunk_id]
            for chunk_id in fact.get("source_chunk_ids") or []
            if chunk_id in by_chunk_id
        ))
        for citation in fact.get("citations") or []:
            citation["evidence_ids"] = list(dict.fromkeys(
                by_chunk_id[chunk_id]
                for chunk_id in citation.get("source_chunk_ids") or []
                if chunk_id in by_chunk_id
            ))


async def enrich_targeted_evidence_items(
    requirement_items: list[dict[str, Any]],
    *,
    max_concurrency: int | None = None,
    evidence_corpus: list[dict[str, Any]] | None = None,
) -> dict[str, int]:
    """Discover, attach, and cite focused Databricks facts before verification.

    Supplying ``evidence_corpus`` activates the corpus-wide recall path and
    augments each requirement's candidate set. Omitting it preserves the
    legacy post-retrieval behavior for callers that only have selected chunks.
    """
    stats = {"attempted": 0, "succeeded": 0, "failed": 0}
    if not settings.DATABRICKS_TARGETED_EXTRACTION_ENABLED:
        return stats

    semaphore = asyncio.Semaphore(max(
        1,
        max_concurrency or settings.DATABRICKS_TARGETED_EXTRACTION_CONCURRENCY,
    ))

    async def enrich(item: dict[str, Any]) -> None:
        stats["attempted"] += 1
        requirement_text = " ".join(
            str(value)
            for value in (item.get("req_code"), item.get("title"), item.get("description"))
            if value
        )
        try:
            async with semaphore:
                if evidence_corpus is not None:
                    result = await asyncio.to_thread(
                        discover_targeted_evidence,
                        requirement_text,
                        evidence_corpus,
                        item.get("conditions", []),
                    )
                else:
                    result = await asyncio.to_thread(
                        extract_targeted_evidence,
                        requirement_text,
                        item.get("candidate_chunks", []),
                        item.get("conditions", []),
                    )
            if result and evidence_corpus is not None:
                candidates = _augment_candidates_from_discovery(
                    item.get("candidate_chunks", []),
                    evidence_corpus,
                    result,
                    max_candidates=settings.DATABRICKS_TARGETED_EXTRACTION_MAX_CANDIDATES,
                )
                _remap_discovery_evidence_ids(result, candidates)
                item["candidate_chunks"] = candidates
            item["targeted_extraction"] = result
            stats["succeeded" if result else "failed"] += 1
        except Exception as exc:
            stats["failed"] += 1
            logger.warning(
                "Targeted evidence extraction failed for %s: %s",
                item.get("req_code"),
                exc,
            )

    await asyncio.gather(*(enrich(item) for item in requirement_items))
    return stats
