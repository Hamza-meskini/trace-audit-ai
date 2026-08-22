# TRACEAUDIT BENCHMARK REPORT

**Execution Timestamp:** 2026-08-22T22:28:22.753011+00:00  
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
| **Requirement Extraction** | F1 Score | **100.0%** | 🟢 PASS |
| **Evidence Retrieval** | Recall@3 | **91.3%** | 🟢 PASS |
| **Evidence Retrieval** | Mean Reciprocal Rank (MRR) | **0.6268** | 🟡 REVIEW |
| **Verification Classification** | Accuracy | **86.67%** | 🟢 PASS |
| **Verification Classification** | Macro F1 | **87.41%** | 🟢 PASS |
| **Contradiction Detection** | Conflict F1 | **90.91%** | 🟢 PASS |
| **Missing Evidence Detection** | Missing F1 | **100.0%** | 🟢 PASS |
| **Unsupported Claim Rate (Hallucination)** | Rate on Missing Evidence | **0.0%** | 🟢 ZERO |

## 3. Stage-by-Stage Performance

### 3.1 Requirement Extraction
- **Precision:** 100.0%
- **Recall:** 100.0%
- **F1 Score:** 100.0%
- Extracted: 30 clauses vs Ground Truth: 30

### 3.2 Evidence Retrieval (Top-K)
- **Recall@1:** 34.78% (first retrieved chunk contains ground truth)
- **Recall@3:** 91.3% (ground truth hit within top 3 chunks)
- **Recall@5:** 95.65% (ground truth hit within top 5 chunks)
- **MRR:** 0.6268

### 3.3 Verification Breakdown per Class

| Status Class | Ground Truth Count | Precision | Recall | F1 Score |
|---|---|---|---|---|
| **Supported** | 10 | 75.0% | 90.0% | 81.82% |
| **Partial** | 8 | 100.0% | 62.5% | 76.92% |
| **Missing** | 7 | 100.0% | 100.0% | 100.0% |
| **Conflict** | 5 | 83.33% | 100.0% | 90.91% |

### 3.4 Verification Confusion Matrix

| Expected \ Actual | Supported | Partial | Missing | Conflict |
|---|---|---|---|---|
| **Supported** | 9 | 0 | 0 | 1 |
| **Partial** | 3 | 5 | 0 | 0 |
| **Missing** | 0 | 0 | 7 | 0 |
| **Conflict** | 0 | 0 | 0 | 5 |

## 4. Failure & Root-Cause Analysis

Total Discrepancies: **4** out of 30 requirements.

| Req ID | Title | Expected | Actual | Error Type | Explanation |
|---|---|---|---|---|---|
| `REQ-BCU-001` | High Voltage Pack Nominal Operating Range | `Supported` | `Conflict` | **verification_error** | Verification rules classified requirement as 'Conflict', but ground truth expects 'Supported'. |
| `REQ-BCU-011` | Extended Climatic Operating Temperature | `Partial` | `Supported` | **verification_error** | Verification rules classified requirement as 'Supported', but ground truth expects 'Partial'. |
| `REQ-BCU-013` | Thermal Runaway Gas Venting Pressure Calculation and Burst Validation | `Partial` | `Supported` | **verification_error** | Verification rules classified requirement as 'Supported', but ground truth expects 'Partial'. |
| `REQ-BCU-015` | Overcurrent Protection Multi-Tier Inverse Time Curve | `Partial` | `Supported` | **verification_error** | Verification rules classified requirement as 'Supported', but ground truth expects 'Partial'. |

## 5. Pipeline Improvement Recommendations

1. **Retrieval Semantic Expansion:** Enhance BM25 ranking by incorporating dense sentence embeddings (`text-embedding-004`) to capture synonyms (e.g. *dielectric breakdown* ↔ *high-pot isolation*).
2. **Multi-Point Verification:** Extend deterministic regex parser to automatically construct continuous interpolation envelopes for multi-tier curves.
3. **Cross-Document Entity Linking:** Track supplier part numbers and interface pinouts explicitly across documents to improve conflict recall.
