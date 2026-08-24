# TraceAudit AI — 100-Requirement Complex Benchmark Report

> **Benchmark Date:** 2026-08-24 23:40:26  
> **Total Requirements Evaluated:** 100  
> **Total Atomic Conditions Evaluated:** 172  
> **Total Technical Documents:** 20 (DOCX, PDF, XLSX)  
> **Overall Verification Accuracy:** **77.00%**  
> **Macro F1-Score:** **77.28%**  

---

## 1. Executive Summary & Pipeline Health

The 100-requirement synthetic benchmark tests real-world automotive compliance auditing across 10 mission-critical domains (High-Voltage BMS, Traction Inverters, DC-DC Converters, Charging, Thermal, CAN-FD/Ethernet, UDS Diagnostics, Functional Safety ASIL-D, Cybersecurity ISO 21434, and Environmental EMC).

```
Pipeline Performance Summary:
  • Requirement Extraction F1 : 100.00%
  • Document Retrieval Recall@3: 94.00% | Recall@5: 98.00% (MRR: 0.5712)
  • Exact Passage Recall@3    : 100.00% | Recall@5: 100.00% (MRR: 1.0000)
  • 5-Class Requirement Macro F1: 77.28% (Accuracy: 77.00%)
  • Atomic Condition Accuracy  : 82.56% (F1: 84.67%)
  • Unsupported Claim Rate    : 5.00% (Evidence-grounded)
```

---

## 2. 5-Class Confusion Matrix

| Expected \ Predicted | SUPPORTED | PARTIAL | CONFLICT | MISSING | UNKNOWN | Total |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **SUPPORTED** | 15 | 1 | 1 | 0 | 3 | **20** |
| **PARTIAL** | 4 | 16 | 0 | 0 | 0 | **20** |
| **CONFLICT** | 0 | 0 | 20 | 0 | 0 | **20** |
| **MISSING** | 1 | 0 | 1 | 14 | 4 | **20** |
| **UNKNOWN** | 6 | 2 | 0 | 0 | 12 | **20** |

---

## 3. Detailed Verification Metrics by Class

| Class | Precision | Recall | F1-Score | Support | True Positives | False Positives | False Negatives |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **SUPPORTED** | 57.69% | 75.00% | 65.22% | 20 | 15 | 11 | 5 |
| **PARTIAL** | 84.21% | 80.00% | 82.05% | 20 | 16 | 3 | 4 |
| **CONFLICT** | 90.91% | 100.00% | 95.24% | 20 | 20 | 2 | 0 |
| **MISSING** | 100.00% | 70.00% | 82.35% | 20 | 14 | 0 | 6 |
| **UNKNOWN** | 63.16% | 60.00% | 61.54% | 20 | 12 | 7 | 8 |
| **MACRO AVG** | **79.19%** | **77.00%** | **77.28%** | **100** | - | - | - |

---

## 4. Multi-Condition & Specialty Metrics

| Metric | Score | Industry Target | Assessment |
|---|:---:|:---:|:---|
| **Conflict Detection F1** | **95.24%** | >= 90.0% | ✅ Met |
| **Missing Evidence Detection F1** | **82.35%** | >= 90.0% | ⚠️ Review Needed |
| **Partial Compliance Detection F1** | **82.05%** | >= 85.0% | ⚠️ Review Needed |
| **UNKNOWN Detection F1** | **61.54%** | >= 70.0% | ⚠️ Review Needed |
| **Atomic Condition Accuracy** | **82.56%** | >= 90.0% | ⚠️ Review Needed |
| **Atomic Condition F1** | **84.67%** | >= 85.0% | ⚠️ Review Needed |
| **Numerical & Range Accuracy** | **77.00%** | >= 90.0% | ⚠️ Review Needed |
| **Unsupported Claim Rate** | **5.00%** | <= 5.0% | ✅ Safe |

---

## 5. Signal-Based Root Cause Failure Classification

```
Total Failures: 23 / 100
Failure Categorization:
  • SOURCE_AUTHORITY_FAILURE           :  7 failure(s)
  • NUMERIC_REASONING_FAILURE          :  6 failure(s)
  • PARTIAL_COMPLIANCE_FAILURE         :  4 failure(s)
  • UNKNOWN_CLASSIFICATION_FAILURE     :  3 failure(s)
  • CONTRADICTION_FAILURE              :  2 failure(s)
  • RETRIEVAL_FAILURE                  :  1 failure(s)
```

