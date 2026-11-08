"""Offline replay of the verification stage over saved benchmark runs.

Each saved run (``evaluation/results/runs/<id>/results.json``) records the
verification inputs and every LLM outcome of the verification stage. This tool
feeds those recordings back through the *current* deterministic code
(qualification, reconciliation, aggregation, review gate) without any network
access, so changes to that code can be scored on 240 Nova instances in seconds
and for free.

What replay can and cannot measure:

- Exact: changes to verdict_aggregator, the classification review gates,
  evidence qualification, units, and the post-LLM condition audit.
- Verdicts faithful, diagnostics approximate: changes to retry triggers and
  routing. Every route is answered with the same recorded final snapshot.
- Not measurable (by construction shows zero change): prompt, schema or model
  changes, a secondary adjudicator, batch sibling effects. Use live runs.

Usage (from the repo root)::

    backend/venv/Scripts/python.exe -B evaluation/replay_verification.py \
        --label baseline --out replay_baseline.json
    backend/venv/Scripts/python.exe -B evaluation/replay_verification.py \
        --backend path/to/worktree/backend --label candidate \
        --compare replay_baseline.json --out replay_candidate.json

The fidelity gate (evaluation/ACCURACY_PROTOCOL.md) is the default report:
unmodified code must reproduce the saved predicted status.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUNS_DIR = REPO / "evaluation" / "results" / "runs"
DEFAULT_RUNS = (
    "20260915T140120.543292Z",
    "20260915T222056.820191Z",
    "20260916T133251.054900Z",
    "20260922T115355.064325Z",
    "20260922T123239.778672Z",
)
PROVIDER_KEYS = (
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "TOKENROUTER_API_KEY",
    "DASHSCOPE_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "HF_TOKEN",
    "HUGGINGFACE_TOKEN", "DATABRICKS_TOKEN", "DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET",
)
FINAL_CLASSES = ["SUPPORTED", "PARTIAL", "CONFLICT", "MISSING", "UNKNOWN", "NOT_APPLICABLE"]
SCORED_CLASSES = ["SUPPORTED", "PARTIAL", "CONFLICT", "MISSING", "UNKNOWN"]
# Exceptions the pipeline swallows and turns into UNKNOWN verdicts. In a replay
# they mean the code under test is broken, so they fail the run.
SWALLOWED_ERROR_MARKERS = (
    "Batch verification LLM call failed",
    "Citation grounding failed for one batch item",
    "Combined verification repair failed",
    "Structured LLM verification call failed",
    "Compact verification recovery failed",
    "Citation grounding failed for",
    "Secondary semantic adjudication failed",
    "Individual verification failed",
)


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--backend", default=str(REPO / "backend"),
                        help="backend folder to import `app` from (e.g. a candidate worktree)")
    parser.add_argument("--runs", nargs="*", default=list(DEFAULT_RUNS),
                        help="run ids under evaluation/results/runs (default: the five replayable Nova runs)")
    parser.add_argument("--label", default="replay", help="name recorded in the output")
    parser.add_argument("--out", help="write per-instance rows and summary to this JSON file")
    parser.add_argument("--compare", help="earlier replay JSON to diff against (default: the saved predictions)")
    parser.add_argument("--quiet", action="store_true", help="print the summary only")
    parser.add_argument("--simulate", choices=["derived-status"], default=None,
                        help="approximate a prompt candidate instead of replaying verbatim. 'derived-status' "
                             "answers every call with the mechanical aggregate of the model's own condition "
                             "results as its requirement status (an upper bound for a derive-the-status prompt: "
                             "it assumes the condition results themselves do not change)")
    return parser.parse_args(argv)


ARGS = _parse_args() if __name__ == "__main__" else None

# ---- environment pinning before any app import ------------------------------
os.environ["DATABRICKS_MLFLOW_TRACING_ENABLED"] = "false"
os.environ["DATABASE_BACKEND"] = "sqlite"
os.environ["PYTHONUTF8"] = "1"
os.environ.setdefault("UPLOAD_DIR", str(Path(tempfile.gettempdir()) / "traceaudit-replay-uploads"))
for _key in PROVIDER_KEYS:
    os.environ.pop(_key, None)

import asyncio  # noqa: E402
import copy  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import re  # noqa: E402
import socket  # noqa: E402
from collections import Counter  # noqa: E402

import httpx  # noqa: E402

NETWORK_LOG: list[str] = []


def _tripwire(name):
    def _raise(*args, **kwargs):
        NETWORK_LOG.append(name)
        raise RuntimeError(f"offline replay: blocked {name}")
    return _raise


httpx.AsyncClient.__init__ = _tripwire("httpx.AsyncClient")  # type: ignore[method-assign]
httpx.Client.__init__ = _tripwire("httpx.Client")  # type: ignore[method-assign]
socket.getaddrinfo = _tripwire("socket.getaddrinfo")  # type: ignore[assignment]
socket.create_connection = _tripwire("socket.create_connection")  # type: ignore[assignment]
_ORIGINAL_CONNECT = socket.socket.connect
_ORIGINAL_CONNECT_EX = socket.socket.connect_ex


def _loopback_only(original, name):
    # asyncio's Windows self-pipe connects to 127.0.0.1; everything else is refused.
    def _connect(self, address):
        host = address[0] if isinstance(address, tuple) and address else address
        if host in ("127.0.0.1", "::1"):
            return original(self, address)
        NETWORK_LOG.append(f"{name}:{host}")
        raise RuntimeError(f"offline replay: blocked {name} to {host}")
    return _connect


socket.socket.connect = _loopback_only(_ORIGINAL_CONNECT, "socket.connect")  # type: ignore[method-assign]
socket.socket.connect_ex = _loopback_only(_ORIGINAL_CONNECT_EX, "socket.connect_ex")  # type: ignore[method-assign]

BACKEND = Path(ARGS.backend if ARGS else os.environ.get("REPLAY_BACKEND", REPO / "backend")).resolve()
sys.path.insert(0, str(BACKEND))

from app.config import settings  # noqa: E402

# Pin the configuration the saved runs used, so backend/.env cannot add calls
# the recordings cannot answer (for example a secondary adjudicator).
settings.DATABRICKS_TOKEN = "offline-replay-placeholder"  # non-empty: keeps the LLM path; no SDK lookup
for _attr in PROVIDER_KEYS[:9]:
    if hasattr(settings, _attr):
        setattr(settings, _attr, "")
settings.LLM_PROVIDER = "databricks"
settings.LLM_MODEL = "system.ai.llama-4-maverick"
settings.GEMINI_THINKING_LEVEL = "HIGH"
settings.SECONDARY_ADJUDICATOR_ENABLED = True
settings.SECONDARY_ADJUDICATOR_MODEL = ""
settings.DATABRICKS_FALLBACK_MODELS = []
settings.CITATION_GROUNDING_ENABLED = True
settings.CITATION_GROUNDING_MODEL = ""
settings.DATABRICKS_MLFLOW_TRACING_ENABLED = False

from app.services import llm_client  # noqa: E402
from app.services import verification_reasoner as vr  # noqa: E402
from app.services.classification import batch_assess_requirements  # noqa: E402
from app.schemas.verification_result import (  # noqa: E402
    BatchVerificationResult,
    CitationGroundingResult,
    VerificationAnalysisResult,
)

llm_client._generate_structured_unlimited = _tripwire("llm_client._generate_structured_unlimited")  # type: ignore[assignment]
llm_client.generate_structured = _tripwire("llm_client.generate_structured")  # type: ignore[assignment]


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


LOG_HANDLER = _ListHandler()
for _name in ("traceaudit.verifier", "traceaudit.aggregator", "traceaudit.qualification", "traceaudit.llm"):
    _logger = logging.getLogger(_name)
    _logger.addHandler(LOG_HANDLER)
    _logger.setLevel(logging.INFO)

PIPELINE_OWNED = {"validation_state", "validation_notes", "evidence_spans"}


def normal_final(value) -> str:
    """Same mapping as evaluation/run_fmvss305_benchmark.py::_normal_final."""
    status = str(value or "UNKNOWN").strip().upper().replace(" ", "_")
    aliases = {
        "PASS": "SUPPORTED", "PASSED": "SUPPORTED", "VERIFIED": "SUPPORTED",
        "PARTIALLY_SUPPORTED": "PARTIAL", "FAILED": "CONFLICT", "FAIL": "CONFLICT",
        "NO_EVIDENCE": "MISSING", "INCONCLUSIVE": "UNKNOWN", "N/A": "NOT_APPLICABLE",
        "NA": "NOT_APPLICABLE", "EXEMPT": "NOT_APPLICABLE",
    }
    status = aliases.get(status, status)
    return status if status in FINAL_CLASSES else "UNKNOWN"


def macro_f1(pairs: list[tuple[str, str]]) -> float:
    """Same definition as evaluation/run_fmvss305_benchmark.py::_macro_f1."""
    scores = []
    for label in FINAL_CLASSES:
        support = sum(1 for gold, _ in pairs if gold == label)
        if not support:
            continue
        tp = sum(1 for gold, pred in pairs if gold == label and pred == label)
        fp = sum(1 for gold, pred in pairs if gold != label and pred == label)
        fn = support - tp
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        scores.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return round(100.0 * sum(scores) / len(scores), 2) if scores else 0.0


def _recover_reason(ai_analysis: str) -> str:
    text = ai_analysis or ""
    for marker in (" Whole-clause rationale: ", " Provisional model rationale: "):
        if marker in text:
            return text.split(marker, 1)[1]
    return text


class Recording:
    """The LLM outcome a saved row implies, rebuilt from its diagnostics."""

    def __init__(self, row: dict):
        diag = row["pipeline_diagnostics"]
        self.code = row["requirement_id"]
        # Path B rows went through the individual repair path; their snapshot
        # after the repair merge is what every verification call should return.
        self.path = "B" if "primary_condition_results_after_retry" in diag else "A"
        snapshot = diag["primary_condition_results_after_retry"] if self.path == "B" else diag["llm_condition_results"]
        self.conditions = [{k: v for k, v in item.items() if k not in PIPELINE_OWNED} for item in snapshot]
        self.status = diag["llm_provisional_status"]
        self.confidence = diag["llm_provisional_confidence"]
        self.reason = _recover_reason(row.get("ai_analysis", ""))
        self.requested = set(diag.get("citation_grounding_requested_ids") or [])
        self.failed = set(diag.get("citation_grounding_failed_ids") or [])
        self.citations = {}
        for item in diag["llm_condition_results"]:
            spans = item.get("evidence_spans") or []
            if spans:
                self.citations[item["condition_id"]] = (spans[0]["evidence_id"], spans[0]["exact_quote"])

    def payload(self) -> dict:
        return {
            "status": self.status,
            "confidence": self.confidence,
            "condition_results": copy.deepcopy(self.conditions),
            "reason": self.reason,
            "highlight": None,
        }


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


class ReplayLLM:
    """Stands in for verification_reasoner.generate_structured."""

    def __init__(self, recordings: dict[str, Recording]):
        self.rec = recordings
        self.calls: list[dict] = []
        self.unexpected: list[dict] = []
        self.approximate: list[dict] = []

    async def __call__(self, prompt, response_model, model=None, system_instruction=None,
                       thinking_level=None, max_output_tokens=None, allow_model_fallback=True,
                       diagnostics=None):
        name = response_model.__name__
        entry = {"response_model": name, "prompt_sha": _sha(prompt)}
        self.calls.append(entry)
        if name == "BatchVerificationResult":
            codes = re.findall(r"^- Req ID: (\S+)\s*$", prompt, re.M)
            entry.update(kind="batch", codes=codes)
            items = []
            for code in codes:
                rec = self.rec.get(code)
                if rec is None:
                    self.unexpected.append({"kind": "batch-unknown-code", "code": code})
                    continue
                items.append({"req_code": code, **rec.payload()})
            if not codes:
                self.unexpected.append({"kind": "batch-no-codes", "prompt_sha": entry["prompt_sha"]})
            return BatchVerificationResult.model_validate({"batch_results": items})
        if name == "VerificationAnalysisResult":
            match = re.search(r"^- Requirement ID: (\S+)\s*$", prompt, re.M)
            code = match.group(1) if match else None
            kind = "repair" if "CORRECTION REQUIRED" in prompt else "first"
            entry.update(kind=kind, codes=[code])
            rec = self.rec.get(code)
            if rec is None:
                self.unexpected.append({"kind": f"{kind}-unknown-code", "code": code})
                return None
            if kind == "repair" and rec.path == "A":
                self.approximate.append({"kind": "repair-for-batch-success-item", "code": code})
            return VerificationAnalysisResult.model_validate(rec.payload())
        if name == "CitationGroundingResult":
            match = re.search(r"^Requirement: (\S+) — ", prompt, re.M)
            code = match.group(1) if match else None
            block = ""
            if "\nTargets: " in prompt:
                block = prompt.split("\nTargets: ", 1)[1].split("\n\nEvidence excerpts:\n", 1)[0]
            targets = re.findall(r"'condition_id': '([^']+)'", block)
            entry.update(kind="grounding", codes=[code], targets=targets)
            rec = self.rec.get(code)
            if rec is None:
                self.unexpected.append({"kind": "grounding-unknown-code", "code": code})
                return None
            citations = []
            for cid in targets:
                if cid in rec.failed:
                    continue
                if cid in rec.citations:
                    if cid not in rec.requested:
                        self.approximate.append({"kind": "grounding-not-originally-requested", "code": code, "cid": cid})
                    evidence_id, quote = rec.citations[cid]
                    citations.append({"condition_id": cid, "evidence_id": evidence_id, "exact_quote": quote})
                else:
                    self.approximate.append({"kind": "grounding-no-recorded-span", "code": code, "cid": cid})
            return CitationGroundingResult.model_validate({"citations": citations})
        # SemanticAdjudicationResult (secondary adjudicator or compact recovery)
        # never fired in the saved runs, so there is nothing to replay.
        kind = "compact_recovery" if "Recover a malformed" in prompt else (
            "secondary" if "independent second reviewer" in prompt else "other")
        entry.update(kind=kind)
        self.unexpected.append({"kind": kind, "response_model": name})
        return None


def _derived_statuses(req_items: list[dict], recordings: dict[str, Recording]) -> dict[str, str]:
    """--simulate derived-status: the requirement status a model would report if it
    derived the status from its own condition results with the production precedence."""
    from app.schemas.contract import parse_requirement_contract
    from app.schemas.verification_result import ConditionVerificationResult
    from app.services.verdict_aggregator import aggregate_condition_statuses

    derived = {}
    for item in req_items:
        rec = recordings.get(item["req_code"])
        if rec is None:
            continue
        # Same arguments as classification.batch_assess_requirements.
        contract = parse_requirement_contract(
            req_code=item["req_code"],
            title=item.get("title", ""),
            description=item.get("description", ""),
            category=item.get("category", "General"),
            structured_conditions=item.get("conditions"),
            semantic_clauses=item.get("semantic_clauses"),
            clause_coverage=item.get("clause_coverage"),
            unmapped_obligations=item.get("unmapped_obligations"),
            contract_complete=item.get("contract_complete"),
            logic=item.get("logic"),
            logic_tree=item.get("logic_tree"),
            decomposition_confidence=item.get("decomposition_confidence"),
            ambiguities=item.get("ambiguities"),
            validation_issues=item.get("validation_issues"),
        )
        results = [ConditionVerificationResult.model_validate(condition) for condition in rec.conditions]
        derived[item["req_code"]] = aggregate_condition_statuses(contract, results)[0]
    return derived


async def replay_run(run_id: str) -> dict:
    saved = json.loads((RUNS_DIR / run_id / "results.json").read_text(encoding="utf-8"))
    if not saved.get("verification_inputs"):
        raise SystemExit(f"{run_id} has no verification_inputs and cannot be replayed")
    rows = {row["requirement_id"]: row for row in saved["requirements"]}
    recordings = {code: Recording(row) for code, row in rows.items()}
    mock = ReplayLLM(recordings)
    vr.generate_structured = mock  # every LLM call site in verification_reasoner uses this bound name
    NETWORK_LOG.clear()
    LOG_HANDLER.records.clear()
    req_items = copy.deepcopy(saved["verification_inputs"])
    simulated = 0
    if ARGS is not None and ARGS.simulate == "derived-status":
        for code, status in _derived_statuses(req_items, recordings).items():
            simulated += status != recordings[code].status
            recordings[code].status = status
    assessments = await batch_assess_requirements(
        req_items=req_items,
        model=saved.get("verification_model") or "system.ai.llama-4-maverick",
        thinking_level="HIGH",
        batch_size=int((saved.get("generation_config") or {}).get("batch_size", 4)),
        spec_doc_names=set(req_items[0].get("spec_doc_names") or []),
    )
    errors = [
        record.getMessage()[:200] for record in LOG_HANDLER.records
        if any(marker in record.getMessage() for marker in SWALLOWED_ERROR_MARKERS)
    ]
    instances = []
    for code, row in rows.items():
        assessment = assessments.get(code)
        diag = (assessment.pipeline_diagnostics or {}) if assessment else {}
        predicted = normal_final(assessment.coverage_status) if assessment else "NOT_EXTRACTED"
        review = assessment.review_state if assessment else "Needs review"
        if diag.get("decision_source") == "operational_failure":
            errors.append(f"{code}: operational_failure")
        instances.append({
            "run_id": run_id,
            "requirement_id": code,
            "expected": row["expected_status"],
            "predicted": predicted,
            "saved_predicted": row["predicted_status"],
            "review": review,
            "saved_review": row["predicted_review_state"],
            "confidence": float(assessment.confidence) if assessment else None,
            "atomic_aggregate": normal_final(diag.get("atomic_aggregate_status")) if diag.get("atomic_aggregate_status") else None,
            "llm_label": normal_final(diag.get("llm_provisional_status")) if diag.get("llm_provisional_status") else None,
            "abstained": bool(diag.get("aggregator_abstained")),
            "blocking_issues": [str(item) for item in diag.get("aggregator_blocking_issues") or []],
            "gate_reasons": (diag.get("review_gate") or {}).get("reasons") or [],
            "conditions": {
                result.condition_id: [result.status, result.validation_state]
                for result in (assessment.condition_results if assessment else [])
            },
        })
    return {
        "run_id": run_id,
        "instances": instances,
        "calls": dict(Counter(call.get("kind") for call in mock.calls)),
        "unexpected": mock.unexpected,
        "approximate": mock.approximate,
        "network_attempts": list(NETWORK_LOG),
        "errors": errors,
        "simulated_label_changes": simulated,
    }


def summarize(instances: list[dict]) -> dict:
    pairs = [(item["expected"], item["predicted"]) for item in instances]
    total = len(pairs)
    correct = sum(gold == pred for gold, pred in pairs)
    auto = [item for item in instances if item["review"] == "Reviewed"]
    per_class = {}
    for label in SCORED_CLASSES:
        support = [item for item in instances if item["expected"] == label]
        if support:
            per_class[label] = f"{sum(item['predicted'] == label for item in support)}/{len(support)}"
    return {
        "instances": total,
        "correct": correct,
        "accuracy": round(100.0 * correct / total, 2) if total else 0.0,
        "macro_f1": macro_f1(pairs),
        "recall": per_class,
        "auto_closed": len(auto),
        "auto_closed_correct": sum(item["predicted"] == item["expected"] for item in auto),
        # Protocol metric: an auto-closed SUPPORTED that is wrong.
        "unsafe_false_auto_closes": sum(
            1 for item in auto if item["predicted"] == "SUPPORTED" and item["expected"] != "SUPPORTED"
        ),
        # The runner does not count this one; it is just as unsafe.
        "wrong_conflict_auto_closes": sum(
            1 for item in auto if item["predicted"] == "CONFLICT" and item["expected"] != "CONFLICT"
        ),
        "status_matches_saved": sum(item["predicted"] == item["saved_predicted"] for item in instances),
        "review_matches_saved": sum(item["review"] == item["saved_review"] for item in instances),
    }


def paired_flips(current: list[dict], reference: dict[tuple[str, str], str]) -> dict:
    fixed, broken, changed = [], [], []
    for item in current:
        key = (item["run_id"], item["requirement_id"])
        before = reference.get(key)
        if before is None or before == item["predicted"]:
            continue
        label = f"{item['requirement_id']} @{item['run_id'][:15]}: {before}->{item['predicted']} (gold {item['expected']})"
        if item["predicted"] == item["expected"]:
            fixed.append(label)
        elif before == item["expected"]:
            broken.append(label)
        else:
            changed.append(label)
    return {"fixed": len(fixed), "broken": len(broken), "net": len(fixed) - len(broken),
            "wrong_to_wrong": len(changed), "fixed_items": fixed, "broken_items": broken,
            "wrong_to_wrong_items": changed}


def main(args) -> int:
    results = [asyncio.run(replay_run(run_id)) for run_id in args.runs]
    instances = [item for result in results for item in result["instances"]]
    if args.compare:
        earlier = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        reference = {(item["run_id"], item["requirement_id"]): item["predicted"] for item in earlier["instances"]}
        reference_name = earlier.get("label", args.compare)
    else:
        reference = {(item["run_id"], item["requirement_id"]): item["saved_predicted"] for item in instances}
        reference_name = "saved predictions"
    summary = summarize(instances)
    flips = paired_flips(instances, reference)
    integrity = {
        "network_attempts": sum(len(r["network_attempts"]) for r in results),
        "unexpected_calls": sum(len(r["unexpected"]) for r in results),
        "approximate_calls": sum(len(r["approximate"]) for r in results),
        "swallowed_errors": [error for r in results for error in r["errors"]],
        "calls": dict(sum((Counter(r["calls"]) for r in results), Counter())),
    }
    ok = not integrity["network_attempts"] and not integrity["unexpected_calls"] and not integrity["swallowed_errors"]
    report = {
        "label": args.label,
        "backend": str(BACKEND),
        "simulate": args.simulate,
        "simulated_label_changes": sum(r["simulated_label_changes"] for r in results),
        "runs": args.runs,
        "summary": summary,
        "per_run": {r["run_id"]: summarize(r["instances"]) for r in results},
        "compared_with": reference_name,
        "flips": flips,
        "integrity": integrity,
        "integrity_ok": ok,
        "instances": instances,
    }
    print(f"[{args.label}] backend={BACKEND}")
    if args.simulate:
        print(f"  SIMULATION {args.simulate}: {report['simulated_label_changes']} recorded labels replaced "
              f"(an approximation, not a replay of recorded outputs)")
    print(f"  accuracy {summary['correct']}/{summary['instances']} = {summary['accuracy']}%  macro-F1 {summary['macro_f1']}  "
          f"recall {summary['recall']}")
    print(f"  auto-closed {summary['auto_closed']} (correct {summary['auto_closed_correct']}), unsafe SUPPORTED "
          f"{summary['unsafe_false_auto_closes']}, wrong CONFLICT {summary['wrong_conflict_auto_closes']}")
    print(f"  vs {reference_name}: fixed {flips['fixed']}, broken {flips['broken']}, net {flips['net']:+d}, "
          f"wrong->wrong {flips['wrong_to_wrong']}; status matches saved {summary['status_matches_saved']}/{summary['instances']}")
    print(f"  integrity: {'OK' if ok else 'FAILED'} {integrity['calls']} network={integrity['network_attempts']} "
          f"unexpected={integrity['unexpected_calls']} approximate={integrity['approximate_calls']} "
          f"errors={len(integrity['swallowed_errors'])}")
    if not args.quiet:
        for kind in ("fixed_items", "broken_items", "wrong_to_wrong_items"):
            for line in flips[kind]:
                print(f"    {kind.split('_')[0]:>6}: {line}")
        for error in integrity["swallowed_errors"][:20]:
            print(f"    ERROR: {error}")
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(ARGS))
