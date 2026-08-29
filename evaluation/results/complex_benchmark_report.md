# TraceAudit AI — 100-Requirement Complex Benchmark Report

> **Benchmark Date:** 2026-08-29 00:11:44  
> **Evaluation Mode:** ORACLE-EVIDENCE
> **Downstream Requirement Source:** extracted_contracts
> **Downstream Evidence Source:** ground_truth_oracle_passages
> **Total Requirements Evaluated:** 100  
> **Total Atomic Conditions Evaluated:** 172  
> **Total Technical Documents:** 20 (DOCX, PDF, XLSX)  
> **Overall Verification Accuracy:** **76.00%**  
> **Macro F1-Score:** **76.43%**  

---

## 1. Executive Summary & Pipeline Health

The 100-requirement synthetic benchmark tests real-world automotive compliance auditing across 10 mission-critical domains (High-Voltage BMS, Traction Inverters, DC-DC Converters, Charging, Thermal, CAN-FD/Ethernet, UDS Diagnostics, Functional Safety ASIL-D, Cybersecurity ISO 21434, and Environmental EMC).

```
Pipeline Performance Summary:
  • Requirement Extraction F1 : 100.00%
  • Exact Extracted Contract Recall: 72.09%
  • Document Retrieval Recall@3: 97.00% | Recall@5: 99.00% (MRR: 0.5823)
  • Document-qualified Passage Recall@3: 76.00% | Recall@5: 78.00% (MRR: 0.4790)
  • 5-Class Requirement Macro F1: 76.43% (Accuracy: 76.00%)
  • Atomic Condition Accuracy  : 67.44% (F1: 69.72%)
  • Raw LLM Atomic Accuracy    : 68.46%
  • Aggregation Oracle Accuracy: 100.00%
  • Audit-Defensible Accuracy  : 67.00%
  • Unsupported Claim Rate    : 0.00% (Evidence-grounded)
```

Atomic ground-truth provenance: **26 explicit** condition labels and **146 inferred** labels. Inferred labels are derived from the final requirement status and `missing_conditions`; they are useful for regression tracking but are not independently annotated atomic truth.

---

## 2. 5-Class Confusion Matrix

| Expected \ Predicted | SUPPORTED | PARTIAL | CONFLICT | MISSING | UNKNOWN | Total |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **SUPPORTED** | 14 | 2 | 0 | 0 | 4 | **20** |
| **PARTIAL** | 1 | 9 | 1 | 0 | 9 | **20** |
| **CONFLICT** | 0 | 0 | 14 | 0 | 6 | **20** |
| **MISSING** | 0 | 0 | 0 | 19 | 1 | **20** |
| **UNKNOWN** | 0 | 0 | 0 | 0 | 20 | **20** |

---

## 3. Detailed Verification Metrics by Class

| Class | Precision | Recall | F1-Score | Support | True Positives | False Positives | False Negatives |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **SUPPORTED** | 93.33% | 70.00% | 80.00% | 20 | 14 | 1 | 6 |
| **PARTIAL** | 81.82% | 45.00% | 58.06% | 20 | 9 | 2 | 11 |
| **CONFLICT** | 93.33% | 70.00% | 80.00% | 20 | 14 | 1 | 6 |
| **MISSING** | 100.00% | 95.00% | 97.44% | 20 | 19 | 0 | 1 |
| **UNKNOWN** | 50.00% | 100.00% | 66.67% | 20 | 20 | 20 | 0 |
| **MACRO AVG** | **83.70%** | **76.00%** | **76.43%** | **100** | - | - | - |

---

## 4. Multi-Condition & Specialty Metrics

| Metric | Score | Industry Target | Assessment |
|---|:---:|:---:|:---|
| **Conflict Detection F1** | **80.00%** | >= 90.0% | ⚠️ Review Needed |
| **Missing Evidence Detection F1** | **97.44%** | >= 90.0% | ✅ Met |
| **Partial Compliance Detection F1** | **58.06%** | >= 85.0% | ⚠️ Review Needed |
| **UNKNOWN Detection F1** | **66.67%** | >= 70.0% | ⚠️ Review Needed |
| **Atomic Condition Accuracy** | **67.44%** | >= 90.0% | ⚠️ Review Needed |
| **Atomic Condition F1** | **69.72%** | >= 85.0% | ⚠️ Review Needed |
| **Numeric-Condition Verdict Accuracy** | **67.88%** | >= 90.0% | ⚠️ Review Needed |
| **Audit-Defensible Accuracy** | **67.00%** | >= 90.0% | ⚠️ Review Needed |
| **Unsupported Claim Rate** | **0.00%** | <= 5.0% | ✅ Safe |

---

## 5. Signal-Based Root Cause Failure Classification

