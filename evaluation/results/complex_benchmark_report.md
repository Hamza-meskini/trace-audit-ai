# TraceAudit AI — 100-Requirement Complex Benchmark Report

> **Benchmark Date:** 2026-08-28 16:47:48  
> **Evaluation Mode:** END-TO-END
> **Downstream Requirement Source:** extracted_contracts
> **Total Requirements Evaluated:** 100  
> **Total Atomic Conditions Evaluated:** 172  
> **Total Technical Documents:** 20 (DOCX, PDF, XLSX)  
> **Overall Verification Accuracy:** **96.00%**  
> **Macro F1-Score:** **95.97%**  

---

## 1. Executive Summary & Pipeline Health

The 100-requirement synthetic benchmark tests real-world automotive compliance auditing across 10 mission-critical domains (High-Voltage BMS, Traction Inverters, DC-DC Converters, Charging, Thermal, CAN-FD/Ethernet, UDS Diagnostics, Functional Safety ASIL-D, Cybersecurity ISO 21434, and Environmental EMC).

```
Pipeline Performance Summary:
  • Requirement Extraction F1 : 100.00%
  • Exact Extracted Contract Recall: 68.02%
  • Document Retrieval Recall@3: 97.00% | Recall@5: 99.00% (MRR: 0.5823)
  • Document-qualified Passage Recall@3: 76.00% | Recall@5: 78.00% (MRR: 0.4790)
  • 5-Class Requirement Macro F1: 95.97% (Accuracy: 96.00%)
  • Atomic Condition Accuracy  : 81.40% (F1: 87.30%)
  • Unsupported Claim Rate    : 0.00% (Evidence-grounded)
```

---

## 2. 5-Class Confusion Matrix

| Expected \ Predicted | SUPPORTED | PARTIAL | CONFLICT | MISSING | UNKNOWN | Total |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **SUPPORTED** | 19 | 0 | 0 | 0 | 1 | **20** |
| **PARTIAL** | 0 | 19 | 1 | 0 | 0 | **20** |
| **CONFLICT** | 0 | 0 | 20 | 0 | 0 | **20** |
| **MISSING** | 0 | 0 | 0 | 20 | 0 | **20** |
| **UNKNOWN** | 1 | 1 | 0 | 0 | 18 | **20** |

---

## 3. Detailed Verification Metrics by Class

| Class | Precision | Recall | F1-Score | Support | True Positives | False Positives | False Negatives |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **SUPPORTED** | 95.00% | 95.00% | 95.00% | 20 | 19 | 1 | 1 |
| **PARTIAL** | 95.00% | 95.00% | 95.00% | 20 | 19 | 1 | 1 |
| **CONFLICT** | 95.24% | 100.00% | 97.56% | 20 | 20 | 1 | 0 |
| **MISSING** | 100.00% | 100.00% | 100.00% | 20 | 20 | 0 | 0 |
| **UNKNOWN** | 94.74% | 90.00% | 92.31% | 20 | 18 | 1 | 2 |
| **MACRO AVG** | **95.99%** | **96.00%** | **95.97%** | **100** | - | - | - |

---

## 4. Multi-Condition & Specialty Metrics

| Metric | Score | Industry Target | Assessment |
|---|:---:|:---:|:---|
| **Conflict Detection F1** | **97.56%** | >= 90.0% | ✅ Met |
| **Missing Evidence Detection F1** | **100.00%** | >= 90.0% | ✅ Met |
| **Partial Compliance Detection F1** | **95.00%** | >= 85.0% | ✅ Met |
| **UNKNOWN Detection F1** | **92.31%** | >= 70.0% | ✅ Met |
| **Atomic Condition Accuracy** | **81.40%** | >= 90.0% | ⚠️ Review Needed |
| **Atomic Condition F1** | **87.30%** | >= 85.0% | ✅ Met |
| **Numerical & Range Accuracy** | **81.21%** | >= 90.0% | ⚠️ Review Needed |
| **Unsupported Claim Rate** | **0.00%** | <= 5.0% | ✅ Safe |

---

## 5. Signal-Based Root Cause Failure Classification

```
Total Failures: 4 / 100
Failure Categorization:
  • SOURCE_AUTHORITY_FAILURE           :  2 failure(s)
  • UNKNOWN_CLASSIFICATION_FAILURE     :  1 failure(s)
  • CONTRADICTION_FAILURE              :  1 failure(s)
```

### Detailed Failure Cases:

| Req ID | Expected | Predicted | Failure Category | Ground Truth Failure Context |
|---|:---:|:---:|---|---|
| `REQ-AUT-011` | **SUPPORTED** | `UNKNOWN` | `UNKNOWN_CLASSIFICATION_FAILURE` | Inverter phase overcurrent at 680 A peak triggered DESAT gate shutdown in 1.1 µs (<= 1.5 µ... |
| `REQ-AUT-014` | **PARTIAL** | `CONFLICT` | `CONTRADICTION_FAILURE` | Resolver angle error ±0.14° verified at room temp up to 15000 rpm. 20000 rpm and +105°C sc... |
| `REQ-AUT-030` | **UNKNOWN** | `SUPPORTED` | `SOURCE_AUTHORITY_FAILURE` | Architecture calculation document only.... |
| `REQ-AUT-070` | **UNKNOWN** | `PARTIAL` | `SOURCE_AUTHORITY_FAILURE` | Architecture specification only.... |

---

## 6. Bottleneck Analysis & Next Technical Improvements

### 🔍 Main Bottleneck:
- **Pipeline is well-balanced** with high fidelity across both semantic retrieval and hybrid verification stages.

### 🚀 Recommended Next Improvements:
1. **Adaptive Chunk Reranking:** Integrate cross-encoder reranker for dense technical terms (e.g. distinguishing battery coolant temperature vs ASIC junction temperature).
2. **Atomic Condition Decomposition:** Pass extracted condition trees directly into the LLM verification reasoner prompt to evaluate each sub-condition as a formal boolean clause.
3. **Document Authority Layer:** Explicitly tag supplier datasheets vs system validation reports in evidence prompts to enforce scope hierarchy rules.
