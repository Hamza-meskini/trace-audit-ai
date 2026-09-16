# TraceAudit Complex Extraction Benchmark

This synthetic benchmark isolates the two stages that determine what the audit LLM can see:

1. document ingestion and structural reconstruction;
2. requirement discovery and atomic-condition extraction.

It deliberately stops before evidence retrieval and verdict classification. The corpus contains eight four-page specifications, 48 natural-language requirements, 147 evaluator-only atomic conditions, 16 authored tables, and 12 meaningful figures or scanned pages.

The PDFs never expose `C1` / `C2`, `ALL_OF`, expected fields, or answer labels. Requirement identifiers are legitimate source identifiers, while atomic IDs and normalized logic exist only in `ground_truth.json`.

## Difficulty coverage

- ordinary body text;
- two-column text;
- requirements embedded in tables;
- scanned requirement pages requiring OCR;
- normative requirements embedded only in raster figures;
- numeric ranges, durations, triggers, alternatives, sequences, and joined obligations;
- visually dependent color, direction, geometry, and state requirements.

## Generate and validate

The generated PDFs are committed as frozen fixtures. Validate them with:

```powershell
.\backend\venv\Scripts\python.exe evaluation\run_extraction_benchmark.py --validate-only
```

Regeneration is intentionally uncommon and requires the optional authoring packages `reportlab` and `Pillow`.

## Run extraction

First measure only what survives PDF parsing; this command makes no LLM calls:

```powershell
.\backend\venv\Scripts\python.exe evaluation\run_extraction_benchmark.py --ingestion-only
```

Then isolate requirement-stage visual recovery before contract extraction:

```powershell
.\backend\venv\Scripts\python.exe evaluation\run_extraction_benchmark.py --vision-only --model system.ai.llama-4-maverick
```

Start with one document so model output and scoring can be inspected quickly:

```powershell
.\backend\venv\Scripts\python.exe evaluation\run_extraction_benchmark.py --document 01_Nova_HV_Electrical_Requirements.pdf --model system.ai.llama-4-maverick
```

Then run the frozen eight-document corpus:

```powershell
.\backend\venv\Scripts\python.exe evaluation\run_extraction_benchmark.py --model system.ai.llama-4-maverick
```

Results are written under `evaluation/results/`. The important diagnostics are requirement recall/precision, atomic recall/precision, exact field accuracy, under/over-decomposition, logic accuracy, and performance by source modality.

## Run the complete Nova audit

The version 2 corpus adds four five-page verification reports. Together, both phases contain 12 PDFs, 52 pages, 48 requirements, 147 atomic conditions, 28 authored tables, and 16 meaningful figures. Every final class is represented: supported, partial, conflict, missing, and unknown. The fifth page of each evidence report is an image-only inspection record, so the full run exercises evidence-stage vision as well as requirement-stage vision.

Validate the complete corpus without making an LLM call:

```powershell
.\backend\venv\Scripts\python.exe evaluation\run_nova_end_to_end_benchmark.py --validate-only
```

Run all stages with Llama 4 Maverick for discovery, decomposition, and verification:

```powershell
.\backend\venv\Scripts\python.exe evaluation\run_nova_end_to_end_benchmark.py --mode end-to-end --provider databricks --model system.ai.llama-4-maverick --atomic-model system.ai.llama-4-maverick --batch-size 4 --retrieval-backend auto --no-resume
```

The runner uses the production contract serializer, retaining nested logic, semantic clauses,
validation findings and relational operands. Retrieval uses production query construction,
eight candidates and the shared search/reranking service. The shared Databricks targeted
extraction stage enriches evidence before visual analysis and verification when enabled in
backend settings. `--retrieval-backend auto` follows the configured managed-search setting;
`local` selects local hybrid retrieval; `databricks` requires managed search to succeed.

Fresh extraction is the CLI default. `--no-resume` is retained for explicit, reproducible
commands; old extraction artifacts are not loaded by the CLI. Evidence discovery follows
the configured `DATABRICKS_TARGETED_EXTRACTION_ENABLED` setting and its execution counts
are printed. The result records retrieval before/after discovery, complete verification
inputs, and a runner version/hash. Ground truth remains frozen and evaluator-only in
end-to-end mode. Strict atom matching remains a diagnostic and can penalize differing
decomposition granularity; no labels are changed to accommodate predictions.

The runner also supports `oracle-contracts-evidence` and `oracle-contracts` for stage isolation.
Oracle evidence enrichment is restricted to the selected passages. Results are written to
`evaluation/results/nova_<mode>_<model>_atomic-<model>_verify-<model>_results.json` with a
companion Markdown report and an immutable copy under `evaluation/results/runs/`.
Results from this runner revision are not directly comparable to the old adapter that
dropped nested trees and omitted targeted evidence discovery.

Do not tune evidence wording or expected outcomes after inspecting a model's errors. Fix the pipeline generically, preserve this frozen corpus, and use a separately versioned holdout when Nova becomes a development target.

## Latest measured result

The latest end-to-end run completed on **15 September 2026** with runner
3.0-production-contracts and Llama 4 Maverick for discovery, atomic decomposition,
and verification.

| Metric | Result |
|---|---:|
| Final verdict accuracy | **91.67%** (44/48) |
| Final verdict macro F1 | **91.48%** |
| Retrieval Recall@3 | **100.00%** |
| Document-role accuracy | **100.00%** |
| Structured atom precision / recall / F1 | **38.26% / 38.78% / 38.51%** |
| Combined extraction + atomic-status score | **38.10%** |
| Atomic-status accuracy on aligned atoms | **98.25%** (57/147; 38.78% coverage) |
| Strict logic equivalence | **35.42%** (17/48) |
| Unsafe false auto-closes | **0** |

The run is stored at
[evaluation/results/runs/20260915T140120.543292Z](../results/runs/20260915T140120.543292Z/).
The high atomic-status number applies only after strict alignment; it is not an overall
atomic-extraction score. This run recorded zero targeted ai_extract attempts and zero
retrieved figures analyzed, so it does not measure those optional stages. Nova is a synthetic
development set and is not a held-out generalization claim.
