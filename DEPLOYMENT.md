# Deployment notes (Databricks Apps)

The app is deployed from `app.yaml`: it installs Bun, builds the frontend,
runs `backend/scripts/migrate_database.py` and starts uvicorn. This file lists
the security-relevant settings and the owner actions still open.

## Configuration that controls customer data

| Setting | Default | Purpose |
| --- | --- | --- |
| `EGRESS_ALLOWED_HOSTS` | `[]` | Extra outbound hosts. The Databricks workspace host is always allowed; everything else is blocked (`services/egress.py`). Gemini embeddings need `["generativelanguage.googleapis.com"]`; without it retrieval is BM25-only, which is **not** the configuration the Nova benchmarks measured. |
| `EGRESS_ENFORCED` | `true` | Set `false` only for local experiments. |
| `LLM_DATABRICKS_ONLY` | `true` | Every LLM stage stays on Databricks Model Serving (Maverick). |
| `TRUST_FORWARDED_IDENTITY` | `auto` | Review sign-off uses the SSO identity from the Apps proxy; `auto` trusts the `X-Forwarded-*` headers only inside the Apps runtime. |
| `SETTINGS_ADMINS` | `[]` | SSO users (user id or email, JSON list) allowed to change the global AI model and thinking level. Empty means nobody can change them behind SSO; changes are also refused while an audit runs. |
| `LLM_RECORD_DIR` | empty (off) | Records raw model requests and answers for evaluation (EVA-03). Holds customer text; must be outside the git checkout. |
| `LOG_PROVIDER_BODIES` / `SQL_ECHO` | `false` | Keep provider bodies, model output and SQL parameters (document text) out of logs. |
| `MAX_REQUEST_BODY_MB` / `MAX_UPLOAD_FILES_PER_REQUEST` | `250` / `25` | Upload request caps (each file is also capped at 50 MB). |
| `CORS_ORIGINS` | localhost dev origins | Only the local Vite frontend calls the API cross-origin; no credentials are allowed. |
| `UPLOAD_DIR` | `./uploads` | Originals, the pipeline cache and audit progress. See "Storage" below. |

## Deletion

Deleting a document or project removes its rows and then erases the original,
the local Databricks parse cache, the parsed figure images in
`DATABRICKS_DOCUMENT_VOLUME`, cached embedding vectors and reranker cache
entries (`services/data_purge.py`). Content still used by another document is
kept. The purge logs counts only.

Visitor leads: the public capture endpoint was removed. Existing rows can be
exported and erased with `backend/scripts/purge_visitor_leads.py` (owner
decision; the table is not dropped).

## Storage (SEC-04, preparation only)

`UPLOAD_DIR` is relative by default, so originals live on the app's local
disk next to the working directory. Whether that disk survives an Apps
restart or redeploy has not been verified. Before relying on it:

1. Run a restart test in staging: upload a document, restart the app, and
   download the original (`GET /api/projects/{id}/documents/{doc}/file`).
2. If the original is gone, point `UPLOAD_DIR` at durable storage (for
   example a Unity Catalog Volume path, if the Apps runtime mounts it) and
   migrate existing `documents.storage_path` values. Chunks and verdicts are
   in the database and are not affected; re-parsing and downloads are.

## Authentication (SEC-06, preparation only)

`app.yaml` injects `DATABRICKS_TOKEN` from a secret, and the token takes
precedence over the app's service principal (`DATABRICKS_CLIENT_ID` /
`DATABRICKS_CLIENT_SECRET`). Recommended: grant the app service principal
only the warehouse, serving endpoints and Volume it needs, remove the
`DATABRICKS_TOKEN` entry, and confirm the app works with the token unset.
Logs redact tokens and `client_secret=` values either way.

## Releases (SEC-08, preparation only)

The Bun installer is pinned to the version CI uses (1.4.2); the frontend is
still built at start-up. Target state: build `dist/client` in CI, deploy a
tagged commit, and skip the network fetch at start. Python dependencies are
pinned (`requirements.txt`) with the full closure in `requirements.lock`.

## Owner actions

- **SEC-01**: rotate the Gemini API key (it could have reached logs through
  key-in-URL requests on `main`) and merge the hardening branches into `main`
  with ordinary merges.
- Decide whether Google may receive document text for embeddings
  (`EGRESS_ALLOWED_HOSTS`), and whether other third-party processors are
  allowed at all.
- Export or discard visitor leads, then run the purge script.
- Check the PyMuPDF licence (AGPL) for the deployment model.