```
Total Failures: 24 / 100
Failure Categorization:
  • UNKNOWN_CLASSIFICATION_FAILURE     : 12 failure(s)
  • CONTRADICTION_FAILURE              :  7 failure(s)
  • NUMERIC_REASONING_FAILURE          :  3 failure(s)
  • POST_LLM_DETERMINISTIC_REGRESSION  :  1 failure(s)
  • PARTIAL_COMPLIANCE_FAILURE         :  1 failure(s)
```

### Detailed Failure Cases:

| Req ID | Expected | Predicted | Failure Category | Ground Truth Failure Context |
|---|:---:|:---:|---|---|
| `REQ-AUT-002` | **SUPPORTED** | `PARTIAL` | `NUMERIC_REASONING_FAILURE` | Pack operating envelope validated from 380 V to 820 V DC (fully covers required 400 V to 8... |
| `REQ-AUT-006` | **CONFLICT** | `UNKNOWN` | `CONTRADICTION_FAILURE` | Measured balancing current is clamped to 120 mA to avoid PCB overheating, failing the >= 3... |
| `REQ-AUT-008` | **MISSING** | `UNKNOWN` | `NUMERIC_REASONING_FAILURE` | Only descriptive layout mention in report; test is scheduled for Phase 3 validation.... |
| `REQ-AUT-011` | **SUPPORTED** | `UNKNOWN` | `UNKNOWN_CLASSIFICATION_FAILURE` | Inverter phase overcurrent at 680 A peak triggered DESAT gate shutdown in 1.1 µs (<= 1.5 µ... |
| `REQ-AUT-013` | **PARTIAL** | `UNKNOWN` | `UNKNOWN_CLASSIFICATION_FAILURE` | Efficiency verified only at 4000 rpm from 100 Nm to 250 Nm. Extreme speed and torque point... |
| `REQ-AUT-015` | **CONFLICT** | `UNKNOWN` | `CONTRADICTION_FAILURE` | Datasheet limits maximum junction temperature to 150°C, contradicting requirement of conti... |
| `REQ-AUT-016` | **CONFLICT** | `UNKNOWN` | `CONTRADICTION_FAILURE` | Measured current sensor -3dB bandwidth is 160 kHz, failing the required >= 250 kHz.... |
| `REQ-AUT-024` | **PARTIAL** | `UNKNOWN` | `UNKNOWN_CLASSIFICATION_FAILURE` | Ripple 68 mVpp measured at room temp; thermal extreme chamber measurements pending.... |
| `REQ-AUT-025` | **CONFLICT** | `UNKNOWN` | `CONTRADICTION_FAILURE` | Datasheet continuous current limit is 180 A at 65°C, contradicting requirement of >= 250 A... |
| `REQ-AUT-043` | **PARTIAL** | `UNKNOWN` | `UNKNOWN_CLASSIFICATION_FAILURE` | Tested at 0°C and -5°C; -15°C ambient extreme test pending.... |
| `REQ-AUT-046` | **CONFLICT** | `UNKNOWN` | `CONTRADICTION_FAILURE` | Measured rise time is 32.4 s, failing the <= 15.0 s requirement.... |
| `REQ-AUT-052` | **SUPPORTED** | `PARTIAL` | `NUMERIC_REASONING_FAILURE` | Node resumed transmission in 94.6 ms (within 90 to 110 ms window).... |
| `REQ-AUT-053` | **PARTIAL** | `UNKNOWN` | `UNKNOWN_CLASSIFICATION_FAILURE` | E2E Profile 01 verified on 8 of 16 message IDs.... |
| `REQ-AUT-055` | **CONFLICT** | `UNKNOWN` | `CONTRADICTION_FAILURE` | Datasheet limits common mode voltage to ±7.0 V, contradicting required ±12.0 V tolerance.... |
| `REQ-AUT-061` | **SUPPORTED** | `UNKNOWN` | `POST_LLM_DETERMINISTIC_REGRESSION` | UDS 0x10 response time was 18.4 ms (<= 50.0 ms required).... |

---

## 6. Bottleneck Analysis & Next Technical Improvements

### 🔍 Main Bottleneck:
- **Deterministic Post-Processing** causes more atomic regressions than corrections. Inspect qualification and reconciliation transitions in the atomic diagnostics sheet.

### 🚀 Recommended Next Improvements:
1. **Annotate Explicit Atomic Truth:** Add an expected state for every benchmark condition so atomic accuracy no longer depends on labels inferred from the final requirement verdict.
2. **Use Oracle Ablations:** Compare end-to-end, contract-oracle, evidence-oracle, and combined-oracle runs before changing a production stage.
3. **Review Stage Transitions:** Prioritize cases marked `DAMAGED`, then address `UNCHANGED_WRONG`; do not tune aggregation based only on final-status mismatches.
