# TraceAudit AI 🛡️

**Enterprise AI-Assisted Requirements Auditing & Technical Evidence Traceability Platform**

TraceAudit AI is a production-grade decision-support software platform engineered for hardware and systems engineering teams, product compliance auditors, and quality validation engineers. TraceAudit AI serves as an automated decision-support assistant and audit accelerator; it does not replace formal human engineering judgment or sign-off. It automatically ingests complex technical documentation, extracts structured requirement contracts, searches across multi-format evidence files, and formally analyzes specification compliance while detecting cross-document contradictions, compound condition gaps, and non-authoritative evidence modalities.

---

## 🌟 Key Capabilities

- 📄 **Multi-Format Technical Document Ingestion:** High-fidelity chunking and table parsing across PDF test reports, Word SRS specifications (`.docx`), Excel compliance matrices (`.xlsx`), and datasheets.
- 📐 **Structured Requirement Extraction (100% F1):** Automatically extracts mandatory clauses, engineering categories, severities, atomic conditions, and strict numeric tolerance bounds ($V, \text{mA}, \text{ms}, ^\circ\text{C}, \text{mbar}$).
- 🔎 **Hybrid BM25 Lexical & Dense Retrieval:** Multi-document evidence linking with code-boosting and document type diversification.
- 🔬 **Deterministic SI Validation + LLM Semantic Reasoner:**
  - **Local Python SI Validator:** Formally verifies metric ranges ($[T_{min}, T_{max}] \supseteq [R_{min}, R_{max}]$), threshold limits ($\le, \ge$), and timing latency without hallucinations.
  - **Source Authority & Modality Classifier:** Distinguishes empirical test reports from theoretical simulations, calculations, and component datasheets.
  - **Databricks Model Serving & Gemini 3.7 Flash Reasoning:** Evaluates multi-condition engineering clauses, compound predicates ($X \land Y \land Z$), and physical vs simulation modalities.
- ⚡ **Strict Evidence-Grounded Verification:** Achieves a **0.00% Unsupported Claim Rate** on benchmark suites (evaluated by verifying that no requirement marked SUPPORTED lacks authoritative empirical evidence).
- 🗺️ **Interactive Traceability Matrix:** Visual network graph linking Requirements $\to$ Documents $\to$ Evidence Quotes $\to$ Audit Findings.
- 📊 **Auditable Reporting:** Exportable executive compliance files with audit trails and human-in-the-loop review actions.

---

## 📊 Scientific Benchmark Suites

TraceAudit AI is evaluated across two distinct benchmark suites:

### 1. Foundational Benchmark (30 Requirements)
Evaluates core requirement extraction, basic single-condition numeric ranges, threshold comparisons, and formal compliance tracking records in an aerospace/automotive battery control system (BCU) specification suite (30 requirements, 7 technical documents, 70 evidence segments):

| Metric | Score | Industry Context |
|---|---|---|
| **Requirement Extraction F1** | **100.0%** (Precision: 100%, Recall: 100%) | Extracts all requirements with exact numeric parameters |
| **Retrieval Recall@3** | **91.30%** | Relevant lab report in top 3 ranked chunks |
| **Retrieval Recall@5** | **95.65%** | Relevant lab report in top 5 ranked chunks |
| **Verification Accuracy** | **86.67%** | Correct compliance status classification |
| **Verification Macro F1** | **87.41%** | Balanced across Supported, Partial, Missing, Conflict |
| **Conflict Detection F1** | **90.91%** | Catches datasheet rating & thermal derating contradictions |
| **Missing Evidence Detection F1** | **100.0%** | Perfect recall on unverified and "Not Started" clauses |
| **Unsupported Claim Rate** | **0.00%** | Zero false verifications on missing evidence |

### 2. Complex Automotive Benchmark (100 Requirements, 172 Atomic Conditions)
Evaluates advanced multi-condition compliance across 10 vehicle subsystems (HV BMS, Traction Inverter, DC-DC, Charging, Thermal, CAN-FD/Ethernet, UDS Diagnostics, ASIL-D Safety, ISO 21434 Cyber, EMC), testing:
- **Multi-Condition Decomposition (172 Atomic Conditions):** Detecting partial compliance where one sub-clause passes but another is unmeasured or pending.
- **Source Authority & Modality Discrimination:** Accurately identifying theoretical simulations (SPICE, CFD, MATLAB) and architecture specs as `UNKNOWN` for physical requirements.
- **Entity & Scope Reasoning:** Preventing component datasheet ratings (e.g. ASIC max voltage) from creating false conflicts against system-level requirements when system tests pass.
- **Cross-Document Contradiction Detection:** Detecting subtle datasheet deratings and hardware interface limit violations.


---

## 🏗️ System Architecture

