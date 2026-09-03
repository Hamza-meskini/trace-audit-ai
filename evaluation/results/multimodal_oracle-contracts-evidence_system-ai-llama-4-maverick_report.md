# TraceAudit Multimodal Diagnostic Benchmark

- Mode: `oracle-contracts-evidence`
- Model: `system.ai.llama-4-maverick`
- Final accuracy: **100.00%**
- Final atomic accuracy: **100.00%**
- Document role accuracy: **100.00%**
- Retrieval Recall@3 (document + page): **87.50%**
- Unsafe false auto-closes: **0**

## Requirement results

| Requirement | Logic | Expected | Predicted | Review | Correct |
|---|---|---|---|---|---|
| MM-REQ-001 | ALL_OF | SUPPORTED | SUPPORTED | Reviewed | yes |
| MM-REQ-002 | ALL_OF | PARTIAL | PARTIAL | Needs review | yes |
| MM-REQ-003 | ALL_OF | SUPPORTED | SUPPORTED | Needs review | yes |
| MM-REQ-004 | ALL_OF | SUPPORTED | SUPPORTED | Reviewed | yes |
| MM-REQ-005 | ALL_OF | CONFLICT | CONFLICT | Needs review | yes |
| MM-REQ-006 | ALL_OF | UNKNOWN | UNKNOWN | Needs review | yes |
| MM-REQ-007 | ALL_OF | PARTIAL | PARTIAL | Needs review | yes |
| MM-REQ-008 | ALL_OF | SUPPORTED | SUPPORTED | Reviewed | yes |
| MM-REQ-009 | ALL_OF | CONFLICT | CONFLICT | Needs review | yes |
| MM-REQ-010 | ALL_OF | CONFLICT | CONFLICT | Needs review | yes |
| MM-REQ-011 | IF_THEN | MISSING | MISSING | Open | yes |
| MM-REQ-012 | ANY_OF | SUPPORTED | SUPPORTED | Needs review | yes |

## Interpretation

`oracle-contracts-evidence` isolates document profiling, visual understanding, condition reasoning and aggregation. `oracle-contracts` adds real retrieval. `end-to-end` adds requirement and atomic-condition extraction.