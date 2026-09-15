# Contract construction

The default is `ATOMIC_CONSTRUCTION_MODE=direct`: discovery identifies requirements,
then one structured LLM request per requirement returns a shallow V2 draft containing
grounded clauses, flat conditions and flat `logic_nodes`. Python assigns stable clause
and condition IDs, rebuilds clause coverage, and compiles the graph into the typed nested
`logic_tree`. The model's `contract_complete` opinion is ignored; deterministic structural,
grounding, coverage and semantic checks derive completeness. The existing `planned` mode
remains available for controlled comparisons.

Malformed legacy responses are salvaged before another LLM request is spent. In particular,
condition fields accidentally embedded in a recursive `logic_tree` are recovered into the
flat condition array and the tree is migrated into `logic_nodes`. A request that cannot be
salvaged may use the single correction budget. If the first draft is structurally valid but
fails semantic validation, the same budget is used for one targeted repair. Direct mode
therefore follows one normal request plus at most one corrective request, excluding an
explicitly configured provider/model fallback.

Independent requirements are constructed concurrently under the shared
`ATOMIC_CONTRACT_CONCURRENCY` semaphore (default 4). Repairs use the same semaphore. Result
ordering remains deterministic because `asyncio.gather` preserves discovery order.
Accepted drafts persist `contract_schema_version=2.0` and a SHA-256 fingerprint of the
normalized authoritative source. These fields make cached/reused contracts auditable and
allow callers to invalidate them when either the source or compiler version changes.

The tree governs verification prompts, Boolean retries, aggregation and review proof
paths. Legacy flat logic is derived only when it represents the tree without loss.
Runtime consumers fall back to flat logic only for older contracts without a tree.
Normalization does not reclassify omitted obligations as applicability conditions. It also
detects missing alternative logic for “one of the following”/“either … or” clauses and
requires applicability conditions to be governed by an `IF_THEN` gate. Malformed graphs,
unreachable nodes, cycles, source-grounding defects and missing obligations block
authoritative aggregation.

Databricks construction diagnostics retain raw responses, schema errors, token usage
and finish reasons when the endpoint supplies them. Fresh FMVSS `contracts.json`
archives include these diagnostics. Treat those archives as document-bearing data.
HTTP 400 errors disable schema mode only when they explicitly report unsupported
response formatting. Output truncation is reported separately from schema validation.

For a valid before/after comparison, run fresh extraction with the same documents,
model, evidence retrieval settings and clause scope. Do not resume old contracts to
measure the new construction path. Oracle-contract runs bypass this change. Gold
labels and atomic scoring rules have not changed.

Local validation does not establish an accuracy improvement. Source coverage and
semantic correctness still need a fresh benchmark and unseen document evaluation.
