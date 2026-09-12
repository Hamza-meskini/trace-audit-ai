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


TARGETED_EVIDENCE_SCHEMA = {
    "observed_facts": {
        "type": "string",
        "description": "Facts in the supplied excerpts that directly address the requirement, preserving values and units.",
    },
    "checklist_or_visual_results": {
        "type": "string",
        "description": "Selected checkbox/form options and material visual findings; state ambiguity explicitly.",
    },
    "test_conditions_and_scope": {
        "type": "string",
        "description": "Executed test conditions, configuration, timing, subject identity, and coverage scope.",
    },
    "contradictory_or_negative_evidence": {
        "type": "string",
        "description": "Observed failures, contradictory values, negative findings, or explicit nonconformance.",
    },
    "limitations": {
        "type": "string",
        "description": "Missing, merely planned, indirect, ambiguous, or method-incompatible information.",
    },
}


def extract_targeted_evidence(
    requirement_text: str,
    candidate_chunks: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Extract a compact advisory fact summary from already-retrieved excerpts."""
    if not settings.DATABRICKS_TARGETED_EXTRACTION_ENABLED or not candidate_chunks:
        return None
    sql_module, _ = _databricks_clients()
    host, token, warehouse_id = _sql_config()
    excerpts = []
    for index, chunk in enumerate(candidate_chunks, 1):
        metadata = chunk.get("metadata") or {}
        if metadata.get("context_only") and len(excerpts) >= 10:
            continue
        excerpts.append(
            f"[E{index}] {chunk.get('document_name', 'Document')}, page {chunk.get('page_number') or 'unknown'}, "
            f"block={metadata.get('block_type', 'text')}\n{(chunk.get('content') or '')[:6000]}"
        )
    content = "\n\n".join(excerpts)
    instructions = (
        "Extract only facts relevant to this requirement: " + requirement_text[:6000] + "\n"
        "The output is advisory context for a separate compliance reasoner. Do not decide compliance, "
        "and do not replace raw evidence with assumptions."
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
            cursor.execute(query, [content, json.dumps(TARGETED_EVIDENCE_SCHEMA), instructions])
            row = cursor.fetchone()
    if not row or not row[0]:
        return None
    result = json.loads(row[0]) if isinstance(row[0], str) else row[0]
    response = result.get("response") if isinstance(result, dict) else None
    if not isinstance(response, dict) or not any(
        isinstance(field, dict) and field.get("value") not in (None, "", [], {})
        for field in response.values()
    ):
        logger.warning("Databricks targeted evidence extraction returned no populated fields")
        return None
    return result


async def enrich_targeted_evidence_items(
    requirement_items: list[dict[str, Any]],
    *,
    max_concurrency: int | None = None,
) -> dict[str, int]:
    """Attach focused Databricks facts to requirement items for verification."""
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
                result = await asyncio.to_thread(
                    extract_targeted_evidence,
                    requirement_text,
                    item.get("candidate_chunks", []),
                )
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
