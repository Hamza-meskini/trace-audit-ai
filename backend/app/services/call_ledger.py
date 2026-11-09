"""REL-05: a per-run ledger of model calls, and per-requirement provenance.

Tracing is off by default and the served model reached only the trace span, so
a run could not say which model actually answered or where it fell back. Every
logical provider call (and every response-cache hit) is appended here to each
open recording scope. Scopes nest: an audit opens one for the run and one per
requirement, so the same entry counts in both.

Only metadata is recorded (model, served model, status, latency, retries,
fallback). Prompt and response text never enter the ledger; recording them is
the separate, opt-in EVA-03 switch.
"""
from __future__ import annotations

import time
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Optional

_scopes: ContextVar[tuple[list[dict[str, Any]], ...]] = ContextVar("llm_call_ledger", default=())
_fallback_from: ContextVar[Optional[str]] = ContextVar("llm_fallback_from", default=None)


@contextmanager
def recording() -> Iterator[list[dict[str, Any]]]:
    """Open a ledger scope; calls made inside it (including child tasks) land in the list."""
    entries: list[dict[str, Any]] = []
    token = _scopes.set(_scopes.get() + (entries,))
    try:
        yield entries
    finally:
        _scopes.reset(token)


def current() -> list[dict[str, Any]]:
    """The innermost open scope's entries, or an empty list outside any scope."""
    scopes = _scopes.get()
    return scopes[-1] if scopes else []


@contextmanager
def fallback_from(primary: Optional[str]) -> Iterator[None]:
    """Mark calls inside the block as a fallback away from ``primary``."""
    token = _fallback_from.set(primary)
    try:
        yield
    finally:
        _fallback_from.reset(token)


def record(entry: dict[str, Any]) -> None:
    scopes = _scopes.get()
    if not scopes:
        return
    entry = {"at": round(time.time(), 3), **entry}
    primary = _fallback_from.get()
    if primary and "fallback_from" not in entry:
        entry["fallback_from"] = primary
    for entries in scopes:
        entries.append(entry)


def summarize(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts a reviewer can read: which models answered, failures, fallbacks, cache hits."""
    calls = [entry for entry in entries if entry.get("kind", "call") == "call"]
    hits = [entry for entry in entries if entry.get("kind") == "cache_hit"]
    served = Counter(str(entry.get("served_model") or entry.get("model")) for entry in calls if entry.get("status") == "ok")
    statuses = Counter(str(entry.get("status")) for entry in calls)
    fallbacks = [
        {"from": entry.get("fallback_from"), "to": entry.get("model"), "status": entry.get("status")}
        for entry in calls if entry.get("fallback_from")
    ]
    return {
        "calls": len(calls),
        "cache_hits": len(hits),
        "statuses": dict(sorted(statuses.items())),
        "requested_models": dict(sorted(Counter(str(entry.get("model")) for entry in calls).items())),
        "served_models": dict(sorted(served.items())),
        "served_model_differs": sum(
            1 for entry in calls
            if entry.get("served_model") and entry.get("model")
            and str(entry["served_model"]).lower() != str(entry["model"]).lower()
        ),
        "fallbacks": fallbacks,
        "rate_limit_retries": sum(int(entry.get("rate_limit_retries") or 0) for entry in calls),
        "latency_seconds": round(sum(float(entry.get("latency_seconds") or 0.0) for entry in calls), 3),
    }
