# Databricks document-AI experiment

This harness tests the same Databricks document parsing and focused extraction
capabilities available to the AudiTrace production pipeline. It does not read
benchmark answer files.

## Setup

Install the two Databricks clients into the backend virtual environment:

```powershell
.\backend\venv\Scripts\python.exe -m pip install -r .\evaluation\requirements-databricks-document-ai.txt
```

The script automatically loads `backend/.env`. Add these values there (the
token is already used elsewhere in AudiTrace):

```dotenv
DATABRICKS_HOST=https://dbc-....cloud.databricks.com
DATABRICKS_TOKEN=...
DATABRICKS_SQL_WAREHOUSE_ID=...
```

Do not commit the `.env` file. To discover the warehouse ID:

```powershell
.\backend\venv\Scripts\python.exe .\evaluation\try_databricks_document_ai.py --list-warehouses
```

You also need an existing Unity Catalog Volume and permission to write files to
it. Its path has the form `/Volumes/<catalog>/<schema>/<volume>`. List the
Volumes visible to the configured identity with:

```powershell
.\backend\venv\Scripts\python.exe .\evaluation\try_databricks_document_ai.py --list-volumes
```

## Run

For a regulation or requirements document:

```powershell
.\backend\venv\Scripts\python.exe .\evaluation\try_databricks_document_ai.py `
  "C:\path\to\regulation.pdf" `
  --profile requirements `
  --volume-dir "/Volumes/main/default/auditrace_test"
```

For a first pass over a test report or evidence document, use the compact
summary profile:

```powershell
.\backend\venv\Scripts\python.exe .\evaluation\try_databricks_document_ai.py `
  "C:\path\to\test-report.pdf" `
  --profile evidence-summary `
  --volume-dir "/Volumes/main/default/auditrace_test"
```

The `evidence` profile requests a much larger observation inventory and can be
less reliable on long documents. Use `--profile auto` for a neutral structural
inventory, `--page-range 1-20` for a cheaper first test, or
`--schema path\to\schema.json` to replace the built-in extraction schema.

Re-run extraction against an existing `parsed.json` without uploading or
parsing the document again:

```powershell
.\backend\venv\Scripts\python.exe .\evaluation\try_databricks_document_ai.py `
  --parsed-json ".\evaluation\results\databricks_document_ai\<run>\parsed.json" `
  --profile evidence-summary `
  --warehouse-id "<warehouse-id>"
```

Each run writes a timestamped directory under
`evaluation/results/databricks_document_ai/` containing:

- `parsed.json`: the full `ai_parse_document` 2.0 output, including layout,
  tables, bounding boxes, and generated figure descriptions.
- `extracted.json`: `ai_extract` 2.1 precision-mode output with citations and
  confidence scores.
- `schema.json`: the exact extraction schema used.
- `manifest.json`: input hash, versions, timing, remote paths, and status. It
  never contains the access token.

An extraction whose fields are all null is recorded as `empty_extraction` and
returns exit code 3 even if Databricks reports no API error.

The temporary uploaded source is deleted after the run unless `--keep-upload`
is specified. Rendered page/figure images remain in the Volume path recorded in
`manifest.json` so citations can be inspected visually.

## Production pipeline

Add the following settings to `backend/.env` to make Databricks the preferred
layout parser while retaining the local parser as a fallback:

```dotenv
TRACEAUDIT_DOCUMENT_PARSER=databricks-auto
DATABRICKS_SQL_WAREHOUSE_ID=<warehouse-id>
DATABRICKS_DOCUMENT_VOLUME=/Volumes/<catalog>/<schema>/<volume>
```

The first parse of a file is cached by its SHA-256 hash under
`backend/.cache/databricks_document_ai`; later audit runs reuse that result.
The parsed tables, bounding boxes, page coordinates, and visual descriptions
are converted into the normal AudiTrace evidence-chunk contract.

Focused `ai_extract` enrichment is optional because it adds a SQL call for each
requirement. Enable it only for a measured pilot:

```dotenv
DATABRICKS_TARGETED_EXTRACTION_ENABLED=true
DATABRICKS_TARGETED_EXTRACTION_CONCURRENCY=2
DATABRICKS_TARGETED_EXTRACTION_MIN_CONFIDENCE=0.80
```

This enrichment receives only already-retrieved excerpts. Its output is
advisory: Maverick must cite the raw evidence and remains responsible for the
whole-clause decision.
