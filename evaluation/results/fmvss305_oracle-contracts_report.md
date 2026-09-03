# TraceAudit FMVSS 305 public-document benchmark

- Mode: `oracle-contracts`
- Model: `system.ai.meta-llama-3-3-70b-instruct`
- Runtime: 1029.43 seconds
- Labelled scope: 11 selected requirements; this is not a legal compliance determination.

## Diagnostic scorecard

| Stage | Metric | Result |
|---|---|---:|
| Extraction | Selected-clause recall | 100.00% |
| Extraction | Atomic-condition recall | 100.00% |
| Retrieval | Evidence page Recall@3 | 63.16% |
| Retrieval | Requirement any-hit@3 | 80.00% |
| Retrieval | Requirement full-page-coverage@3 | 50.00% |
| Source qualification | Authority accuracy | 100.00% |
| Source qualification | Authoritative qualification rate | 81.25% |
| Visual evidence | Retrieved figures analyzed | 4 |
| Visual evidence | Retrieved figures unavailable | 0 |
| LLM reasoning | Raw atomic aligned accuracy | 76.67% |
| LLM reasoning | Raw condition alignment coverage | 100.00% |
| LLM reasoning | Raw atomic end-to-end accuracy | 76.67% |
| Pipeline output | Final atomic aligned accuracy | 76.67% |
| Pipeline output | Final condition alignment coverage | 100.00% |
| Pipeline output | Final atomic end-to-end accuracy | 76.67% |
| Regulatory logic | ANY_OF / IF_THEN accuracy | 75.00% |
| Final verdict | Requirement accuracy | 81.82% |
| Final verdict | Macro F1 | 88.10% |
| Safety | Review-state accuracy | 54.55% |
| Safety | Unsafe false auto-closes | 0 |

## Requirement outcomes

| Requirement | Logic | Expected | Predicted | Review | Correct |
|---|---|---|---|---|---|
| FMVSS-305-S5.1 | ALL_OF | SUPPORTED | CONFLICT | Needs review | no |
| FMVSS-305-S5.2 | ALL_OF | SUPPORTED | SUPPORTED | Needs review | yes |
| FMVSS-305-S5.3 | ANY_OF | SUPPORTED | SUPPORTED | Reviewed | yes |
| FMVSS-305-S6.3 | ALL_OF | PARTIAL | PARTIAL | Needs review | yes |
| FMVSS-305-S6.4 | ALL_OF | SUPPORTED | SUPPORTED | Needs review | yes |
| FMVSS-305-S7.1C | IF_THEN | SUPPORTED | SUPPORTED | Needs review | yes |
| FMVSS-305-S7.2 | ALL_OF | SUPPORTED | SUPPORTED | Reviewed | yes |
| FMVSS-305-S7.6.2 | ALL_OF | SUPPORTED | SUPPORTED | Reviewed | yes |
| FMVSS-305-S7.6.6 | IF_THEN | SUPPORTED | UNKNOWN | Needs review | no |
| FMVSS-305-S5.4.1.1 | ALL_OF | UNKNOWN | UNKNOWN | Needs review | yes |
| FMVSS-305-S5.4.6.3 | IF_THEN | MISSING | MISSING | Open | yes |

## Interpretation

The oracle-contract modes isolate retrieval and verification from requirement extraction. The oracle-evidence modes additionally inject the manually annotated test-report passages plus only their immediate structural context, isolating source qualification, condition reasoning, regulatory logic, and aggregation. Use `end-to-end` for the product-level result and compare it with the other modes to locate regressions.
