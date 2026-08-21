# TraceAudit AI — Benchmark & Evaluation Framework

Reproducible end-to-end evaluation and benchmarking framework for the **TraceAudit AI requirements verification pipeline**.

It compares **Ground Truth engineering specifications** against the **Actual TraceAudit Pipeline Output** across extraction, evidence retrieval, deterministic verification, contradiction detection, and hallucination avoidance.

---

## 🚀 Quickstart

### 1. Run the Benchmark
Execute the complete end-to-end benchmark against the real TraceAudit backend pipeline:

```bash
# Using python module syntax:
python -m evaluation.run_evaluation

# Or direct script invocation:
python evaluation/run_evaluation.py
```

### 2. Run Automated Framework Unit Tests
Run the evaluation test suite validating ground truth schemas, metrics algorithms, normalization, and failure classification:

```bash
python -m unittest discover -s evaluation/tests -p "test_*.py"
```

### 3. Regenerate Synthetic Dataset
If you modify requirement clauses or add new test files:

```bash
python evaluation/generate_dataset.py
```

---

## 📁 Repository & Directory Layout

```
evaluation/
├── __init__.py
├── README.md                           # Comprehensive framework documentation (this file)
├── generate_dataset.py                 # Generates 7 synthetic documents & 3 ground truth JSON files
├── run_evaluation.py                   # Main CLI benchmark runner executing the real pipeline
├── metrics.py                          # Quantitative metrics engine (Precision, Recall, F1, Recall@K, MRR)
├── report.py                           # Formatted Markdown report & JSON result exporter
├── documents/                          # 7 Synthetic Automotive BCU technical documents
│   ├── 01_Product_Requirements_SRS.docx
│   ├── 02_System_Architecture_Spec.pdf
│   ├── 03_OEM_Supplier_Datasheet.pdf
│   ├── 04_Battery_Management_Test_Report.pdf
│   ├── 05_Environmental_EMC_Report.pdf
│   ├── 06_Thermal_Runaway_Safety_Report.pdf
│   └── 07_Compliance_Verification_Matrix.xlsx
├── ground_truth/                       # Machine-readable ground truth data
│   ├── requirements.json               # 30 requirements with categories & expected statuses
│   ├── evidence_links.json             # Document/page/quote mappings with relationship labels
│   └── expected_findings.json          # Expected triage findings for Partial/Missing/Conflict
├── tests/
│   ├── __init__.py
│   └── test_evaluation.py             # 9 automated unit tests for evaluation logic
└── results/
    ├── latest_results.json             # Full machine-readable evaluation results
    └── latest_report.md                # Human-readable Markdown benchmark report
```

---

## 📊 Benchmark Dataset: Automotive Battery Control Unit (BCU-800V)

The benchmark is built on a 100% synthetic, realistic technical documentation suite for an **800V Automotive Battery Control Unit (BCU)** with **30 requirements** covering 5 core verification scenarios:

| Scenario | Ground Truth Status | Count | Example Requirement |
|---|---|---|---|
| **A. Supported (Verified)** | `Supported` | 10 | Nominal voltage 400V–800V DC tested at 400V, 600V, 800V with zero resets |
| **B. Partial Coverage** | `Partial` | 8 | Ambient temperature $-40^\circ\text{C}$ to $+85^\circ\text{C}$ tested only from $-20^\circ\text{C}$ to $+70^\circ\text{C}$ (gap) |
| **C. Missing Evidence** | `Missing` | 7 | ISO 21434 secure boot with ECDSA P-384 hardware root-of-trust (no test report) |
| **D. Potential Conflict** | `Conflict` | 5 | SRS states 400V–800V DC vs. Supplier Datasheet restricts to 750V DC max |
| **E. Human Review / Edge** | `Partial` / `Needs review` | 2 | Acoustic electrolyte leak detection calculation present, vehicle installation pending |

---

## 📐 Implemented Metrics

### 1. Requirement Extraction
- **Precision:** $\frac{TP}{TP + FP}$ — proportion of extracted requirements that match true clauses.
- **Recall:** $\frac{TP}{TP + FN}$ — proportion of true requirement clauses successfully extracted.
- **F1 Score:** $2 \cdot \frac{P \cdot R}{P + R}$ — harmonic mean of extraction quality.

### 2. Evidence Retrieval (Top-K & MRR)
- **Recall@1:** Percentage of queries where the top-ranked chunk is a ground-truth evidence hit.
- **Recall@3:** Percentage of queries where a ground-truth hit appears within the top 3 chunks.
- **Recall@5:** Percentage of queries where a ground-truth hit appears within the top 5 chunks.
- **Mean Reciprocal Rank (MRR):** $\frac{1}{|Q|} \sum_{i=1}^{|Q|} \frac{1}{\text{rank}_i}$ measuring ranking quality.

### 3. Verification Classification
- **Accuracy:** Overall proportion of requirements correctly classified across all 4 statuses (`Supported`, `Partial`, `Missing`, `Conflict`).
- **Macro F1:** Unweighted average F1 across all 4 status classes.
- **Weighted F1:** Support-weighted average F1.
- **4x4 Confusion Matrix:** Detailed breakdown of expected vs. predicted statuses.

### 4. Specialty Metrics
- **Conflict Detection F1:** Binary F1 for identifying cross-document parameter and semantic contradictions.
- **Missing Evidence Detection F1:** Binary F1 for correctly detecting absent evidence.
- **Unsupported Claim Rate (Hallucination Rate):**
  $$\text{Unsupported Claim Rate} = \frac{\text{Missing Requirements Classified as Supported}}{\text{Total Missing Requirements in Ground Truth}} \times 100\%$$

---

## 🔍 Failure Classification Schema

Every prediction discrepancy is logged in `evaluation/results/latest_results.json` and categorized into one of:

1. `extraction_error`: Requirement was missed or parsed incorrectly from the specification.
2. `retrieval_error`: Retriever failed to fetch relevant evidence chunks in the top candidates.
3. `verification_error`: Numerical range bounds, units, or rules misclassified the evidence.
4. `conflict_detection_error`: Cross-document contradiction was not identified.
5. `unsupported_claim`: System claimed `Supported` when no evidence exists (**Hallucination**).

---

## 🛠️ How to Add New Test Cases

1. Open `evaluation/generate_dataset.py`.
2. Add your requirement definition to `BENCHMARK_REQUIREMENTS` with:
   - `requirement_id` (e.g. `REQ-BCU-031`)
   - `req_code`
   - `title`
   - `description`
   - `category`
   - `severity`
   - `expected_status` (`Supported`, `Partial`, `Missing`, or `Conflict`)
   - `evidence_sources` (list of matching documents, pages, and quotes)
3. Update the corresponding document generation function (e.g., `create_battery_management_test_report_pdf`).
4. Run `python evaluation/generate_dataset.py` to re-generate the document corpus and JSON ground truth.
5. Run `python evaluation/run_evaluation.py` to benchmark the updated dataset.

---

## ⚠️ Benchmark Disclaimer

This benchmark is a **DEVELOPMENT & ENGINEERING TOOL** designed for regression testing and continuous improvement. It does not constitute regulatory or legal certification and should be used to guide iterative development of extraction, retrieval, and verification algorithms.
