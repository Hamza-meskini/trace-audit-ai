# Atomic evaluation policy, version 3

This is a deterministic structured-agreement evaluator, not an expert-certified
semantic judge. Nova is a synthetic development corpus. Neither the corpus nor
the evaluator has yet been independently validated on held-out enterprise data.

## What is measured

- **Structured atom precision/recall/F1:** one-to-one identity alignment followed
  by agreement on operator, value, unit, applicability/verification role,
  mandatory flag, and reference-annotated scope/method. Extra predictions count
  in precision, including atoms on unexpected requirements. Ambiguous mappings
  get no automatic credit. An unmatched atom is unresolved; it is not necessarily
  missing or hallucinated.
- **Routing metadata:** visual-evidence flags are measured separately. They do
  not decide whether a voltage limit or other obligation has been preserved.
- **Granularity:** one original predicate is one atom. An explicit inclusive
  interval is one original atom. A separate normalized-constraint diagnostic
  expands intervals into lower and upper predicates. It does not interpret
  operating-range coverage as pointwise bounds or copy a combined verification
  result onto two reference atoms. Other split/merged representations require
  review and are not silently credited.
- **Logic:** truth-table equivalence after complete, unambiguous condition
  alignment, up to 12 original conditions. Nested trees are used when available;
  otherwise the recorded flat logic is used. Unknown mappings are reported as
  unevaluable, counted in the overall denominator. Operator-name agreement alone
  is insufficient. This checks Boolean structure, not the entire semantics of
  evidence aggregation with unknown or untested states.
- **Atomic status accuracy on eligible atoms:** correct statuses divided by
  structurally equivalent extracted atoms. Missing, duplicate-ID, and invalid
  results are failures within this denominator. Always display eligibility
  coverage alongside this score.
- **Combined extraction + atomic status score:** an atom earns credit only if
  both its extracted structure and its verification status pass, divided by all
  reference atoms. This must not be presented as pure reasoning accuracy.
- **Final verdict accuracy:** remains a separate requirement-level measurement.

## Matching and independence

`atomic_evaluation.py` uses parameter, source-parameter, source-span and description
tokens for candidate identity (at least 0.65 overlap on one field comparison).
Global assignment uses identity and structured field agreement. It never uses
expected statuses, evidence annotations, output statuses, reference condition
numbers, or positions to find matches. Equal-best ambiguous assignments are
withheld. This lexical identity step can still make mistakes; every pair includes
its checks so reviewers can inspect it.

Common unit conversions are explicit. Unknown units require literal agreement.
AC/DC and RMS qualifications are preserved. Boolean False is distinct from zero;
an inequality cannot pass merely because its bound matches. Negative reference
prose with a False predicate does not require repeating the negative word in a
model's short parameter description. Arbitrary predicate antonyms and free-text
value paraphrases are not automatically assumed equivalent.

Verification results attach to the model's own local condition IDs only *after*
the contracts are aligned. IDs are used for this internal trace, not to establish
equivalence with the answer key. The pipeline receives only its own extracted
contracts in end-to-end mode. Oracle modes deliberately supply reference
contracts/evidence and do not measure extraction performance.

## Reproducibility and review

New end-to-end results contain a copy of the actual contracts supplied to
verification, scorer and reference hashes, explicit metric definitions, and an
immutable timestamped run under `evaluation/results/runs/`. Existing latest-result
paths remain available. Test invariants before changing the scoring version:
equivalent units/formatting, deliberately wrong bounds/operators, lost
prerequisites, extra/duplicate atoms, altered logic, answer-label permutation,
result-ID traceability, and reference self-agreement.

Re-score a new saved run without making any provider requests:

```powershell
.\backend\venv\Scripts\python.exe evaluation/rescore_atomic_benchmark.py PATH_TO_RESULTS.json --output NEW_RESCORE.json
```

For an older run without embedded contracts, add `--extraction` with its original
extraction JSON. This is explicitly marked as a legacy reconstruction because
the old artifacts cannot prove their historical pairing cryptographically.
Output files are created exclusively; re-scoring does not overwrite originals.

Before claiming semantic accuracy, have an independent domain reviewer resolve
the unmatched/ambiguous pairs and representation disagreements against the source
requirements. Validate the evaluator against those decisions, freeze the policy,
and evaluate an unseen document set. Do not raise a score by changing matching
thresholds or the reference labels after seeing a run.
