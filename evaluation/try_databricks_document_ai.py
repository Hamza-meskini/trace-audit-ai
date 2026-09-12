"""Standalone Databricks document-AI experiment for one local document.

The script uploads a document to a Unity Catalog Volume, runs
``ai_parse_document`` and ``ai_extract``, then writes the raw JSON results to a
timestamped local directory. It deliberately has no dependency on AudiTrace's
benchmark labels or pipeline code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "evaluation" / "results" / "databricks_document_ai"
SUPPORTED_SUFFIXES = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".jpg", ".jpeg", ".png", ".tif", ".tiff"}
VOLUME_PATH_RE = re.compile(r"^/Volumes/[^/]+/[^/]+/[^/]+(?:/.*)?$")


REQUIREMENTS_SCHEMA: dict[str, Any] = {
        "document_title": {
            "type": "string",
            "description": "Title or identifying name printed in the document.",
        },
        "requirements": {
            "type": "array",
            "description": (
                "Every explicit normative requirement or independently applicable clause. "
                "Preserve the document's clause order and do not invent implied requirements."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "clause_id": {
                        "type": "string",
                        "description": "Exact printed clause identifier, or null if none is printed.",
                    },
                    "requirement_text": {
                        "type": "string",
                        "description": "Exact or minimally normalized requirement wording, including qualifiers.",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Entity, component, process, or actor constrained by the requirement.",
                    },
                    "obligation": {
                        "type": "string",
                        "description": "Required, prohibited, or permitted action/state without dropping modality.",
                    },
                    "applicability": {
                        "type": "string",
                        "description": "Complete IF/WHEN/UNLESS antecedent or scope condition, if present.",
                    },
                    "parameter": {
                        "type": "string",
                        "description": "Measured or constrained property, if present.",
                    },
                    "operator": {
                        "type": "string",
                        "description": "Exact comparison such as <=, >, between, equals, contains, or null.",
                    },
                    "target_value": {
                        "type": "string",
                        "description": "Target value or range exactly as stated; keep it as text to avoid losing notation.",
                    },
                    "unit": {
                        "type": "string",
                        "description": "Unit exactly as printed, if present.",
                    },
                    "exceptions_and_alternatives": {
                        "type": "array",
                        "description": "Explicit exceptions, alternative compliance paths, or OR branches.",
                        "items": {"type": "string"},
                    },
                    "referenced_clauses": {
                        "type": "array",
                        "description": "Clause identifiers explicitly referenced by this requirement.",
                        "items": {"type": "string"},
                    },
                },
            },
        },
}


EVIDENCE_SCHEMA: dict[str, Any] = {
        "document_title": {
            "type": "string",
            "description": "Title or identifying name printed in the document.",
        },
        "test_or_audit_context": {
            "type": "string",
            "description": "What was tested, inspected, measured, or asserted, including configuration and conditions.",
        },
        "evidence_observations": {
            "type": "array",
            "description": (
                "Distinct factual observations, measurements, checklist selections, test results, and explicit conclusions. "
                "Separate different parameters and test conditions. Do not infer compliance with an external requirement."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "claim": {
                        "type": "string",
                        "description": "Exact or minimally normalized factual statement supported by the document.",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Component, product, process, or test item to which the observation applies.",
                    },
                    "parameter": {
                        "type": "string",
                        "description": "Observed or measured property.",
                    },
                    "observed_value": {
                        "type": "string",
                        "description": "Value, range, pass/fail mark, yes/no selection, or stated outcome exactly as shown.",
                    },
                    "unit": {
                        "type": "string",
                        "description": "Unit exactly as printed, if present.",
                    },
                    "test_condition": {
                        "type": "string",
                        "description": "Applicable test sequence, timing, configuration, or environmental condition.",
                    },
                    "evidence_kind": {
                        "type": "enum",
                        "labels": ["measurement", "checklist", "table", "narrative", "figure", "procedure", "other"],
                        "description": "Form in which the evidence appears.",
                    },
                    "visual_interpretation": {
                        "type": "string",
                        "description": "For figures/forms, state what a marker, checkbox, label, diagram, or image visibly shows.",
                    },
                    "limitations": {
                        "type": "string",
                        "description": "Any explicit uncertainty, missing data, indirect wording, or scope limitation.",
                    },
                },
            },
        },
}


EVIDENCE_SUMMARY_SCHEMA: dict[str, Any] = {
    "document_title": {
        "type": "string",
        "description": "Exact title or identifying name printed in the report.",
    },
    "test_or_audit_context": {
        "type": "string",
        "description": "What was tested or inspected, including configuration, dates, and test conditions.",
    },
    "measured_results": {
        "type": "string",
        "description": (
            "Complete concise list of material measurements and observed values. Preserve parameter names, values, "
            "units, test stages, timing, and negative or zero results."
        ),
    },
    "checklist_and_form_results": {
        "type": "string",
        "description": (
            "Complete concise list of checklist questions and the visibly selected Yes/No, Pass/Fail, or marked option. "
            "State ambiguous marker layouts explicitly."
        ),
    },
    "visual_findings": {
        "type": "string",
        "description": "Material facts visibly shown by figures, photographs, diagrams, labels, and forms.",
    },
    "explicit_conclusions": {
        "type": "string",
        "description": "Conclusions explicitly stated by the document, preserving cautious words such as appears or indicates.",
    },
    "limitations_or_missing_information": {
        "type": "string",
        "description": "Explicit uncertainty, unavailable photographs, missing data, indirect evidence, or scope limitations.",
    },
}


AUTO_SCHEMA: dict[str, Any] = {
        "document_type": {
            "type": "string",
            "description": "Specific document type based only on its content.",
        },
        "document_title": {
            "type": "string",
            "description": "Title or identifying name printed in the document.",
        },
        "document_purpose": {
            "type": "string",
            "description": "Concise description of what the document establishes or records.",
        },
        "clause_or_section_inventory": {
            "type": "array",
            "description": "Ordered inventory of substantive clauses or sections; omit headers and footers.",
            "items": {
                "type": "object",
                "properties": {
                    "identifier": {"type": "string", "description": "Exact printed identifier, if any."},
                    "heading": {"type": "string", "description": "Exact printed heading, if any."},
                    "content_summary": {
                        "type": "string",
                        "description": "Faithful summary that preserves thresholds, exceptions, conditions, and results.",
                    },
                    "contains_visual_evidence": {
                        "type": "boolean",
                        "description": "Whether understanding this section depends on a figure, form marker, or image.",
                    },
                },
            },
        },
        "important_tables_and_figures": {
            "type": "array",
            "description": "Substantive tables, charts, diagrams, photos, forms, and checklist selections.",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "Printed label or a concise generated label."},
                    "meaning": {"type": "string", "description": "What the visual element shows, including marked options."},
                },
            },
        },
}

PROFILE_SCHEMAS = {
    "requirements": REQUIREMENTS_SCHEMA,
    "evidence": EVIDENCE_SCHEMA,
    "evidence-summary": EVIDENCE_SUMMARY_SCHEMA,
    "auto": AUTO_SCHEMA,
}

PROFILE_INSTRUCTIONS = {
    "requirements": (
        "Extract normative requirements from this document for a traceable compliance system. "
        "Keep clause boundaries, applicability conditions, AND/OR alternatives, exceptions, thresholds, units, and references. "
        "Return only requirements supported by the document. Do not evaluate evidence and do not infer compliance."
    ),
    "evidence": (
        "Extract traceable factual evidence from this report or record. Read tables, figures, forms, and checkbox markers. "
        "Preserve numeric values, units, test conditions, negative findings, and uncertainty. "
        "Do not decide whether an external regulation is satisfied."
    ),
    "evidence-summary": (
        "Summarize all material factual evidence in this report for later compliance reasoning. Read tables, figures, "
        "forms, and checkbox markers. Preserve numeric values, units, test stages, negative findings, and uncertainty. "
        "Do not decide whether an external regulation is satisfied."
    ),
    "auto": (
        "Build a faithful, traceable structural inventory of this document. Preserve clause identifiers, numeric details, "
        "conditions, exceptions, table results, figures, and form selections. Do not compare against external documents."
    ),
}


def _load_local_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(PROJECT_ROOT / "backend" / ".env", override=False)
    load_dotenv(PROJECT_ROOT / ".env", override=False)


def _hostname(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    parsed = urlparse(value if "://" in value else f"https://{value}")
    if not parsed.hostname:
        raise ValueError("Databricks host must be a workspace hostname or URL")
    return parsed.hostname


def _validate_volume_dir(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if not VOLUME_PATH_RE.fullmatch(normalized):
        raise ValueError("--volume-dir must look like /Volumes/<catalog>/<schema>/<volume>[/folder]")
    if ".." in normalized.split("/"):
        raise ValueError("--volume-dir cannot contain '..'")
    return normalized


def _safe_filename(path: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem).strip("._") or "document"
    return f"{stem[:80]}-{uuid.uuid4().hex[:10]}{path.suffix.lower()}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_schema(args: argparse.Namespace) -> dict[str, Any]:
    if not args.schema:
        return PROFILE_SCHEMAS[args.profile]
    try:
        loaded = json.loads(args.schema.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read --schema: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError("--schema must contain a JSON object")
    return loaded


def _require_databricks_packages() -> tuple[Any, Any]:
    try:
        from databricks import sql
        from databricks.sdk import WorkspaceClient
    except ImportError as exc:
        raise RuntimeError(
            "Databricks packages are missing. Install with: "
            r"backend\venv\Scripts\python.exe -m pip install -r evaluation\requirements-databricks-document-ai.txt"
        ) from exc
    return sql, WorkspaceClient


def _credentials(args: argparse.Namespace) -> tuple[str, str]:
    raw_host = args.host or os.getenv("DATABRICKS_HOST") or os.getenv("DATABRICKS_BASE_URL", "")
    token = os.getenv("DATABRICKS_TOKEN", "")
    host = _hostname(raw_host)
    if not host:
        raise ValueError("Set DATABRICKS_HOST (or DATABRICKS_BASE_URL) or pass --host")
    if not token:
        raise ValueError("Set DATABRICKS_TOKEN; never pass the token on the command line")
    return host, token


def _list_warehouses(args: argparse.Namespace) -> int:
    _, workspace_client_cls = _require_databricks_packages()
    host, token = _credentials(args)
    client = workspace_client_cls(host=f"https://{host}", token=token)
    rows = list(client.warehouses.list())
    if not rows:
        print("No SQL warehouses are visible to this identity.")
        return 1
    print("Visible SQL warehouses:")
    for warehouse in rows:
        state = getattr(warehouse, "state", None)
        state_value = getattr(state, "value", state) or "unknown"
        print(f"  {warehouse.name}  id={warehouse.id}  state={state_value}")
    return 0


def _list_volumes(args: argparse.Namespace) -> int:
    _, workspace_client_cls = _require_databricks_packages()
    host, token = _credentials(args)
    client = workspace_client_cls(host=f"https://{host}", token=token)
    visible: list[Any] = []
    for catalog in client.catalogs.list():
        catalog_name = getattr(catalog, "name", "")
        if not catalog_name or catalog_name in {"system", "__databricks_internal"}:
            continue
        try:
            schemas = client.schemas.list(catalog_name=catalog_name)
            for schema in schemas:
                schema_name = getattr(schema, "name", "")
                if not schema_name or schema_name == "information_schema":
                    continue
                try:
                    visible.extend(client.volumes.list(catalog_name=catalog_name, schema_name=schema_name))
                except Exception:
                    continue
        except Exception:
            continue
    if not visible:
        print("No Unity Catalog Volumes are visible to this identity.")
        return 1
    print("Visible Unity Catalog Volumes:")
    for volume in visible:
        full_name = getattr(volume, "full_name", None)
        if not full_name:
            full_name = f"{volume.catalog_name}.{volume.schema_name}.{volume.name}"
        print(f"  /Volumes/{str(full_name).replace('.', '/')}")
    return 0


def _extract_error(payload: Any) -> str | None:
    if isinstance(payload, dict) and payload.get("error_message"):
        return str(payload["error_message"])
    return None


def _has_extracted_value(value: Any) -> bool:
    if isinstance(value, dict):
        if "value" in value:
            return value["value"] not in (None, "", [], {})
        return any(_has_extracted_value(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_extracted_value(item) for item in value)
    return value not in (None, "")


def _extraction_is_empty(payload: Any) -> bool:
    return not isinstance(payload, dict) or not _has_extracted_value(payload.get("response"))


def extract_saved_parse(args: argparse.Namespace) -> int:
    sql_module, _ = _require_databricks_packages()
    host, token = _credentials(args)
    parsed_path = args.parsed_json.resolve()
    if not parsed_path.is_file():
        raise ValueError(f"Parsed JSON does not exist: {parsed_path}")
    try:
        parsed = json.loads(parsed_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read --parsed-json: {exc}") from exc

    warehouse_id = (
        args.warehouse_id
        or os.getenv("DATABRICKS_SQL_WAREHOUSE_ID", "")
        or os.getenv("DATABRICKS_WAREHOUSE_ID", "")
    )
    http_path = args.http_path or os.getenv("DATABRICKS_HTTP_PATH", "")
    if not http_path:
        if not warehouse_id:
            raise ValueError("Set DATABRICKS_SQL_WAREHOUSE_ID, pass --warehouse-id, or pass --http-path")
        http_path = f"/sql/1.0/warehouses/{warehouse_id}"

    schema = _load_schema(args)
    instructions = args.instructions or PROFILE_INSTRUCTIONS[args.profile]
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    local_dir = (args.output_dir or DEFAULT_OUTPUT_ROOT / f"{run_id}_reextract_{args.profile}").resolve()
    local_dir.mkdir(parents=True, exist_ok=False)
    _write_json(local_dir / "schema.json", schema)
    manifest: dict[str, Any] = {
        "status": "started",
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input": {"parsed_json": str(parsed_path), "sha256": _sha256(parsed_path)},
        "configuration": {
            "workspace_host": host,
            "warehouse_id": warehouse_id or None,
            "profile": args.profile,
            "extract_version": "2.1",
            "extract_mode": "precision",
            "citations": True,
            "confidence_scores": True,
        },
    }
    _write_json(local_dir / "manifest.json", manifest)
    started = time.perf_counter()
    query = """
    SELECT to_json(ai_extract(
      parse_json(?),
      ?,
      map(
        'version', '2.1',
        'mode', 'precision',
        'instructions', ?,
        'enableCitations', 'true',
        'enableConfidenceScores', 'true'
      )
    )) AS extracted_json
    """
    try:
        print("Running ai_extract 2.1 against the saved parse (no document re-parse)...")
        with sql_module.connect(server_hostname=host, http_path=http_path, access_token=token) as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, [json.dumps(parsed, ensure_ascii=False), json.dumps(schema, ensure_ascii=False), instructions])
                row = cursor.fetchone()
        if not row:
            raise RuntimeError("Databricks returned no row")
        extracted = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        _write_json(local_dir / "extracted.json", extracted)
        extract_error = _extract_error(extracted)
        empty = _extraction_is_empty(extracted)
        manifest["status"] = "extract_error" if extract_error else "empty_extraction" if empty else "success"
        manifest["duration_seconds"] = round(time.perf_counter() - started, 3)
        manifest["extract_error"] = extract_error
        _write_json(local_dir / "manifest.json", manifest)
        print(f"Saved results: {local_dir}")
        if extract_error:
            print(f"ai_extract error: {extract_error}", file=sys.stderr)
            return 2
        if empty:
            print("ai_extract returned no non-null values", file=sys.stderr)
            return 3
        return 0
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["duration_seconds"] = round(time.perf_counter() - started, 3)
        manifest["error_type"] = type(exc).__name__
        manifest["error"] = str(exc)
        _write_json(local_dir / "manifest.json", manifest)
        raise


def process_document(args: argparse.Namespace) -> int:
    sql_module, workspace_client_cls = _require_databricks_packages()
    host, token = _credentials(args)
    volume_dir = _validate_volume_dir(args.volume_dir)
    input_path = args.document.resolve()
    if not input_path.is_file():
        raise ValueError(f"Document does not exist: {input_path}")
    if input_path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError(f"Unsupported document type: {input_path.suffix or '(none)'}")

    warehouse_id = (
        args.warehouse_id
        or os.getenv("DATABRICKS_SQL_WAREHOUSE_ID", "")
        or os.getenv("DATABRICKS_WAREHOUSE_ID", "")
    )
    http_path = args.http_path or os.getenv("DATABRICKS_HTTP_PATH", "")
    if not http_path:
        if not warehouse_id:
            raise ValueError("Set DATABRICKS_SQL_WAREHOUSE_ID, pass --warehouse-id, or pass --http-path")
        http_path = f"/sql/1.0/warehouses/{warehouse_id}"

    schema = _load_schema(args)
    instructions = args.instructions or PROFILE_INSTRUCTIONS[args.profile]
    if len(instructions) >= 20_000:
        raise ValueError("Extraction instructions must be shorter than 20,000 characters")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    remote_input_dir = f"{volume_dir}/auditrace-document-ai-inputs"
    remote_image_dir = f"{volume_dir}/auditrace-document-ai-images/{run_id}"
    remote_file = f"{remote_input_dir}/{_safe_filename(input_path)}"
    local_dir = (args.output_dir or DEFAULT_OUTPUT_ROOT / f"{run_id}_{input_path.stem}").resolve()
    local_dir.mkdir(parents=True, exist_ok=False)

    manifest: dict[str, Any] = {
        "status": "started",
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input": {
            "local_path": str(input_path),
            "filename": input_path.name,
            "bytes": input_path.stat().st_size,
            "sha256": _sha256(input_path),
        },
        "configuration": {
            "workspace_host": host,
            "warehouse_id": warehouse_id or None,
            "profile": args.profile,
            "parse_version": "2.0",
            "extract_version": "2.1",
            "extract_mode": "precision",
            "figure_descriptions": not args.no_figure_descriptions,
            "page_range": args.page_range,
            "citations": True,
            "confidence_scores": True,
            "remote_file": remote_file,
            "remote_image_dir": remote_image_dir,
        },
        "outputs": {
            "parsed": str(local_dir / "parsed.json"),
            "extracted": str(local_dir / "extracted.json"),
            "schema": str(local_dir / "schema.json"),
        },
    }
    _write_json(local_dir / "manifest.json", manifest)
    _write_json(local_dir / "schema.json", schema)

    workspace = workspace_client_cls(host=f"https://{host}", token=token)
    uploaded = False
    started = time.perf_counter()
    try:
        workspace.files.create_directory(remote_input_dir)
        workspace.files.create_directory(remote_image_dir)
        workspace.files.upload_from(remote_file, str(input_path), overwrite=False)
        uploaded = True
        print(f"Uploaded: {remote_file}")

        parse_options = ["'version', '2.0'", "'imageOutputPath', ?", "'descriptionElementTypes', ?"]
        parse_parameters: list[Any] = [remote_file, remote_image_dir, "" if args.no_figure_descriptions else "*"]
        if args.page_range:
            parse_options.append("'pageRange', ?")
            parse_parameters.append(args.page_range)

        query = f"""
        WITH source_document AS (
          SELECT content
          FROM read_files(?, format => 'binaryFile')
        ), parsed_document AS (
          SELECT ai_parse_document(content, map({', '.join(parse_options)})) AS parsed
          FROM source_document
        )
        SELECT
          to_json(parsed) AS parsed_json,
          to_json(ai_extract(
            parsed,
            ?,
            map(
              'version', '2.1',
              'mode', 'precision',
              'instructions', ?,
              'enableCitations', 'true',
              'enableConfidenceScores', 'true'
            )
          )) AS extracted_json
        FROM parsed_document
        """
        parameters = parse_parameters + [json.dumps(schema, ensure_ascii=False), instructions]

        print("Running ai_parse_document 2.0 and ai_extract 2.1 (precision mode)...")
        with sql_module.connect(
            server_hostname=host,
            http_path=http_path,
            access_token=token,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, parameters)
                row = cursor.fetchone()
        if not row:
            raise RuntimeError("Databricks returned no row")

        parsed = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        extracted = json.loads(row[1]) if isinstance(row[1], str) else row[1]
        _write_json(local_dir / "parsed.json", parsed)
        _write_json(local_dir / "extracted.json", extracted)

        extract_error = _extract_error(extracted)
        empty = _extraction_is_empty(extracted)
        manifest["status"] = "extract_error" if extract_error else "empty_extraction" if empty else "success"
        manifest["duration_seconds"] = round(time.perf_counter() - started, 3)
        manifest["extract_error"] = extract_error
        _write_json(local_dir / "manifest.json", manifest)
        print(f"Saved results: {local_dir}")
        if extract_error:
            print(f"ai_extract error: {extract_error}", file=sys.stderr)
            return 2
        if empty:
            print("ai_extract returned no non-null values", file=sys.stderr)
            return 3
        return 0
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["duration_seconds"] = round(time.perf_counter() - started, 3)
        manifest["error_type"] = type(exc).__name__
        manifest["error"] = str(exc)
        _write_json(local_dir / "manifest.json", manifest)
        raise
    finally:
        if uploaded and not args.keep_upload:
            try:
                workspace.files.delete(remote_file)
                print("Removed temporary uploaded source document.")
            except Exception as cleanup_error:
                print(f"Warning: could not remove temporary upload: {cleanup_error}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Test Databricks ai_parse_document + ai_extract on one local document.",
    )
    parser.add_argument("document", nargs="?", type=Path, help="Local PDF/image/Office document")
    parser.add_argument("--profile", choices=sorted(PROFILE_SCHEMAS), default="auto")
    parser.add_argument("--schema", type=Path, help="Custom advanced ai_extract JSON schema")
    parser.add_argument("--parsed-json", type=Path, help="Re-run extraction from a saved parsed.json without uploading/parsing")
    parser.add_argument("--instructions", help="Custom extraction instructions (under 20,000 characters)")
    parser.add_argument("--host", help="Workspace hostname or URL; token must remain in DATABRICKS_TOKEN")
    parser.add_argument("--warehouse-id", help="SQL warehouse ID (or DATABRICKS_SQL_WAREHOUSE_ID)")
    parser.add_argument("--http-path", help="Full SQL HTTP path (or DATABRICKS_HTTP_PATH)")
    parser.add_argument("--volume-dir", help="Existing UC path: /Volumes/catalog/schema/volume[/folder]")
    parser.add_argument("--page-range", help="Optional 1-indexed range such as 1,3,5-10")
    parser.add_argument("--output-dir", type=Path, help="Local result directory (must not already exist)")
    parser.add_argument("--no-figure-descriptions", action="store_true", help="Skip AI descriptions for figures")
    parser.add_argument("--keep-upload", action="store_true", help="Keep the temporary source file in the UC Volume")
    parser.add_argument("--list-warehouses", action="store_true", help="List visible SQL warehouses, then exit")
    parser.add_argument("--list-volumes", action="store_true", help="List visible Unity Catalog Volumes, then exit")
    return parser


def main() -> int:
    _load_local_env()
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.list_warehouses:
            return _list_warehouses(args)
        if args.list_volumes:
            return _list_volumes(args)
        if args.parsed_json:
            return extract_saved_parse(args)
        if not args.document:
            parser.error("document is required unless a --list-* discovery option is used")
        if not args.volume_dir:
            parser.error("--volume-dir is required for document processing")
        return process_document(args)
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
