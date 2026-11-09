"""Run validity for live benchmarks (EVA-02).

A run that hit a failed model call or a heuristic fallback measures the outage,
not the system, so it must never be scored. The runner calls preflight_models
before any work and assert_run_valid before scoring; either raises and no
results file is written.
"""

from __future__ import annotations

from typing import Any, Iterable

from app.services.llm_client import generate_text

FAILED_DECISION_SOURCES = frozenset({"llm_error", "operational_failure"})


class RunInvalidError(RuntimeError):
    """The run hit failures that make its scores meaningless."""


async def preflight_models(models: Iterable[str]) -> None:
    """One short call per distinct model; any failure aborts before the run starts."""
    for model in dict.fromkeys(m for m in models if m):
        reply = await generate_text("Reply with exactly: OK", model=model)
        if not (reply or "").strip():
            raise RunInvalidError(f"Preflight call to {model} returned nothing; the run was not started.")


def _get(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def invalid_reasons(assessments: dict[str, Any], profiles: dict[str, Any] | None = None) -> list[str]:
    reasons = []
    for code, assessment in sorted(assessments.items()):
        source = (_get(assessment, "pipeline_diagnostics") or {}).get("decision_source")
        if source in FAILED_DECISION_SOURCES:
            reasons.append(f"{code}: verification decision_source={source}")
    for name, profile in sorted((profiles or {}).items()):
        if _get(profile, "classification_source") == "content_heuristic":
            reasons.append(f"{name}: document profile fell back to the keyword heuristic")
    return reasons


def assert_run_valid(assessments: dict[str, Any], profiles: dict[str, Any] | None = None) -> None:
    reasons = invalid_reasons(assessments, profiles)
    if reasons:
        shown = "; ".join(reasons[:10]) + (f" (+{len(reasons) - 10} more)" if len(reasons) > 10 else "")
        raise RunInvalidError(f"Run invalid, not scored: {shown}")
