# TraceAudit AI — 100-Requirement Complex Benchmark Report

> **Benchmark Date:** 2026-08-23 19:02:13  
> **Total Requirements Evaluated:** 100  
> **Total Atomic Conditions Evaluated:** 172  
> **Total Technical Documents:** 20 (DOCX, PDF, XLSX)  
> **Overall Verification Accuracy:** **84.00%**  
> **Macro F1-Score:** **83.51%**  

---

## 1. Executive Summary & Pipeline Health

The 100-requirement synthetic benchmark tests real-world automotive compliance auditing across 10 mission-critical domains (High-Voltage BMS, Traction Inverters, DC-DC Converters, Charging, Thermal, CAN-FD/Ethernet, UDS Diagnostics, Functional Safety ASIL-D, Cybersecurity ISO 21434, and Environmental EMC).

```
Pipeline Performance Summary:
  • Requirement Extraction F1 : 100.00%
  • Document Retrieval Recall@3: 94.00% | Recall@5: 98.00% (MRR: 0.5712)
  • Exact Passage Recall@3    : 100.00% | Recall@5: 100.00% (MRR: 1.0000)
  • 5-Class Requirement Macro F1: 83.51% (Accuracy: 84.00%)
  • Atomic Condition Accuracy  : 86.63% (F1: 91.97%)
  • Unsupported Claim Rate    : 5.00% (Evidence-grounded)
```

---

## 2. 5-Class Confusion Matrix

| Expected \ Predicted | SUPPORTED | PARTIAL | CONFLICT | MISSING | UNKNOWN | Total |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **SUPPORTED** | 19 | 0 | 1 | 0 | 0 | **20** |
| **PARTIAL** | 3 | 16 | 1 | 0 | 0 | **20** |
| **CONFLICT** | 0 | 0 | 20 | 0 | 0 | **20** |
| **MISSING** | 1 | 0 | 1 | 18 | 0 | **20** |
| **UNKNOWN** | 3 | 6 | 0 | 0 | 11 | **20** |

---

## 3. Detailed Verification Metrics by Class

| Class | Precision | Recall | F1-Score | Support | True Positives | False Positives | False Negatives |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **SUPPORTED** | 73.08% | 95.00% | 82.61% | 20 | 19 | 7 | 1 |
| **PARTIAL** | 72.73% | 80.00% | 76.19% | 20 | 16 | 6 | 4 |
| **CONFLICT** | 86.96% | 100.00% | 93.02% | 20 | 20 | 3 | 0 |
| **MISSING** | 100.00% | 90.00% | 94.74% | 20 | 18 | 0 | 2 |
| **UNKNOWN** | 100.00% | 55.00% | 70.97% | 20 | 11 | 0 | 9 |
| **MACRO AVG** | **86.55%** | **84.00%** | **83.51%** | **100** | - | - | - |

---

## 4. Multi-Condition & Specialty Metrics

| Metric | Score | Industry Target | Assessment |
|---|:---:|:---:|:---|
| **Conflict Detection F1** | **93.02%** | >= 90.0% | ✅ Met |
| **Missing Evidence Detection F1** | **94.74%** | >= 90.0% | ✅ Met |
| **Partial Compliance Detection F1** | **76.19%** | >= 85.0% | ⚠️ Review Needed |
| **UNKNOWN Detection F1** | **70.97%** | >= 70.0% | ✅ Met |
| **Atomic Condition Accuracy** | **86.63%** | >= 90.0% | ⚠️ Review Needed |
| **Atomic Condition F1** | **91.97%** | >= 85.0% | ✅ Met |
| **Numerical & Range Accuracy** | **84.00%** | >= 90.0% | ⚠️ Review Needed |
| **Unsupported Claim Rate** | **5.00%** | <= 5.0% | ✅ Safe |

---

## 5. Signal-Based Root Cause Failure Classification

```
Total Failures: 16 / 100
Failure Categorization:
  • SOURCE_AUTHORITY_FAILURE           :  7 failure(s)
  • CONTRADICTION_FAILURE              :  3 failure(s)
  • PARTIAL_COMPLIANCE_FAILURE         :  3 failure(s)
  • RETRIEVAL_FAILURE                  :  2 failure(s)
  • NUMERIC_REASONING_FAILURE          :  1 failure(s)
```

