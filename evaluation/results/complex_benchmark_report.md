# TraceAudit AI — 100-Requirement Complex Benchmark Report

> **Benchmark Date:** 2026-08-30 16:42:44  
> **Evaluation Mode:** END-TO-END
> **Downstream Requirement Source:** extracted_contracts
> **Downstream Evidence Source:** retrieved_top_5_passages
> **Total Requirements Evaluated:** 100  
> **Total Atomic Conditions Evaluated:** 172  
> **Total Technical Documents:** 20 (DOCX, PDF, XLSX)  
> **Overall Verification Accuracy:** **96.00%**  
> **Macro F1-Score:** **95.99%**  

---

## 1. Executive Summary & Pipeline Health

The 100-requirement synthetic benchmark tests real-world automotive compliance auditing across 10 mission-critical domains (High-Voltage BMS, Traction Inverters, DC-DC Converters, Charging, Thermal, CAN-FD/Ethernet, UDS Diagnostics, Functional Safety ASIL-D, Cybersecurity ISO 21434, and Environmental EMC).

```
Pipeline Performance Summary:
  • Requirement Extraction F1 : 100.00%
  • Normalized Exact Contract Recall: 69.19%
  • Extraction Completeness Gate: 97.00% (3 routed as incomplete)
  • Document Retrieval Recall@3: 95.00% | Recall@5: 98.00% (MRR: 0.5715)
  • Document-qualified Passage Recall@3: 75.00% | Recall@5: 77.00% (MRR: 0.4690)
  • 5-Class Requirement Macro F1: 95.99% (Accuracy: 96.00%)
  • Mixed-Provenance Atomic Agreement: 90.70% (F1: 92.54%)
  • Explicit Atomic Accuracy   : 88.46%
  • Raw LLM Atomic Accuracy    : 90.70%
  • Aggregation Oracle Accuracy: 100.00%
  • Audit-Defensible Accuracy  : 84.00%
  • Unsupported Claim Rate    : 0.00% (Evidence-grounded)
  • False Automatic Closures  : 0
  • Auto-Closure Precision    : 100.00%
```

Atomic ground-truth provenance: **26 explicit** condition labels and **146 inferred** labels. Inferred labels are derived from the final requirement status and `missing_conditions`; they are useful for regression tracking but are not independently annotated atomic truth.

---

## 2. 5-Class Confusion Matrix

| Expected \ Predicted | SUPPORTED | PARTIAL | CONFLICT | MISSING | UNKNOWN | Total |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **SUPPORTED** | 20 | 0 | 0 | 0 | 0 | **20** |
| **PARTIAL** | 1 | 18 | 0 | 1 | 0 | **20** |
| **CONFLICT** | 0 | 0 | 20 | 0 | 0 | **20** |
| **MISSING** | 0 | 0 | 0 | 20 | 0 | **20** |
| **UNKNOWN** | 1 | 0 | 0 | 1 | 18 | **20** |

---

## 3. Detailed Verification Metrics by Class

| Class | Precision | Recall | F1-Score | Support | True Positives | False Positives | False Negatives |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **SUPPORTED** | 90.91% | 100.00% | 95.24% | 20 | 20 | 2 | 0 |
| **PARTIAL** | 100.00% | 90.00% | 94.74% | 20 | 18 | 0 | 2 |
| **CONFLICT** | 100.00% | 100.00% | 100.00% | 20 | 20 | 0 | 0 |
| **MISSING** | 90.91% | 100.00% | 95.24% | 20 | 20 | 2 | 0 |
| **UNKNOWN** | 100.00% | 90.00% | 94.74% | 20 | 18 | 0 | 2 |
| **MACRO AVG** | **96.36%** | **96.00%** | **95.99%** | **100** | - | - | - |

---

## 4. Multi-Condition & Specialty Metrics

