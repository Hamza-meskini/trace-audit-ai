# TraceAudit AI

Checks engineering requirements against the evidence in your technical documents.

Upload specifications, test reports and datasheets. TraceAudit extracts each requirement, finds the
evidence for it, and gives a verdict (**Supported, Partial, Missing, Conflict or Unknown**) with the
exact quotes it relied on. Anything uncertain goes to a human reviewer.

> TraceAudit is a decision-support tool, not a certification authority. Final sign-off stays with
> qualified engineers.

## How it works

1. **Ingest:** parses PDF, DOCX and XLSX, keeping tables, figures and page positions.
2. **Extract:** turns each requirement into atomic conditions (numeric bounds, units, test method, scope).
3. **Retrieve:** hybrid keyword and embedding search, with requirement IDs matched across formats.
4. **Verify:** an LLM judges each condition against cited evidence. Python, not the model, decides the
   final verdict: units, numeric limits, logic (ALL_OF / ANY_OF / IF_THEN) and source authority.
5. **Review:** reviewers confirm or override verdicts. Evidence is highlighted on the source page,
   and the result exports to Excel.

## Stack

- **Backend:** Python 3.13, FastAPI, SQLAlchemy (SQLite locally, Databricks SQL in production)
- **Frontend:** React 19, TanStack Start/Router/Query, Vite, Tailwind, shadcn/ui
- **Models:** Databricks Model Serving, Llama 4 Maverick by default. Gemini, OpenAI and Groq are optional.

## Quick start

**Backend**

```bash
cd backend
python -m venv venv
source venv/bin/activate        # Windows: .\venv\Scripts\Activate.ps1
pip install -r ../requirements.txt
cp .env.example .env            # then set DATABRICKS_TOKEN and DATABRICKS_BASE_URL
python -m uvicorn app.main:app --reload --port 8000
```

API docs: http://localhost:8000/docs

**Frontend** (from the repository root)

```bash
npm install
npm run dev
```

## Tests and evaluation

```bash
pytest -q                                              # backend tests, no model calls
npm run lint && npm run typecheck && npm run build     # frontend checks
python evaluation/replay_verification.py --quiet       # offline verdict replay
```

The replay re-scores five recorded benchmark runs through the current decision logic. Every change
to a verdict must be explained in `evaluation/replay_expectations.json`.

**Results on the Nova benchmark** (48 requirements, 12 technical PDFs):

| Measure | Result |
|---|---|
| Final-verdict accuracy, 5 live runs | **88.3% ± 2.4** |
| Offline replay of those runs through current logic | 234/240 (97.5%) |
| Unsafe auto-closes | **0** |

Nova is a development set: the pipeline was tuned on it, so these numbers do not predict accuracy
on new documents. See [`evaluation/`](evaluation/) for the protocol and other benchmarks.

## Configuration

All settings are in [`backend/app/config.py`](backend/app/config.py) and can be set through
environment variables or `backend/.env`. Settings that affect security and customer data (egress
allow-list, SSO identity, `SETTINGS_ADMINS`, call recording) are covered in
[`DEPLOYMENT.md`](DEPLOYMENT.md), along with deployment to Databricks Apps.

## Development rules

Before merging, make sure `pytest` and the replay pass and the frontend checks are clean. Never
commit `.env` files, customer documents or recordings.

## License

Proprietary. All rights reserved; see [LICENSE](LICENSE). Use requires a written license or pilot
agreement. Third-party dependencies keep their own licences; PyMuPDF is AGPL-3.0 unless a
commercial licence is obtained from Artifex.
