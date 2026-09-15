"""Create the Databricks Delta table, AI Search endpoint, and Delta Sync index.

Environment variables are read from backend/.env through app.config. This is a
one-time provisioning command; normal audits only update rows and trigger sync.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import timedelta
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings
from app.services.databricks_ai_search import (
    _search_config,
    _workspace_client,
    _write_source_table,
)


def provision(*, wait_seconds: float = 900.0) -> dict[str, object]:
    from databricks.sdk.service.vectorsearch import (
        DeltaSyncVectorIndexSpecRequest,
        EmbeddingSourceColumn,
        EndpointType,
        PipelineType,
        VectorIndexType,
    )

    endpoint_name, index_name, source_table = _search_config()
    embedding_model = settings.DATABRICKS_AI_SEARCH_EMBEDDING_MODEL
    if not embedding_model:
        raise RuntimeError("DATABRICKS_AI_SEARCH_EMBEDDING_MODEL is required")

    # Creating the source table first gives the index a stable schema.
    _write_source_table("__auditrace_setup__", [])
    workspace = _workspace_client()

    endpoint_names = {
        str(getattr(item, "name", "") or "")
        for item in workspace.vector_search_endpoints.list_endpoints()
    }
    endpoint_created = endpoint_name not in endpoint_names
    if endpoint_created:
        wait = workspace.vector_search_endpoints.create_endpoint(
            name=endpoint_name,
            endpoint_type=EndpointType.STANDARD,
        )
        if hasattr(wait, "result"):
            wait.result(timeout=timedelta(seconds=max(1.0, wait_seconds)))

    index_created = False
    try:
        workspace.vector_search_indexes.get_index(index_name=index_name)
    except Exception:
        workspace.vector_search_indexes.create_index(
            name=index_name,
            endpoint_name=endpoint_name,
            primary_key="chunk_id",
            index_type=VectorIndexType.DELTA_SYNC,
            delta_sync_index_spec=DeltaSyncVectorIndexSpecRequest(
                source_table=source_table,
                pipeline_type=PipelineType.TRIGGERED,
                columns_to_sync=[
                    "project_id", "document_id", "document_name", "doc_type",
                    "page_number", "chunk_to_retrieve", "metadata_json",
                ],
                embedding_source_columns=[EmbeddingSourceColumn(
                    name="chunk_to_embed",
                    embedding_model_endpoint_name=embedding_model,
                    model_endpoint_name_for_query=embedding_model,
                )],
            ),
        )
        index_created = True

    deadline = time.monotonic() + max(0.0, wait_seconds)
    status = None
    while time.monotonic() < deadline:
        info = workspace.vector_search_indexes.get_index(index_name=index_name)
        status = getattr(info, "status", None)
        if getattr(status, "ready", False):
            break
        time.sleep(5)

    return {
        "endpoint": endpoint_name,
        "endpoint_created": endpoint_created,
        "index": index_name,
        "index_created": index_created,
        "source_table": source_table,
        "embedding_model": embedding_model,
        "ready": bool(getattr(status, "ready", False)),
        "status_message": getattr(status, "message", None),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait-seconds", type=float, default=900.0)
    parser.add_argument(
        "--show-config",
        action="store_true",
        help="Validate and display resource names without creating anything.",
    )
    args = parser.parse_args()
    endpoint, index, table = _search_config()
    if args.show_config:
        print({
            "endpoint": endpoint,
            "index": index,
            "source_table": table,
            "embedding_model": settings.DATABRICKS_AI_SEARCH_EMBEDDING_MODEL,
        })
        return
    result = provision(wait_seconds=max(0.0, args.wait_seconds))
    print(result)
    if not result["ready"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