```
                               ┌─────────────────────────────┐
                               │ Technical Documents Archive │
                               │ (SRS, Test Logs, Datasheets)│
                               └──────────────┬──────────────┘
                                              │
                                              ▼
                               ┌─────────────────────────────┐
                               │ Document Ingestion Pipeline │
                               │ (PyMuPDF, docx, openpyxl)   │
                               └──────────────┬──────────────┘
                                              │
                       ┌──────────────────────┴──────────────────────┐
                       ▼                                             ▼
        ┌─────────────────────────────┐               ┌─────────────────────────────┐
        │    Requirement Extractor    │               │   BM25 Retrieval & Chunks   │
        │   (Contracts & Parameters)  │               │   (Evidence Vector Index)   │
        └──────────────┬──────────────┘               └──────────────┬──────────────┘
                       │                                             │
                       └──────────────────────┬──────────────────────┘
                                              │
                                              ▼
                        ┌───────────────────────────────────────────┐
                        │   TraceAudit Hybrid Verification Engine   │
                        ├───────────────────────────────────────────┤
                        │ 1. Deterministic SI Range & Bounds Proof  │
                        │ 2. Contradiction & Matrix Precedence      │
                        │ 3. Batched LLM Reasoner (Gemini / DBX)    │
                        └─────────────────────┬─────────────────────┘
                                              │
                                              ▼
                        ┌───────────────────────────────────────────┐
                        │  Interactive Traceability & Findings UI   │
                        │  (React 19, Vite, TanStack Router, Tailwind)│
                        └───────────────────────────────────────────┘
```

---

## 📁 Repository Structure

```
AudiTrace/
├── backend/                        # Python FastAPI Backend
│   ├── app/
│   │   ├── api/                    # REST API Endpoints (Projects, Documents, Requirements)
│   │   ├── models/                 # SQLAlchemy DB Models
│   │   ├── schemas/                # Pydantic Schemas (Contract, Claim, Verification)
│   │   ├── services/               # Core Auditing & Processing Engine
│   │   │   ├── ingestion.py        # PDF, DOCX, XLSX Ingestion
│   │   │   ├── extraction.py       # Requirement Extraction Engine
│   │   │   ├── retrieval.py        # BM25 Lexical Retrieval Engine
│   │   │   ├── classification.py   # Coverage Assessment & Batching
│   │   │   ├── contradiction.py    # Cross-Document Contradiction Detector
│   │   │   ├── llm_client.py       # Unified Gemini & Databricks AI Gateway Client
│   │   │   ├── verification_reasoner.py # Multi-Condition Semantic Reasoner
│   │   │   └── validators/         # Modular Deterministic SI Validators
│   │   │       ├── numeric_range.py
│   │   │       ├── threshold.py
│   │   │       ├── duration.py
│   │   │       ├── test_verdict.py
│   │   │       └── semantic.py
│   │   ├── config.py               # Central Settings & Model Routing
│   │   ├── database.py             # SQLite / AioSQLite Engine
│   │   └── main.py                 # FastAPI Application Factory
│   └── venv/                       # Isolated Python Virtualenv
├── evaluation/                     # Scientific Benchmark & Testing Suite
│   ├── run_evaluation.py           # End-to-end Benchmark Runner (--oracle, --model)
│   ├── generate_dataset.py         # Ground-Truth Specification & Test Suite
│   ├── metrics.py                  # Evaluation Metrics Engine
│   ├── tests/                      # Automated Unit Tests
│   └── results/                    # Latest Benchmark Output (JSON & Markdown)
├── src/                            # React 19 Frontend Web Application
│   ├── components/                 # UI Component Library (shadcn/ui, Radix UI)
│   ├── routes/                     # TanStack Router Pages (Dashboard, Findings, Matrix)
│   ├── hooks/                      # TanStack Query Data Hooks
│   └── lib/                        # API Client & Utility Functions
└── package.json                    # Frontend Package Manifest & Scripts
```

---

## 🚀 Quick Start

### 1. Prerequisites
- **Node.js**: v20.x or higher
- **Python**: v3.11 or higher
- **npm** or **pnpm**

### 2. Backend Setup
```bash
# Navigate to backend directory
cd backend

# Create virtual environment
python -m venv venv

# Activate virtual environment (Windows PowerShell)
.\venv\Scripts\Activate.ps1

# Install backend dependencies
pip install -r requirements.txt

# Configure environment variables
copy .env.example .env
```

Edit `backend/.env` with your preferred AI provider:
```env
# Option A: Google Gemini API Key (Google AI Studio)
GEMINI_API_KEY="AIzaSy..."
LLM_MODEL="gemini-3.7-flash"
GEMINI_THINKING_LEVEL="HIGH"

# Option B: Databricks Model Serving AI Gateway
DATABRICKS_TOKEN="dapi..."
DATABRICKS_BASE_URL="https://dbc-4973b3f3-18e4.cloud.databricks.com/ai-gateway/mlflow/v1"
DATABRICKS_MODEL="system.ai.qwen35-122b-a10b"
```

Start the FastAPI backend:
```bash
python -m uvicorn app.main:app --reload --port 8000
```
*API docs available at:* `http://localhost:8000/docs`

---

### 3. Frontend Setup
```bash
# In the root repository directory:
npm install

# Start Vite development server
npm run dev
```
*Application available at:* `http://localhost:5173`

---

### 4. Running Automated Tests & Benchmark Suite
```bash
# Run all backend unit tests (19 tests)
.\backend\venv\Scripts\python.exe -m unittest discover -s evaluation/tests -p "test_*.py"

# Run full end-to-end audit benchmark pipeline
.\backend\venv\Scripts\python.exe evaluation/run_evaluation.py

# Run oracle retrieval experiment (isolating verifier ceiling)
.\backend\venv\Scripts\python.exe evaluation/run_evaluation.py --oracle
```

---

## 🔒 Important Product Positioning

> [!IMPORTANT]
> TraceAudit AI is an **engineering decision-support tool**, not a legal certification authority. It provides automated technical documentation analysis, evidence traceability, and inconsistency detection. Final conformity assessment and safety sign-off remain the responsibility of qualified human engineers and compliance officers.

---

## 📜 License

MIT License. Designed & Developed for Enterprise Technical Compliance Engineering.
