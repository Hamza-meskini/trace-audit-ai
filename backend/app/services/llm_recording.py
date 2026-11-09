"""EVA-03: opt-in recording of raw model requests and answers.

Saved runs held no prompts or responses, so a prompt or model change could not
be compared on identical inputs. With LLM_RECORD_DIR set, every structured call
is written as one JSON file named by the full sha256 of its request (the same
key as the layer-1 response cache), and each run appends the keys it used to
``runs/<audit_run_id>.jsonl``. evaluation/replay_recorded_calls.py re-runs
those inputs against a new prompt or model, checks that they replay exactly,
or seeds the response cache so a re-audit replays with zero paid calls.

Recordings hold customer text. Recording is off by default and refuses any
directory inside the git checkout, so it cannot be committed by accident.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional, Type

from pydantic import BaseModel

from app.config import settings
from app.services.pipeline_cache import stable_key

logger = logging.getLogger("traceaudit.llm_recording")
REPO_ROOT = Path(__file__).resolve().parents[3]
RECORD_FORMAT = "llm-recording-v1"
_lock = threading.Lock()
_refused: set[str] = set()


def request_key(
    *,
    prompt: str,
    response_model: Type[BaseModel],
    model: Optional[str],
    system_instruction: Optional[str],
    thinking_level: Optional[str],
    max_output_tokens: Optional[int],
    temperature: float,
) -> str:
    """Full sha256 of everything that determines a structured call's answer."""
    schema = json.dumps(response_model.model_json_schema(), sort_keys=True)
    return stable_key({
        "model": model or settings.LLM_MODEL,
        "schema": schema,
        "prompt": prompt,
        "system": system_instruction,
        "thinking": thinking_level,
        "max_tokens": max_output_tokens,
        "temperature": temperature,
    })


def record_dir() -> Optional[Path]:
    """The recording directory, or None when recording is off or the path is unsafe."""
    raw = str(getattr(settings, "LLM_RECORD_DIR", "") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser().resolve()
    try:
        path.relative_to(REPO_ROOT)
    except ValueError:
        return path
    if raw not in _refused:
        _refused.add(raw)
        logger.error(
            "LLM_RECORD_DIR %s is inside the git checkout; recordings hold customer text, "
            "so nothing is recorded. Choose a directory outside the repository.", path,
        )
    return None


def model_path(response_model: Type[BaseModel]) -> str:
    return f"{response_model.__module__}:{response_model.__qualname__}"


def record(
    *,
    prompt: str,
    response_model: Type[BaseModel],
    model: Optional[str],
    system_instruction: Optional[str],
    thinking_level: Optional[str],
    max_output_tokens: Optional[int],
    temperature: float,
    result: Optional[BaseModel],
    raw_response: Optional[str] = None,
    source: str = "provider",
    served_model: Optional[str] = None,
) -> Optional[str]:
    """Write one call; return its key, or None when recording is off. Never raises."""
    root = record_dir()
    if root is None:
        return None
    try:
        from app.services.observability import current_observation_context

        key = request_key(
            prompt=prompt, response_model=response_model, model=model,
            system_instruction=system_instruction, thinking_level=thinking_level,
            max_output_tokens=max_output_tokens, temperature=temperature,
        )
        context = current_observation_context()
        run_id = str(context.get("audit_run_id") or f"session-{os.getpid()}")
        call_file = root / "calls" / key[:2] / f"{key}.json"
        payload = {
            "format": RECORD_FORMAT,
            "key": key,
            "request": {
                "prompt": prompt,
                "system_instruction": system_instruction,
                "response_model": model_path(response_model),
                "model": model or settings.LLM_MODEL,
                "thinking_level": thinking_level,
                "max_output_tokens": max_output_tokens,
                "temperature": temperature,
            },
            "response": result.model_dump(mode="json") if result is not None else None,
            "raw_response": raw_response,
            "served_model": served_model,
            "recorded_at": round(time.time(), 3),
        }
        index_line = json.dumps({
            "key": key, "schema": response_model.__name__, "model": payload["request"]["model"],
            "status": "ok" if result is not None else "failed", "source": source,
            "at": payload["recorded_at"],
        }, ensure_ascii=False)
        with _lock:
            call_file.parent.mkdir(parents=True, exist_ok=True)
            # A failed answer never overwrites a good one recorded for the same request.
            if result is not None or not call_file.exists():
                call_file.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            runs = root / "runs"
            runs.mkdir(parents=True, exist_ok=True)
            with (runs / f"{run_id}.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(index_line + "\n")
        return key
    except Exception:  # recording must never stop an audit
        logger.warning("LLM call recording failed", exc_info=True)
        return None
