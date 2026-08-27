# TraceAudit AI — 100-Requirement Complex Benchmark Report

> **Benchmark Date:** 2026-08-27 18:56:43  
> **Total Requirements Evaluated:** 100  
> **Total Atomic Conditions Evaluated:** 172  
> **Total Technical Documents:** 20 (DOCX, PDF, XLSX)  
> **Overall Verification Accuracy:** **80.00%**  
> **Macro F1-Score:** **79.77%**  

---

## 1. Executive Summary & Pipeline Health

The 100-requirement synthetic benchmark tests real-world automotive compliance auditing across 10 mission-critical domains (High-Voltage BMS, Traction Inverters, DC-DC Converters, Charging, Thermal, CAN-FD/Ethernet, UDS Diagnostics, Functional Safety ASIL-D, Cybersecurity ISO 21434, and Environmental EMC).

```
Pipeline Performance Summary:
  • Requirement Extraction F1 : 100.00%
  • Document Retrieval Recall@3: 94.00% | Recall@5: 98.00% (MRR: 0.5712)
  • Exact Passage Recall@3    : 100.00% | Recall@5: 100.00% (MRR: 1.0000)
  • 5-Class Requirement Macro F1: 79.77% (Accuracy: 80.00%)
  • Atomic Condition Accuracy  : 82.56% (F1: 85.93%)
  • Unsupported Claim Rate    : 5.00% (Evidence-grounded)
```

---

## 2. 5-Class Confusion Matrix

| Expected \ Predicted | SUPPORTED | PARTIAL | CONFLICT | MISSING | UNKNOWN | Total |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **SUPPORTED** | 17 | 0 | 1 | 0 | 2 | **20** |
| **PARTIAL** | 4 | 14 | 0 | 0 | 2 | **20** |
| **CONFLICT** | 0 | 0 | 20 | 0 | 0 | **20** |
| **MISSING** | 1 | 0 | 1 | 18 | 0 | **20** |
| **UNKNOWN** | 5 | 4 | 0 | 0 | 11 | **20** |

---

## 3. Detailed Verification Metrics by Class

| Class | Precision | Recall | F1-Score | Support | True Positives | False Positives | False Negatives |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **SUPPORTED** | 62.96% | 85.00% | 72.34% | 20 | 17 | 10 | 3 |
| **PARTIAL** | 77.78% | 70.00% | 73.68% | 20 | 14 | 4 | 6 |
| **CONFLICT** | 90.91% | 100.00% | 95.24% | 20 | 20 | 2 | 0 |
| **MISSING** | 100.00% | 90.00% | 94.74% | 20 | 18 | 0 | 2 |
| **UNKNOWN** | 73.33% | 55.00% | 62.86% | 20 | 11 | 4 | 9 |
| **MACRO AVG** | **81.00%** | **80.00%** | **79.77%** | **100** | - | - | - |

---

## 4. Multi-Condition & Specialty Metrics

| Metric | Score | Industry Target | Assessment |
|---|:---:|:---:|:---|
| **Conflict Detection F1** | **95.24%** | >= 90.0% | ✅ Met |
| **Missing Evidence Detection F1** | **94.74%** | >= 90.0% | ✅ Met |
| **Partial Compliance Detection F1** | **73.68%** | >= 85.0% | ⚠️ Review Needed |
| **UNKNOWN Detection F1** | **62.86%** | >= 70.0% | ⚠️ Review Needed |
| **Atomic Condition Accuracy** | **82.56%** | >= 90.0% | ⚠️ Review Needed |
| **Atomic Condition F1** | **85.93%** | >= 85.0% | ✅ Met |
| **Numerical & Range Accuracy** | **80.00%** | >= 90.0% | ⚠️ Review Needed |
| **Unsupported Claim Rate** | **5.00%** | <= 5.0% | ✅ Safe |

---

## 5. Signal-Based Root Cause Failure Classification

```
Total Failures: 20 / 100
Failure Categorization:
  • SOURCE_AUTHORITY_FAILURE           :  9 failure(s)
  • UNKNOWN_CLASSIFICATION_FAILURE     :  4 failure(s)
  • PARTIAL_COMPLIANCE_FAILURE         :  4 failure(s)
  • CONTRADICTION_FAILURE              :  2 failure(s)
  • NUMERIC_REASONING_FAILURE          :  1 failure(s)
```

### Detailed Failure Cases:

| Req ID | Expected | Predicted | Failure Category | Ground Truth Failure Context |
|---|:---:|:---:|---|---|
| `REQ-AUT-010` | **UNKNOWN** | `SUPPORTED` | `SOURCE_AUTHORITY_FAILURE` | Architecture specification describes design intention only; empirical diagnostic test proo... |
| `REQ-AUT-011` | **SUPPORTED** | `CONFLICT` | `CONTRADICTION_FAILURE` | Inverter phase overcurrent at 680 A peak triggered DESAT gate shutdown in 1.1 µs (<= 1.5 µ... |
| `REQ-AUT-014` | **PARTIAL** | `UNKNOWN` | `UNKNOWN_CLASSIFICATION_FAILURE` | Resolver angle error ±0.14° verified at room temp up to 15000 rpm. 20000 rpm and +105°C sc... |
| `REQ-AUT-018` | **MISSING** | `CONFLICT` | `CONTRADICTION_FAILURE` | Thermal bench awaiting flow meter calibration.... |
| `REQ-AUT-020` | **UNKNOWN** | `SUPPORTED` | `SOURCE_AUTHORITY_FAILURE` | Architecture description only; empirical vector control test records absent.... |
| `REQ-AUT-023` | **PARTIAL** | `SUPPORTED` | `PARTIAL_COMPLIANCE_FAILURE` | Efficiency verified at 1000 W and 2000 W; full load envelope endpoints unmeasured.... |
| `REQ-AUT-030` | **UNKNOWN** | `PARTIAL` | `SOURCE_AUTHORITY_FAILURE` | Architecture calculation document only.... |
| `REQ-AUT-034` | **PARTIAL** | `SUPPORTED` | `PARTIAL_COMPLIANCE_FAILURE` | Tested with 1.8 kW resistive load; full 3.6 kW inductive load testing pending.... |
| `REQ-AUT-044` | **PARTIAL** | `UNKNOWN` | `UNKNOWN_CLASSIFICATION_FAILURE` | Positioning verified at +25°C; -30°C extreme scheduled.... |
| `REQ-AUT-049` | **UNKNOWN** | `SUPPORTED` | `SOURCE_AUTHORITY_FAILURE` | CFD modeling calculation only; in-vehicle test mandatory.... |
| `REQ-AUT-059` | **UNKNOWN** | `PARTIAL` | `SOURCE_AUTHORITY_FAILURE` | Simulation calculation only (420 ns drift); physical test required.... |
| `REQ-AUT-063` | **PARTIAL** | `SUPPORTED` | `PARTIAL_COMPLIANCE_FAILURE` | Level 0x01 verified; Level 0x03 engineering access test pending.... |
| `REQ-AUT-069` | **UNKNOWN** | `SUPPORTED` | `SOURCE_AUTHORITY_FAILURE` | Theoretical calculation model only.... |
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