### Detailed Failure Cases:

| Req ID | Expected | Predicted | Failure Category | Ground Truth Failure Context |
|---|:---:|:---:|---|---|
| `REQ-AUT-009` | **UNKNOWN** | `PARTIAL` | `SOURCE_AUTHORITY_FAILURE` | Acoustic baseline characterized in bench test, but vehicle pack calibration remains unprov... |
| `REQ-AUT-010` | **UNKNOWN** | `PARTIAL` | `SOURCE_AUTHORITY_FAILURE` | Architecture specification describes design intention only; empirical diagnostic test proo... |
| `REQ-AUT-011` | **SUPPORTED** | `CONFLICT` | `CONTRADICTION_FAILURE` | Inverter phase overcurrent at 680 A peak triggered DESAT gate shutdown in 1.1 µs (<= 1.5 µ... |
| `REQ-AUT-018` | **MISSING** | `CONFLICT` | `CONTRADICTION_FAILURE` | Thermal bench awaiting flow meter calibration.... |
| `REQ-AUT-019` | **UNKNOWN** | `PARTIAL` | `RETRIEVAL_FAILURE` | Simulation calculation only (THD 2.4%); physical dynamometer test required.... |
| `REQ-AUT-020` | **UNKNOWN** | `PARTIAL` | `SOURCE_AUTHORITY_FAILURE` | Architecture description only; empirical vector control test records absent.... |
| `REQ-AUT-024` | **PARTIAL** | `SUPPORTED` | `PARTIAL_COMPLIANCE_FAILURE` | Ripple 68 mVpp measured at room temp; thermal extreme chamber measurements pending.... |
| `REQ-AUT-029` | **UNKNOWN** | `PARTIAL` | `RETRIEVAL_FAILURE` | SPICE circuit simulation only; physical transient generator test required.... |
| `REQ-AUT-033` | **PARTIAL** | `CONFLICT` | `CONTRADICTION_FAILURE` | Tested at 230 V / 50 Hz and 120 V / 60 Hz; low-line and frequency extremes pending.... |
| `REQ-AUT-040` | **UNKNOWN** | `SUPPORTED` | `SOURCE_AUTHORITY_FAILURE` | Architecture definition only.... |
| `REQ-AUT-044` | **PARTIAL** | `SUPPORTED` | `PARTIAL_COMPLIANCE_FAILURE` | Positioning verified at +25°C; -30°C extreme scheduled.... |
| `REQ-AUT-060` | **UNKNOWN** | `SUPPORTED` | `SOURCE_AUTHORITY_FAILURE` | Architectural configuration documentation only.... |
| `REQ-AUT-078` | **MISSING** | `SUPPORTED` | `NUMERIC_REASONING_FAILURE` | BIST startup profiling pending.... |
| `REQ-AUT-079` | **UNKNOWN** | `SUPPORTED` | `SOURCE_AUTHORITY_FAILURE` | FMEDA theoretical calculation report only; physical fault injection required.... |
| `REQ-AUT-094` | **PARTIAL** | `SUPPORTED` | `PARTIAL_COMPLIANCE_FAILURE` | 250 of 500 cycles completed with zero defects; remaining 250 in progress.... |

---

## 6. Bottleneck Analysis & Next Technical Improvements

### 🔍 Main Bottleneck:
- **Multi-Condition Reasoner & Scope Discrimination** is the primary bottleneck. Retrieval succeeded in finding candidate chunks, but multi-condition boundaries or component scope limits were misclassified.

### 🚀 Recommended Next Improvements:
1. **Adaptive Chunk Reranking:** Integrate cross-encoder reranker for dense technical terms (e.g. distinguishing battery coolant temperature vs ASIC junction temperature).
2. **Atomic Condition Decomposition:** Pass extracted condition trees directly into the LLM verification reasoner prompt to evaluate each sub-condition as a formal boolean clause.
3. **Document Authority Layer:** Explicitly tag supplier datasheets vs system validation reports in evidence prompts to enforce scope hierarchy rules.