### Detailed Failure Cases:

| Req ID | Expected | Predicted | Failure Category | Ground Truth Failure Context |
|---|:---:|:---:|---|---|
| `REQ-AUT-002` | **SUPPORTED** | `PARTIAL` | `NUMERIC_REASONING_FAILURE` | Pack operating envelope validated from 380 V to 820 V DC (fully covers required 400 V to 8... |
| `REQ-AUT-010` | **UNKNOWN** | `SUPPORTED` | `SOURCE_AUTHORITY_FAILURE` | Architecture specification describes design intention only; empirical diagnostic test proo... |
| `REQ-AUT-011` | **SUPPORTED** | `CONFLICT` | `CONTRADICTION_FAILURE` | Inverter phase overcurrent at 680 A peak triggered DESAT gate shutdown in 1.1 µs (<= 1.5 µ... |
| `REQ-AUT-018` | **MISSING** | `CONFLICT` | `CONTRADICTION_FAILURE` | Thermal bench awaiting flow meter calibration.... |
| `REQ-AUT-019` | **UNKNOWN** | `PARTIAL` | `RETRIEVAL_FAILURE` | Simulation calculation only (THD 2.4%); physical dynamometer test required.... |
| `REQ-AUT-020` | **UNKNOWN** | `SUPPORTED` | `SOURCE_AUTHORITY_FAILURE` | Architecture description only; empirical vector control test records absent.... |
| `REQ-AUT-038` | **MISSING** | `UNKNOWN` | `NUMERIC_REASONING_FAILURE` | Cable resistance fixture pending.... |
| `REQ-AUT-049` | **UNKNOWN** | `SUPPORTED` | `SOURCE_AUTHORITY_FAILURE` | CFD modeling calculation only; in-vehicle test mandatory.... |
| `REQ-AUT-063` | **PARTIAL** | `SUPPORTED` | `PARTIAL_COMPLIANCE_FAILURE` | Level 0x01 verified; Level 0x03 engineering access test pending.... |
| `REQ-AUT-064` | **PARTIAL** | `SUPPORTED` | `PARTIAL_COMPLIANCE_FAILURE` | Rollback triggered in 1.4 s; golden image boot verification scheduled.... |
| `REQ-AUT-069` | **UNKNOWN** | `PARTIAL` | `SOURCE_AUTHORITY_FAILURE` | Theoretical calculation model only.... |
| `REQ-AUT-070` | **UNKNOWN** | `SUPPORTED` | `SOURCE_AUTHORITY_FAILURE` | Architecture specification only.... |
| `REQ-AUT-071` | **SUPPORTED** | `UNKNOWN` | `UNKNOWN_CLASSIFICATION_FAILURE` | Watchdog SPI serviced at 15.2 ms (within 13 to 17 ms window). Reset triggered on violation... |
| `REQ-AUT-072` | **SUPPORTED** | `UNKNOWN` | `UNKNOWN_CLASSIFICATION_FAILURE` | Core brownout at 2.94 V triggered PMIC hard reset in 2.1 µs (< 5.0 µs required).... |
| `REQ-AUT-078` | **MISSING** | `SUPPORTED` | `NUMERIC_REASONING_FAILURE` | BIST startup profiling pending.... |

---

## 6. Bottleneck Analysis & Next Technical Improvements

### 🔍 Main Bottleneck:
- **Multi-Condition Reasoner & Scope Discrimination** is the primary bottleneck. Retrieval succeeded in finding candidate chunks, but multi-condition boundaries or component scope limits were misclassified.

### 🚀 Recommended Next Improvements:
1. **Adaptive Chunk Reranking:** Integrate cross-encoder reranker for dense technical terms (e.g. distinguishing battery coolant temperature vs ASIC junction temperature).
2. **Atomic Condition Decomposition:** Pass extracted condition trees directly into the LLM verification reasoner prompt to evaluate each sub-condition as a formal boolean clause.
3. **Document Authority Layer:** Explicitly tag supplier datasheets vs system validation reports in evidence prompts to enforce scope hierarchy rules.