| Metric | Score | Industry Target | Assessment |
|---|:---:|:---:|:---|
| **Conflict Detection F1** | **100.00%** | >= 90.0% | ✅ Met |
| **Missing Evidence Detection F1** | **95.24%** | >= 90.0% | ✅ Met |
| **Partial Compliance Detection F1** | **94.74%** | >= 85.0% | ✅ Met |
| **UNKNOWN Detection F1** | **94.74%** | >= 70.0% | ✅ Met |
| **Mixed-Provenance Atomic Agreement** | **90.70%** | Regression only | Inferred labels are not authoritative |
| **Explicit Atomic Accuracy** | **88.46%** | >= 90.0% | ⚠️ Review Needed |
| **Atomic Condition F1** | **92.54%** | >= 85.0% | ✅ Met |
| **Numeric-Condition Verdict Accuracy** | **90.30%** | >= 90.0% | ✅ Met |
| **Audit-Defensible Accuracy** | **84.00%** | >= 90.0% | ⚠️ Review Needed |
| **Unsupported Claim Rate** | **0.00%** | <= 5.0% | ✅ Safe |

### Review-Gate Business Safety

The review gate does not change semantic verdict accuracy. It controls whether a predicted `SUPPORTED` requirement is safe to close automatically.

| Metric | Result | Safety Target | Assessment |
|---|:---:|:---:|:---:|
| **False SUPPORTED Predictions** | **2** | Classification diagnostic | Reported separately |
| **False SUPPORTED Routed to Review** | **2** | All false SUPPORTED | ✅ |
| **Review-Gate Capture Rate** | **100.00%** | 100% | ✅ Safe |
| **False Automatic Closures** | **0** | 0 | ✅ Safe |
| **Automatic-Closure Precision** | **100.00%** | 100% | ✅ Safe |
| **Supported Review Rate** | **18.18%** | Monitor | Workflow load indicator |

---

## 5. Signal-Based Root Cause Failure Classification

```
Total Failures: 4 / 100
Failure Categorization:
  • POST_LLM_DETERMINISTIC_REGRESSION  :  2 failure(s)
  • SOURCE_AUTHORITY_FAILURE           :  1 failure(s)
  • NUMERIC_REASONING_FAILURE          :  1 failure(s)
```

### Detailed Failure Cases:

| Req ID | Expected | Predicted | Failure Category | Ground Truth Failure Context |
|---|:---:|:---:|---|---|
| `REQ-AUT-033` | **PARTIAL** | `MISSING` | `POST_LLM_DETERMINISTIC_REGRESSION` | Tested at 230 V / 50 Hz and 120 V / 60 Hz; low-line and frequency extremes pending.... |
| `REQ-AUT-049` | **UNKNOWN** | `SUPPORTED` | `SOURCE_AUTHORITY_FAILURE` | CFD modeling calculation only; in-vehicle test mandatory.... |
| `REQ-AUT-050` | **UNKNOWN** | `MISSING` | `NUMERIC_REASONING_FAILURE` | Architecture documentation only.... |
| `REQ-AUT-064` | **PARTIAL** | `SUPPORTED` | `POST_LLM_DETERMINISTIC_REGRESSION` | Rollback triggered in 1.4 s; golden image boot verification scheduled.... |

---

## 6. Bottleneck Analysis & Next Technical Improvements

### 🔍 Main Bottleneck:
- **Pipeline is well-balanced** with high fidelity across both semantic retrieval and hybrid verification stages.

### 🚀 Recommended Next Improvements:
1. **Annotate Explicit Atomic Truth:** Add an expected state for every benchmark condition so atomic accuracy no longer depends on labels inferred from the final requirement verdict.
2. **Use Oracle Ablations:** Compare end-to-end, contract-oracle, evidence-oracle, and combined-oracle runs before changing a production stage.
3. **Review Stage Transitions:** Prioritize cases marked `DAMAGED`, then address `UNCHANGED_WRONG`; do not tune aggregation based only on final-status mismatches.
