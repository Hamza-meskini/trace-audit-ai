# TraceAudit FMVSS 305 public-document benchmark

- Mode: `oracle-contracts-evidence`
- Model: `system.ai.meta-llama-3-3-70b-instruct`
- Runtime: 130.31 seconds
- Labelled scope: 11 selected requirements; this is not a legal compliance determination.

## Diagnostic scorecard

| Stage | Metric | Result |
|---|---|---:|
| Extraction | Selected-clause recall | 100.00% |
| Extraction | Atomic-condition recall | 100.00% |
| Retrieval | Evidence page Recall@3 | 70.00% |
| Source qualification | Authority accuracy | 100.00% |
| Source qualification | Authoritative qualification rate | 36.84% |
| LLM reasoning | Raw atomic aligned accuracy | 45.83% |
| LLM reasoning | Raw condition alignment coverage | 80.00% |
| LLM reasoning | Raw atomic end-to-end accuracy | 36.67% |
| Pipeline output | Final atomic aligned accuracy | 50.00% |
| Pipeline output | Final condition alignment coverage | 86.67% |
| Pipeline output | Final atomic end-to-end accuracy | 43.33% |
| Regulatory logic | ANY_OF / IF_THEN accuracy | 50.00% |
| Final verdict | Requirement accuracy | 45.45% |
| Final verdict | Macro F1 | 38.64% |
| Safety | Review-state accuracy | 45.45% |
| Safety | Unsafe false auto-closes | 0 |

## Requirement outcomes

| Requirement | Logic | Expected | Predicted | Review | Correct |
|---|---|---|---|---|---|
| FMVSS-305-S5.1 | ALL_OF | SUPPORTED | SUPPORTED | Reviewed | yes |
| FMVSS-305-S5.2 | ALL_OF | SUPPORTED | PARTIAL | Needs review | no |
| FMVSS-305-S5.3 | ANY_OF | SUPPORTED | UNKNOWN | Needs review | no |
| FMVSS-305-S6.3 | ALL_OF | PARTIAL | PARTIAL | Needs review | yes |
| FMVSS-305-S6.4 | ALL_OF | SUPPORTED | PARTIAL | Needs review | no |
| FMVSS-305-S7.1C | IF_THEN | SUPPORTED | SUPPORTED | Reviewed | yes |
| FMVSS-305-S7.2 | ALL_OF | SUPPORTED | SUPPORTED | Reviewed | yes |
| FMVSS-305-S7.6.2 | ALL_OF | SUPPORTED | PARTIAL | Needs review | no |
| FMVSS-305-S7.6.6 | IF_THEN | SUPPORTED | PARTIAL | Needs review | no |
| FMVSS-305-S5.4.1.1 | ALL_OF | UNKNOWN | MISSING | Open | no |
| FMVSS-305-S5.4.6.3 | IF_THEN | MISSING | MISSING | Open | yes |

## Interpretation

The oracle-contract modes isolate retrieval and verification from requirement extraction. The oracle-evidence modes additionally inject only the manually annotated test-report pages, isolating source qualification, condition reasoning, regulatory logic, and aggregation. Use `end-to-end` for the product-level result and compare it with the other modes to locate regressions.
