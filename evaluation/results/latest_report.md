# TRACEAUDIT BENCHMARK REPORT

**Execution Timestamp:** 2026-08-21T14:45:15.131167+00:00  
**Model / Engine Evaluated:** `gemini-3.7-flash`  
**Benchmark Dataset:** `Automotive Battery Control Unit (BCU-800V)`  

---

## 1. Dataset Overview

| Metric | Count |
|---|---|
| Ground Truth Requirements | **30** |
| Synthetic Technical Documents | **7** |
| Indexed Evidence Chunks | **70** |
| Verified (Supported) Cases in GT | **10** |
| Partial Coverage Cases in GT | **8** |
| Missing Evidence Cases in GT | **7** |
| Contradiction Cases in GT | **5** |

## 2. Executive Metrics Summary

| Evaluation Dimension | Primary Metric | Score | Target Status |
|---|---|---|---|
| **Requirement Extraction** | F1 Score | **98.36%** | 🟢 PASS |
| **Evidence Retrieval** | Recall@3 | **82.61%** | 🟢 PASS |
| **Evidence Retrieval** | Mean Reciprocal Rank (MRR) | **0.5833** | 🟡 REVIEW |
| **Verification Classification** | Accuracy | **53.33%** | 🟡 REVIEW |
| **Verification Classification** | Macro F1 | **56.21%** | 🟡 REVIEW |
| **Contradiction Detection** | Conflict F1 | **45.45%** | 🟡 REVIEW |
| **Missing Evidence Detection** | Missing F1 | **88.89%** | 🟢 PASS |
| **Unsupported Claim Rate (Hallucination)** | Rate on Missing Evidence | **28.57%** | 🔴 FAIL |

## 3. Stage-by-Stage Performance

### 3.1 Requirement Extraction
- **Precision:** 96.77%
- **Recall:** 100.0%
- **F1 Score:** 98.36%
- Extracted: 31 clauses vs Ground Truth: 30

### 3.2 Evidence Retrieval (Top-K)
- **Recall@1:** 34.78% (first retrieved chunk contains ground truth)
- **Recall@3:** 82.61% (ground truth hit within top 3 chunks)
- **Recall@5:** 95.65% (ground truth hit within top 5 chunks)
- **MRR:** 0.5833

### 3.3 Verification Breakdown per Class

| Status Class | Ground Truth Count | Precision | Recall | F1 Score |
|---|---|---|---|---|
| **Supported** | 10 | 60.0% | 30.0% | 40.0% |
| **Partial** | 8 | 100.0% | 50.0% | 66.67% |
| **Missing** | 7 | 100.0% | 57.14% | 72.73% |
| **Conflict** | 5 | 29.41% | 100.0% | 45.45% |

### 3.4 Verification Confusion Matrix

| Expected \ Actual | Supported | Partial | Missing | Conflict |
|---|---|---|---|---|
| **Supported** | 3 | 0 | 0 | 7 |
| **Partial** | 0 | 4 | 0 | 4 |
| **Missing** | 2 | 0 | 4 | 1 |
| **Conflict** | 0 | 0 | 0 | 5 |

## 4. Failure & Root-Cause Analysis

Total Discrepancies: **14** out of 30 requirements.

| Req ID | Title | Expected | Actual | Error Type | Explanation |
|---|---|---|---|---|---|
| `REQ-BCU-001` | High Voltage Pack Nominal Operating Range | `Supported` | `Conflict` | **verification_error** | Verification rules classified requirement as 'Conflict', but ground truth expects 'Supported'. |
| `REQ-BCU-002` | Continuous Discharge Current Monitoring | `Supported` | `Conflict` | **verification_error** | Verification rules classified requirement as 'Conflict', but ground truth expects 'Supported'. |
| `REQ-BCU-003` | Galvanic High-Voltage Isolation Barrier | `Supported` | `Conflict` | **verification_error** | Verification rules classified requirement as 'Conflict', but ground truth expects 'Supported'. |
| `REQ-BCU-004` | CAN-FD Telemetry Communication Rate | `Supported` | `Conflict` | **verification_error** | Verification rules classified requirement as 'Conflict', but ground truth expects 'Supported'. |
| `REQ-BCU-005` | Quiescent Sleep State Current Draw | `Supported` | `Conflict` | **verification_error** | Verification rules classified requirement as 'Conflict', but ground truth expects 'Supported'. |
| `REQ-BCU-006` | Cell Voltage Sensing Measurement Precision | `Supported` | `Conflict` | **verification_error** | Verification rules classified requirement as 'Conflict', but ground truth expects 'Supported'. |
| `REQ-BCU-007` | Safe State Contactor De-energization Latency | `Supported` | `Conflict` | **verification_error** | Verification rules classified requirement as 'Conflict', but ground truth expects 'Supported'. |
| `REQ-BCU-011` | Extended Climatic Operating Temperature | `Partial` | `Conflict` | **verification_error** | Verification rules classified requirement as 'Conflict', but ground truth expects 'Partial'. |
| `REQ-BCU-012` | Damp Heat Cyclic Humidity Endurance | `Partial` | `Conflict` | **verification_error** | Verification rules classified requirement as 'Conflict', but ground truth expects 'Partial'. |
| `REQ-BCU-014` | Tri-Axial Random Vibration Endurance Profile | `Partial` | `Conflict` | **verification_error** | Verification rules classified requirement as 'Conflict', but ground truth expects 'Partial'. |
| `REQ-BCU-016` | Contact Resistance Degradation After Power Cycling | `Partial` | `Conflict` | **verification_error** | Verification rules classified requirement as 'Conflict', but ground truth expects 'Partial'. |
| `REQ-BCU-018` | High Voltage Interlock Loop (HVIL) Fast Disconnect Latency | `Missing` | `Supported` | **unsupported_claim** | System incorrectly claimed requirement was verified ('Supported') when no evidence exists in the document corpus. |
| `REQ-BCU-021` | Radiated RF Electromagnetic Immunity 100 V/m | `Missing` | `Conflict` | **verification_error** | Verification rules classified requirement as 'Conflict', but ground truth expects 'Missing'. |
| `REQ-BCU-023` | State of Charge (SOC) Extended Kalman Filter Estimation Accuracy | `Missing` | `Supported` | **unsupported_claim** | System incorrectly claimed requirement was verified ('Supported') when no evidence exists in the document corpus. |

## 5. Pipeline Improvement Recommendations

1. **Retrieval Semantic Expansion:** Enhance BM25 ranking by incorporating dense sentence embeddings (`text-embedding-004`) to capture synonyms (e.g. *dielectric breakdown* ↔ *high-pot isolation*).
2. **Multi-Point Verification:** Extend deterministic regex parser to automatically construct continuous interpolation envelopes for multi-tier curves.
3. **Cross-Document Entity Linking:** Track supplier part numbers and interface pinouts explicitly across documents to improve conflict recall.
