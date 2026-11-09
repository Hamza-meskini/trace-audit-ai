"""TRU-06: turn reviewer decisions into evaluation cases.

Every human decision that confirms or corrects the AI verdict is a labelled
case taken from real work. This exports, per reviewed requirement, the latest
decision with what the model saw and decided at the time:

- the AI verdict and the reviewer's verdict (label), the resolution type and
  the rationale;
- the requirement's contract (conditions, logic) and the model's
  condition-level results, when the stored assessment is the one that was
  reviewed (``assessment_matches``); a later re-audit makes them stale;
- the cited evidence catalog and the ``audit_run_id``, which names the EVA-03
  recording index (``runs/<audit_run_id>.jsonl``) holding the raw calls, so
  the case's verification inputs can be re-run with replay_recorded_calls.py.

Reviews today override the requirement-level verdict only, so condition-level
labels come from "Contract correction" and "Evidence issue" rationales, not
from structured fields.

Cases hold customer text: the output must be outside the git checkout.
Reviewer names are replaced by a short hash.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "evaluation"))
os.environ.setdefault("DATABRICKS_MLFLOW_TRACING_ENABLED", "false")

CASE_FORMAT = "review-case-v1"
LABELLED_RESOLUTIONS = {"Confirm AI assessment", "Override verdict", "Evidence issue", "Contract correction"}
VERDICT_TO_CLASS = {
    "Supported": "SUPPORTED", "Partial": "PARTIAL", "Missing": "MISSING",
    "Conflict": "CONFLICT", "Unknown": "UNKNOWN", "Not applicable": "NOT_APPLICABLE",
}
CONTRACT_KEYS = ("conditions", "logic", "logic_tree", "semantic_clauses", "clause_coverage", "contract_complete")


def _reviewer_ref(name: Any) -> str | None:
    return hashlib.sha256(str(name).encode("utf-8")).hexdigest()[:10] if name else None


def latest_labelled_decision(history: Iterable[dict[str, Any]] | None) -> dict[str, Any] | None:
    labelled = [
        event for event in (history or [])
        if event.get("resolution_type") in LABELLED_RESOLUTIONS and event.get("action") != "Comment"
    ]
    return labelled[-1] if labelled else None


def review_case(row: dict[str, Any]) -> dict[str, Any] | None:
    """One case from a requirement row (project_id, req_code, title, description, extracted_parameters)."""
    data = row.get("extracted_parameters") or {}
    decision = latest_labelled_decision(data.get("review_history"))
    if decision is None:
        return None
    verification = data.get("verification") or {}
    matches = bool(decision.get("assessment_at")) and decision.get("assessment_at") == verification.get("assessed_at")
    human = decision.get("human_verdict")
    ai = decision.get("ai_verdict")
    return {
        "format": CASE_FORMAT,
        "case_id": f"{row.get('project_id')}:{row.get('req_code')}:{decision.get('id')}",
        "project_id": row.get("project_id"),
        "req_code": row.get("req_code"),
        "title": row.get("title"),
        "description": row.get("description"),
        "label": {
            "ai_verdict": ai,
            "human_verdict": human,
            "expected_status": VERDICT_TO_CLASS.get(human),
            "agrees": human is not None and human == ai,
            "resolution_type": decision.get("resolution_type"),
            "rationale": decision.get("comment"),
            "reviewer_ref": _reviewer_ref(decision.get("reviewer")),
            "reviewer_verified": decision.get("identity_verified"),
            "identity_source": decision.get("identity_source"),
            "decided_at": decision.get("created_at"),
        },
        "assessment_matches": matches,
        "assessment": {
            "assessed_at": verification.get("assessed_at"),
            "audit_run_id": verification.get("audit_run_id"),
            "input_fingerprint": verification.get("input_fingerprint"),
            "model": verification.get("model"),
            "condition_results": verification.get("condition_results") if matches else None,
            "evidence_catalog": verification.get("evidence_catalog") if matches else None,
        },
        "contract": {key: data.get(key) for key in CONTRACT_KEYS if key in data},
    }


def review_cases(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [case for case in (review_case(row) for row in rows) if case is not None]


def summarize(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Agreement between the AI and reviewers, the measure these cases make possible."""
    with_verdict = [case for case in cases if case["label"]["human_verdict"]]
    confusion = Counter(f"{case['label']['ai_verdict']} -> {case['label']['human_verdict']}" for case in with_verdict)
    return {
        "cases": len(cases),
        "with_human_verdict": len(with_verdict),
        "agreement": round(sum(case["label"]["agrees"] for case in with_verdict) / len(with_verdict), 4) if with_verdict else None,
        "replayable": sum(1 for case in cases if case["assessment_matches"] and case["assessment"]["audit_run_id"]),
        "by_resolution": dict(Counter(case["label"]["resolution_type"] for case in cases)),
        "ai_to_human": dict(sorted(confusion.items())),
    }


def output_is_outside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(REPO)
    except ValueError:
        return True
    return False


async def load_rows(project_id: str | None) -> list[dict[str, Any]]:
    from sqlalchemy import select

    from app.database import get_db_context
    from app.models.requirement import Requirement

    async with get_db_context() as db:
        query = select(Requirement)
        if project_id:
            query = query.where(Requirement.project_id == project_id)
        requirements = (await db.execute(query)).scalars().all()
        return [
            {
                "project_id": req.project_id, "req_code": req.req_code, "title": req.title,
                "description": req.description, "extracted_parameters": req.extracted_parameters,
            }
            for req in requirements
        ]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, help="JSONL file outside the git checkout")
    parser.add_argument("--project", help="limit to one project id")
    args = parser.parse_args(argv)
    out = Path(args.out)
    if not output_is_outside_repo(out):
        parser.error("cases hold customer text; write them outside the git checkout")
    cases = review_cases(asyncio.run(load_rows(args.project)))
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(json.dumps(case, ensure_ascii=False) + "\n")
    print(json.dumps(summarize(cases), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
