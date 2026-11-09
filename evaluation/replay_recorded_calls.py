"""EVA-03 frozen-input arm: re-run recorded model calls on identical inputs.

Record a run with LLM_RECORD_DIR=<dir outside the repo>, then:

  check      (default, offline) every recorded call's key recomputes from its
             request and its answer still parses into the schema: the
             recording replays exactly.
  seed-cache (offline) load the recorded answers into the layer-1 response
             cache so a re-audit of the same inputs makes zero paid calls.
  rerun      (live) send each recorded request again, optionally to another
             model or through a prompt transform, and score agreement with the
             recorded answer. Retrieval is not re-run, so differences come from
             the prompt or model alone (ACC-06, ACC-07, ACC-09 become measurable).

By default only verification-stage schemas are selected (about 36 of the ~110
calls in a run); pass --schemas all for every call.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))
os.environ.setdefault("DATABRICKS_MLFLOW_TRACING_ENABLED", "false")

VERIFICATION_SCHEMAS = (
    "BatchVerificationResult",
    "VerificationAnalysisResult",
    "CitationGroundingResult",
    "SemanticAdjudicationResult",
)
DECISION_KEYS = {"status", "verdict", "final_status", "coverage_status", "observed", "supported", "grounded"}


def load_records(record_dir: Path, run: Optional[str], schemas: Iterable[str] | str) -> list[dict[str, Any]]:
    """Recorded calls of one run (or every recorded call), filtered by schema, de-duplicated by key."""
    calls_root = record_dir / "calls"
    if run:
        index = record_dir / "runs" / f"{run}.jsonl"
        keys = [json.loads(line)["key"] for line in index.read_text(encoding="utf-8").splitlines() if line.strip()]
        paths = [calls_root / key[:2] / f"{key}.json" for key in dict.fromkeys(keys)]
    else:
        paths = sorted(calls_root.glob("*/*.json"))
    wanted = None if schemas == "all" else set(schemas)
    records = []
    for path in paths:
        if not path.exists():
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        schema = record["request"]["response_model"].rsplit(":", 1)[-1].rsplit(".", 1)[-1]
        if wanted is None or schema in wanted:
            records.append(record)
    return records


def resolve_model(path: str):
    module_name, _, qualname = path.partition(":")
    target: Any = importlib.import_module(module_name)
    for part in qualname.split("."):
        target = getattr(target, part)
    return target


def decisions(value: Any, prefix: str = "") -> dict[str, Any]:
    """Every decision-like leaf (status, verdict, ...) by path, for field-level agreement."""
    found: dict[str, Any] = {}
    if isinstance(value, dict):
        ident = value.get("condition_id") or value.get("requirement_id") or value.get("id")
        for key, item in value.items():
            path = f"{prefix}{ident}/{key}" if ident else f"{prefix}{key}"
            if key in DECISION_KEYS and not isinstance(item, (dict, list)):
                found[path] = item
            else:
                found.update(decisions(item, path + "."))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.update(decisions(item, f"{prefix}{index}."))
    return found


def compare(recorded: Any, fresh: Any) -> dict[str, Any]:
    old, new = decisions(recorded), decisions(fresh)
    shared = sorted(set(old) & set(new))
    agree = sum(1 for key in shared if old[key] == new[key])
    return {
        "exact": recorded == fresh,
        "decisions": len(shared),
        "decisions_agree": agree,
        "decisions_missing": len(set(old) ^ set(new)),
        "changes": [{"path": key, "recorded": old[key], "fresh": new[key]} for key in shared if old[key] != new[key]],
    }


def check(records: list[dict[str, Any]]) -> dict[str, Any]:
    from app.services.llm_recording import request_key

    problems = []
    for record in records:
        request = record["request"]
        model_cls = resolve_model(request["response_model"])
        key = request_key(
            prompt=request["prompt"], response_model=model_cls, model=request["model"],
            system_instruction=request["system_instruction"], thinking_level=request["thinking_level"],
            max_output_tokens=request["max_output_tokens"], temperature=request["temperature"],
        )
        if key != record["key"]:
            problems.append({"key": record["key"], "problem": "key does not recompute (schema or request changed)"})
        elif record["response"] is None:
            problems.append({"key": record["key"], "problem": "no recorded answer"})
        else:
            try:
                model_cls.model_validate(record["response"])
            except Exception as ex:
                problems.append({"key": record["key"], "problem": f"answer no longer parses: {ex}"[:300]})
    return {"mode": "check", "records": len(records), "replay_exact": not problems, "problems": problems}


def seed_cache(records: list[dict[str, Any]]) -> dict[str, Any]:
    from app.services import llm_response_cache, pipeline_cache

    seeded = 0
    for record in records:
        if record["response"] is not None:
            pipeline_cache.set_json(llm_response_cache.NAMESPACE, record["key"], record["response"])
            seeded += 1
    return {"mode": "seed-cache", "records": len(records), "seeded": seeded}


def load_transform(spec: Optional[str]) -> Callable[[str, str, Optional[str]], tuple[str, Optional[str]]]:
    """``module:function`` taking (schema, prompt, system) and returning (prompt, system)."""
    if not spec:
        return lambda schema, prompt, system: (prompt, system)
    module_name, _, name = spec.partition(":")
    return getattr(importlib.import_module(module_name), name)


async def rerun(records, *, model: Optional[str], thinking_level: Optional[str], transform, concurrency: int) -> dict[str, Any]:
    from app.services import call_ledger, llm_client

    semaphore = asyncio.Semaphore(max(1, concurrency))
    rows: list[dict[str, Any]] = []

    async def one(record):
        request = record["request"]
        model_cls = resolve_model(request["response_model"])
        prompt, system = transform(model_cls.__name__, request["prompt"], request["system_instruction"])
        async with semaphore:
            fresh = await llm_client.generate_structured(
                prompt=prompt, response_model=model_cls,
                model=model or request["model"], system_instruction=system,
                thinking_level=thinking_level if thinking_level is not None else request["thinking_level"],
                max_output_tokens=request["max_output_tokens"], allow_model_fallback=False,
            )
        fresh_json = fresh.model_dump(mode="json") if fresh is not None else None
        row = {"key": record["key"], "schema": model_cls.__name__, "failed": fresh is None}
        if fresh is not None and record["response"] is not None:
            row.update(compare(record["response"], fresh_json))
        rows.append(row)

    with call_ledger.recording() as calls:
        await asyncio.gather(*(one(record) for record in records))
    scored = [row for row in rows if "decisions" in row]
    decisions_total = sum(row["decisions"] for row in scored)
    by_schema = Counter(row["schema"] for row in rows)
    return {
        "mode": "rerun",
        "model": model,
        "records": len(records),
        "by_schema": dict(by_schema),
        "failed_calls": sum(row["failed"] for row in rows),
        "exact_answers": sum(1 for row in scored if row["exact"]),
        "decision_agreement": round(sum(row["decisions_agree"] for row in scored) / decisions_total, 4) if decisions_total else None,
        "decisions": decisions_total,
        "call_ledger": call_ledger.summarize(calls),
        "rows": rows,
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", nargs="?", choices=("check", "seed-cache", "rerun"), default="check")
    parser.add_argument("--record-dir", required=True, help="the LLM_RECORD_DIR the run was recorded into")
    parser.add_argument("--run", help="audit_run_id to select (default: every recorded call)")
    parser.add_argument("--schemas", default=",".join(VERIFICATION_SCHEMAS),
                        help="comma-separated schema names, or 'all' (default: verification schemas)")
    parser.add_argument("--model", help="rerun: send to this model instead of the recorded one")
    parser.add_argument("--thinking-level", help="rerun: override the recorded thinking level")
    parser.add_argument("--prompt-transform", help="rerun: module:function rewriting (schema, prompt, system)")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--out", help="write the JSON report here")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = parse_args(argv)
    schemas = "all" if args.schemas == "all" else [name.strip() for name in args.schemas.split(",") if name.strip()]
    records = load_records(Path(args.record_dir), args.run, schemas)
    if args.mode == "check":
        report = check(records)
    elif args.mode == "seed-cache":
        report = seed_cache(records)
    else:
        from app.config import settings

        settings.LLM_RESPONSE_CACHE_ENABLED = False
        settings.LLM_RECORD_DIR = ""  # never overwrite the recording being compared against
        report = asyncio.run(rerun(
            records, model=args.model, thinking_level=args.thinking_level,
            transform=load_transform(args.prompt_transform), concurrency=args.concurrency,
        ))
    text = json.dumps(report, indent=1, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text if args.mode != "rerun" else json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=1))
    return 0 if report.get("replay_exact", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
