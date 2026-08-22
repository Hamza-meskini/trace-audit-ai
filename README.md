# TraceAudit AI 🛡️

**Enterprise AI-Assisted Requirements Auditing & Technical Evidence Traceability Platform**

TraceAudit AI is a production-grade decision-support software platform engineered for hardware and systems engineering teams, product compliance auditors, and quality validation engineers. It automatically ingests complex technical documentation, extracts structured requirement contracts, searches across multi-format evidence files, and mathematically proves specification compliance while detecting potential cross-document contradictions and coverage gaps.

---

## 🌟 Key Capabilities

- 📄 **Multi-Format Technical Document Ingestion:** High-fidelity chunking and table parsing across PDF test reports, Word SRS specifications (`.docx`), Excel compliance matrices (`.xlsx`), and datasheets.
- 📐 **Structured Requirement Extraction (100% F1):** Automatically extracts mandatory clauses, engineering categories, severities, and strict numeric tolerance bounds ($V, \text{mA}, \text{ms}, ^\circ\text{C}, \text{mbar}$).
- 🔎 **Hybrid BM25 Lexical & Dense Retrieval:** Multi-document evidence linking with code-boosting and document type diversification.
- 🔬 **Deterministic SI Validation + LLM Semantic Reasoner:**
  - **Local Python SI Validator:** Formally verifies metric ranges, threshold limits ($\le, \ge$), and timing latency without hallucinations.
  - **Databricks Model Serving AI Gateway (`system.ai.qwen35-122b-a10b` / `meta-llama-3-3-70b-instruct`):** Evaluates multi-condition engineering clauses, compound predicates ($X \land Y \land Z$), and physical vs simulation modalities.
  - **Google Gemini 3.7 Flash Thinking (`HIGH`):** Deep multi-tier reasoning with native JSON schema validation.
- ⚡ **Zero Hallucination Guarantee:** Enforces a **0.00% Unsupported Claim Rate** via strict quote citation checking.
- 🗺️ **Interactive Traceability Matrix:** Visual network graph linking Requirements $\to$ Documents $\to$ Evidence Quotes $\to$ Audit Findings.
- 📊 **Auditable Reporting:** Exportable executive compliance files with audit trails and human-in-the-loop review actions.

---

## 📊 Scientific Benchmark Performance

Evaluated against an authoritative aerospace and automotive battery control system (BCU) specification suite (30 requirements, 7 technical documents, 70 evidence segments):

| Metric | Score | Industry Context |
|---|---|---|
| **Requirement Extraction F1** | **100.0%** (Precision: 100%, Recall: 100%) | Extracts all requirements with exact numeric parameters |
| **Retrieval Recall@3** | **91.30%** | Relevant lab report in top 3 ranked chunks |
| **Retrieval Recall@5** | **95.65%** | Relevant lab report in top 5 ranked chunks |
| **Verification Accuracy** | **86.67%** | Correct compliance status classification |
| **Verification Macro F1** | **87.41%** | Balanced across Supported, Partial, Missing, Conflict |
| **Conflict Detection F1** | **90.91%** | Catches datasheet rating & thermal derating contradictions |
| **Missing Evidence Detection F1** | **100.0%** | Perfect recall on unverified and "Not Started" clauses |
| **Unsupported Claim Rate** | **0.00%** | **Zero hallucinations** (mathematically verified citations) |

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
