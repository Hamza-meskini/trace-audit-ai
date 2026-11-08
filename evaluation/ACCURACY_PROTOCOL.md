# Accuracy experiment protocol (pre-registered 8 November 2026)

This file is committed before any candidate change is scored, so the metrics and decision
rules cannot be chosen after seeing results.

## Data

| Set | Role | Size | How it is scored |
|---|---|---|---|
| Nova replay | **Development set**: iterate here | 5 saved runs × 48 requirements = 240 instances | Offline replay: recorded LLM responses are fed through the current Python code. No network, no cost |
| Hard benchmark (`evaluation/hard_benchmark`, new) | **Held-out test set**: built and frozen before any candidate is scored on it | about 48 requirements | Live end-to-end runs |
| Nova live | Regression check | 48 requirements | Live end-to-end runs |

Labels in the hard benchmark are checked by independent agents who see only the generated
PDFs and the requirement text. Any disagreement is resolved by fixing the document or the
label before the set is frozen. Its ground truth is never changed after the first scored
run.

## Metrics

**Primary:** final-verdict accuracy and macro F1 over the five classes (SUPPORTED, PARTIAL,
CONFLICT, MISSING, UNKNOWN), as computed by the existing scorers.

**Secondary:**

- CONFLICT recall;
- auto-close count (`Reviewed`);
- **unsafe false auto-closes, which must stay 0**: an auto-closed requirement whose verdict
  is wrong;
- per-class recall.

**Every comparison reports paired flips**: per instance, fixed (wrong → right),
broken (right → wrong) and unchanged. The net change alone is not enough.

## Decision rules

1. **Replay fidelity gate.**
   - Unmodified current code replayed over the 240 instances must reproduce the saved
     predicted status for at least 95% of them.
   - Every mismatch must be explained, either by a code change since the run or by a known
     gap in the mock.
   - No candidate number is reported until this gate passes.
2. **A candidate advances to live runs** only if, on the replay, it:
   - fixes more instances than it breaks, with a net gain of at least 3;
   - adds no unsafe false auto-close;
   - passes the full backend test suite.
3. **A candidate is accepted** only if:
   - its mean live accuracy over at least 3 runs on the hard benchmark beats the baseline
     mean by more than the baseline's run-to-run standard deviation;
   - the Nova live mean does not drop by more than one baseline standard deviation;
   - it adds no unsafe false auto-close in any run.
4. **Trust fixes are reported separately.** These are changes that make auto-closing
   stricter (citation grounding, checking observed values against quotes). They can lower
   accuracy by pushing items to "needs review". They are judged on unsafe auto-closes and
   on adversarial probes, and their accuracy cost is reported as a trade-off rather than
   used to reject them.
5. **Stopping rule.** Stop when either:
   - the live hard-benchmark result confirms the replay winner within the measured spread; or
   - two consecutive candidate rounds produce no replay improvement.

## Live-run hygiene

- Every live run sets `DATABRICKS_MLFLOW_TRACING_ENABLED=false` and `PYTHONUTF8=1`.
- Runs use the same models (Llama 4 Maverick for every role), temperature 0, batch size 4
  and local retrieval as the saved runs, unless the model itself is the variable under test.
- Repeated runs must not reuse cached LLM outputs. Any cache that would make two runs
  identical is disabled or keyed per run.
- New runs land in `evaluation/results/runs/` and become replay fixtures too.
