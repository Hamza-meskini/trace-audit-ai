# Databricks AI Search experiment

The managed retrieval path is optional. Local hybrid retrieval remains the
default control, and an explicitly requested Databricks run fails if the index
cannot be queried. This prevents a fallback from being reported as an AI Search
benchmark.

## One-time setup

Set the Databricks host, token, SQL warehouse, Unity Catalog source table,
index, endpoint, and embedding endpoint in `backend/.env`, then run:

```powershell
.\backend\venv\Scripts\python.exe .\evaluation\setup_databricks_ai_search.py
```

The configured index uses `chunk_id` as its primary key,
`chunk_to_embed` for managed GTE embeddings, and preserves
`chunk_to_retrieve` for citations. It is a triggered Delta Sync index.

## Fair FMVSS A/B test

Keep `DATABRICKS_AI_PREP_SEARCH_ENABLED=false` for both runs so only retrieval
changes. Run the local control:

```powershell
.\backend\venv\Scripts\python.exe .\evaluation\run_fmvss305_benchmark.py --mode end-to-end --provider databricks --model system.ai.llama-4-maverick --batch-size 1 --scope-file .\evaluation\fmvss305_benchmark\selected_clause_scope.json --retrieval-backend local
```

Then run the managed candidate:

```powershell
.\backend\venv\Scripts\python.exe .\evaluation\run_fmvss305_benchmark.py --mode end-to-end --provider databricks --model system.ai.llama-4-maverick --batch-size 1 --scope-file .\evaluation\fmvss305_benchmark\selected_clause_scope.json --retrieval-backend databricks
```

Run the same candidate with the deployed BGE cross-encoder:

```powershell
.\backend\venv\Scripts\python.exe .\evaluation\run_fmvss305_benchmark.py --mode end-to-end --provider databricks --model system.ai.llama-4-maverick --batch-size 1 --scope-file .\evaluation\fmvss305_benchmark\selected_clause_scope.json --retrieval-backend databricks --custom-reranker
```

Compare evidence Recall@3, requirement any-hit@3, verdict accuracy, runtime,
and the `retrieval_backend` diagnostics stored in each immutable result.
The adapter records whether the Databricks reranker actually ran. If workspace
policy blocks that model, it retries the same managed hybrid query without the
reranker and caches the capability failure for the rest of the process.
`--custom-reranker` instead sends the bounded candidate set to the configured
`bge-reranker-v2-m3` Model Serving endpoint and fails the benchmark if that
explicitly requested endpoint is unavailable or returns misaligned scores.

## Test `ai_prep_search`

After the retrieval-only comparison, set
`DATABRICKS_AI_PREP_SEARCH_ENABLED=true` and repeat both runs. The parser keeps
raw `chunk_to_retrieve` text for citations, uses context-enriched
`chunk_to_embed` text for search, and preserves figure chunks for the vision
stage. Set `DATABRICKS_AI_PREP_SEARCH_REQUIRED=true` only when a run must fail
instead of falling back to structure-aware local chunks.

## Production enablement

Set `DATABRICKS_AI_SEARCH_ENABLED=true` only if the managed run improves the
chosen retrieval metrics without unacceptable latency. Production catches a
managed sync or query failure and uses local hybrid retrieval while recording
the fallback reason.

For MLflow traces and benchmark metrics, install
`backend/requirements-mlflow.txt`, set an experiment path, and enable
`DATABRICKS_MLFLOW_TRACING_ENABLED=true`.
