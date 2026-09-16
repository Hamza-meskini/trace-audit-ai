# FMVSS 305 public-document benchmark

This is a focused, manually labelled regression benchmark built from two public
documents in `sample_documents/`:

- `FMVSS_305_Requirements_2024-11-27.pdf` — the historical 49 CFR 571.305
  regulatory requirements used as the specification source.
- `305-2400152-TEST.pdf` — a NHTSA/Calspan FMVSS 305 laboratory test report used
  as the evidence source.

The benchmark covers 11 selected requirements and 30 atomic conditions. It is
designed to diagnose TraceAudit; it is not an exhaustive annotation of FMVSS 305
and is not a legal compliance determination.

## Validate without an LLM

```powershell
.\backend\venv\Scripts\python.exe evaluation\run_fmvss305_benchmark.py --validate-only
```

This checks the label schema, source files, page references, and evidence quotes.

## Run modes

Start with the most isolated mode:

```powershell
.\backend\venv\Scripts\python.exe evaluation\run_fmvss305_benchmark.py --mode oracle-contracts-evidence
```

Then progressively enable more of the real pipeline:

```powershell
# Ground-truth contracts, real retrieval
.\backend\venv\Scripts\python.exe evaluation\run_fmvss305_benchmark.py --mode oracle-contracts

# LLM-extracted contracts, annotated evidence pages
.\backend\venv\Scripts\python.exe evaluation\run_fmvss305_benchmark.py --mode oracle-evidence

# LLM extraction, real retrieval, and production verification
.\backend\venv\Scripts\python.exe evaluation\run_fmvss305_benchmark.py --mode end-to-end
```

An explicit model can be tested without changing backend configuration:

```powershell
.\backend\venv\Scripts\python.exe evaluation\run_fmvss305_benchmark.py `
  --mode end-to-end `
  --model system.ai.meta-llama-3-3-70b-instruct
```

Each run writes a JSON result and a Markdown report under `evaluation/results/`,
with the mode in the filename.

## What is measured

- selected regulatory-clause extraction recall;
- atomic-condition extraction recall;
- evidence-page Recall@1, Recall@3, Recall@5, and MRR;
- test-report source-authority recognition and qualification;
- raw LLM atomic-condition alignment coverage, aligned accuracy, and end-to-end accuracy;
- final post-pipeline condition alignment coverage, aligned accuracy, and end-to-end accuracy;
- `ANY_OF` and `IF_THEN` regulatory-logic accuracy;
- requirement-level accuracy and Macro F1;
- review-state accuracy, false `SUPPORTED`, and unsafe false auto-closes.

The expected labels and expected evidence are evaluator-only fields. The runner
removes them before invoking the production extraction, retrieval, or verification
services, preventing benchmark-answer leakage into LLM prompts.

The evidence PDF is content-profiled before qualification. Its stored profile
contains role, confidence, verification basis, standards, and page/quote cues;
the filename is only a weak fallback. Document authority, passage modality, and
requirement relevance are scored independently.

## Latest measured scoped end-to-end result

The latest saved scoped run used Llama 3.3 70B with local-hybrid retrieval. It assessed
11 selected clauses and 30 labelled atomic conditions from the public documents.

| Metric | Result |
|---|---:|
| Requirement accuracy | **90.91%** (10/11) |
| Requirement macro F1 | **90.00%** |
| Selected-clause extraction recall | **100.00%** |
| Evidence-page Recall@3 | **78.95%** |
| Targeted evidence enrichments | **10/11** |
| Final atomic alignment coverage | **10.00%** |
| Final atomic accuracy on aligned conditions | **100.00%** |
| Final atomic end-to-end accuracy | **10.00%** |
| Unsafe false auto-closes | **0** |

The aligned-condition accuracy covers only a small portion of the labelled atomic set and
must not be read as 100% condition-level pipeline accuracy. The run report is
[evaluation/results/fmvss305_end-to-end_scoped_report.md](../results/fmvss305_end-to-end_scoped_report.md).

## How to interpret mode differences

- If `oracle-contracts-evidence` is weak, the problem is downstream: source
  authority, condition reasoning, regulatory logic, aggregation, or review gating.
- If that mode is strong but `oracle-contracts` is weak, retrieval is the main gap.
- If `oracle-contracts-evidence` is strong but `oracle-evidence` is weak,
  requirement/condition extraction is the main gap.
- `end-to-end` is the product-level score and should be compared against all three
  diagnostic modes before changing the pipeline.
