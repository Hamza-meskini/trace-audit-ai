# TraceAudit FMVSS 305 public-document benchmark

- Mode: `end-to-end`
- Model: `system.ai.meta-llama-3-3-70b-instruct`
- Runtime: 204.91 seconds
- Labelled scope: 11 selected requirements; this is not a legal compliance determination.

## Diagnostic scorecard

| Stage | Metric | Result |
|---|---|---:|
| Extraction | Selected-clause recall | 100.00% |
| Extraction | Atomic-condition recall | 23.33% |
| Retrieval | Evidence page Recall@3 | 80.00% |
| Source qualification | Authority accuracy | 0.00% |
| Source qualification | Authoritative qualification rate | 0.00% |
| LLM reasoning | Raw atomic accuracy | 10.00% |
| Pipeline output | Final atomic accuracy | 10.00% |
| Regulatory logic | ANY_OF / IF_THEN accuracy | 0.00% |
| Final verdict | Requirement accuracy | 9.09% |
| Final verdict | Macro F1 | 5.56% |
| Safety | Review-state accuracy | 18.18% |
| Safety | Unsafe false auto-closes | 0 |

## Requirement outcomes

| Requirement | Logic | Expected | Predicted | Review | Correct |
|---|---|---|---|---|---|
| FMVSS-305-S5.1 | ALL_OF | SUPPORTED | UNKNOWN | Needs review | no |
| FMVSS-305-S5.2 | ALL_OF | SUPPORTED | UNKNOWN | Needs review | no |
| FMVSS-305-S5.3 | ANY_OF | SUPPORTED | UNKNOWN | Needs review | no |
| FMVSS-305-S6.3 | ALL_OF | PARTIAL | UNKNOWN | Needs review | no |
| FMVSS-305-S6.4 | ALL_OF | SUPPORTED | UNKNOWN | Needs review | no |
| FMVSS-305-S7.1C | IF_THEN | SUPPORTED | MISSING | Open | no |
| FMVSS-305-S7.2 | ALL_OF | SUPPORTED | MISSING | Open | no |
| FMVSS-305-S7.6.2 | ALL_OF | SUPPORTED | SUPPORTED | Reviewed | yes |
| FMVSS-305-S7.6.6 | IF_THEN | SUPPORTED | MISSING | Open | no |
| FMVSS-305-S5.4.1.1 | ALL_OF | UNKNOWN | MISSING | Open | no |
| FMVSS-305-S5.4.6.3 | IF_THEN | MISSING | UNKNOWN | Needs review | no |

## Interpretation

The oracle-contract modes isolate retrieval and verification from requirement extraction. The oracle-evidence modes additionally inject only the manually annotated test-report pages, isolating source qualification, condition reasoning, regulatory logic, and aggregation. Use `end-to-end` for the product-level result and compare it with the other modes to locate regressions.
