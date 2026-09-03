# TraceAudit FMVSS 305 public-document benchmark

- Mode: `end-to-end`
- Model: `system.ai.meta-llama-3-3-70b-instruct`
- Runtime: 635.57 seconds
- Labelled scope: 11 selected requirements; this is not a legal compliance determination.

## Diagnostic scorecard

| Stage | Metric | Result |
|---|---|---:|
| Extraction | Selected-clause recall | 90.91% |
| Extraction | Atomic-condition recall | 76.67% |
| Retrieval | Evidence page Recall@3 | 36.84% |
| Retrieval | Requirement any-hit@3 | 50.00% |
| Retrieval | Requirement full-page-coverage@3 | 20.00% |
| Source qualification | Authority accuracy | 100.00% |
| Source qualification | Authoritative qualification rate | 81.25% |
| Visual evidence | Retrieved figures analyzed | 0 |
| Visual evidence | Retrieved figures unavailable | 0 |
| LLM reasoning | Raw atomic aligned accuracy | 40.00% |
| LLM reasoning | Raw condition alignment coverage | 83.33% |
| LLM reasoning | Raw atomic end-to-end accuracy | 33.33% |
| Pipeline output | Final atomic aligned accuracy | 40.00% |
| Pipeline output | Final condition alignment coverage | 83.33% |
| Pipeline output | Final atomic end-to-end accuracy | 33.33% |
| Regulatory logic | ANY_OF / IF_THEN accuracy | 25.00% |
| Final verdict | Requirement accuracy | 36.36% |
| Final verdict | Macro F1 | 26.14% |
| Safety | Review-state accuracy | 9.09% |
| Safety | Unsafe false auto-closes | 0 |

## Requirement outcomes

| Requirement | Logic | Expected | Predicted | Review | Correct |
|---|---|---|---|---|---|
| FMVSS-305-S5.1 | ALL_OF | SUPPORTED | PARTIAL | Needs review | no |
| FMVSS-305-S5.2 | ALL_OF | SUPPORTED | MISSING | Open | no |
| FMVSS-305-S5.3 | ANY_OF | SUPPORTED | SUPPORTED | Needs review | yes |
| FMVSS-305-S6.3 | ALL_OF | PARTIAL | PARTIAL | Needs review | yes |
| FMVSS-305-S6.4 | ALL_OF | SUPPORTED | PARTIAL | Needs review | no |
| FMVSS-305-S7.1C | IF_THEN | SUPPORTED | UNKNOWN | Needs review | no |
| FMVSS-305-S7.2 | ALL_OF | SUPPORTED | SUPPORTED | Needs review | yes |
| FMVSS-305-S7.6.2 | ALL_OF | SUPPORTED | SUPPORTED | Needs review | yes |
| FMVSS-305-S7.6.6 | IF_THEN | SUPPORTED | UNKNOWN | Needs review | no |
| FMVSS-305-S5.4.1.1 | ALL_OF | UNKNOWN | MISSING | Open | no |
| FMVSS-305-S5.4.6.3 | IF_THEN | MISSING | UNKNOWN | Needs review | no |

## Interpretation

The oracle-contract modes isolate retrieval and verification from requirement extraction. The oracle-evidence modes additionally inject the manually annotated test-report passages plus only their immediate structural context, isolating source qualification, condition reasoning, regulatory logic, and aggregation. Use `end-to-end` for the product-level result and compare it with the other modes to locate regressions.
