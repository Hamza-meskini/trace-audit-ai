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
.\backend\venv\Scripts\python.exe evaluation\run_nova_end_to_end_benchmark.py --mode end-to-end --provider databricks --model system.ai.llama-4-maverick --atomic-model system.ai.llama-4-maverick --batch-size 4
```

The runner also supports `oracle-contracts-evidence` and `oracle-contracts` for stage isolation. Results are written to `evaluation/results/nova_<mode>_<model>_results.json` with a companion Markdown report.

Do not tune evidence wording or expected outcomes after inspecting a model's errors. Fix the pipeline generically, preserve this frozen corpus, and use a separately versioned holdout when Nova becomes a development target.
