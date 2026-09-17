"""Lazy, failure-isolated MLflow tracing and benchmark metric logging."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import logging
import os
from typing import Any, Iterator
from urllib.parse import urlsplit
import uuid

from app.config import settings


logger = logging.getLogger("traceaudit.observability")

_TRACE_CONTEXT: ContextVar[dict[str, Any]] = ContextVar(
    "traceaudit_observability_context", default={}
)
_MLFLOW_UNSET = object()
_MLFLOW_CACHE: Any = _MLFLOW_UNSET


def new_correlation_id(prefix: str = "trace") -> str:
    """Return a human-searchable ID shared by app traces and provider logs."""
    return f"{prefix}-{uuid.uuid4()}"


@contextmanager
def observation_context(**values: Any) -> Iterator[dict[str, Any]]:
    """Propagate audit/requirement/stage metadata across async child calls."""
    merged = {**_TRACE_CONTEXT.get(), **{
        key: value for key, value in values.items() if value is not None
    }}
    token = _TRACE_CONTEXT.set(merged)
    try:
        yield merged
    finally:
        _TRACE_CONTEXT.reset(token)


def current_observation_context() -> dict[str, Any]:
    return dict(_TRACE_CONTEXT.get())


def traced_content(value: Any) -> dict[str, Any]:
    """Represent content safely while retaining a reproducible comparison key."""
    if isinstance(value, str):
        serialized = value
    else:
        try:
            serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
        except Exception:
            serialized = str(value)
    encoded = serialized.encode("utf-8", errors="replace")
    result: dict[str, Any] = {
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "characters": len(serialized),
        "bytes": len(encoded),
    }
    if settings.DATABRICKS_MLFLOW_CAPTURE_CONTENT:
        limit = max(0, int(settings.DATABRICKS_MLFLOW_MAX_CONTENT_CHARS))
        result["content"] = serialized[:limit]
        result["truncated"] = len(serialized) > limit
    return result


def set_span_outputs(span: Any | None, outputs: Any) -> None:
    """Best-effort output attachment compatible with multiple MLflow versions."""
    if span is None:
        return
    try:
        setter = getattr(span, "set_outputs", None)
        if callable(setter):
            setter(outputs)
    except Exception as exc:
        logger.warning("Could not attach outputs to MLflow span: %s", exc)


def set_span_attribute(span: Any | None, key: str, value: Any) -> None:
    if span is None:
        return
    try:
        span.set_attribute(key, value)
    except Exception as exc:
        logger.warning("Could not attach attribute %s to MLflow span: %s", key, exc)


def _mlflow() -> Any | None:
    global _MLFLOW_CACHE
    if not settings.DATABRICKS_MLFLOW_TRACING_ENABLED:
        return None
    if _MLFLOW_CACHE is not _MLFLOW_UNSET:
        return _MLFLOW_CACHE
    try:
        # Pydantic reads backend/.env without exporting it. MLflow's Databricks
        # client uses the standard Databricks environment variables, so bridge
        # the already-loaded application settings without logging credentials.
        parsed = urlsplit(settings.DATABRICKS_BASE_URL)
        if parsed.scheme and parsed.netloc:
            os.environ.setdefault("DATABRICKS_HOST", f"{parsed.scheme}://{parsed.netloc}")
        if settings.effective_databricks_token:
            os.environ.setdefault("DATABRICKS_TOKEN", settings.effective_databricks_token)
        os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
        import mlflow
    except ImportError:
        logger.warning("MLflow observability is enabled but mlflow is not installed")
        return None
    try:
        mlflow.set_tracking_uri("databricks")
        if settings.DATABRICKS_MLFLOW_EXPERIMENT:
            mlflow.set_experiment(settings.DATABRICKS_MLFLOW_EXPERIMENT)
    except Exception as exc:
        logger.warning("Could not configure Databricks MLflow tracking: %s", exc)
        return None
    _MLFLOW_CACHE = mlflow
    return mlflow


@contextmanager
def trace_span(
    name: str,
    *,
    span_type: str = "CHAIN",
    inputs: dict[str, Any] | None = None,
    attributes: dict[str, Any] | None = None,
) -> Iterator[Any | None]:
    """Create an MLflow span when enabled; never break the audit on telemetry."""
    mlflow = _mlflow()
    if mlflow is None:
        yield None
        return
    try:
        merged_attributes = {**current_observation_context(), **(attributes or {})}
        manager = mlflow.start_span(name=name, span_type=span_type)
        span = manager.__enter__()
        if inputs is not None:
            span.set_inputs(inputs)
        for key, value in merged_attributes.items():
            span.set_attribute(key, value)
    except Exception as exc:
        logger.warning("Could not start MLflow span %s: %s", name, exc)
        yield None
        return
    try:
        yield span
    except BaseException as exc:
        manager.__exit__(type(exc), exc, exc.__traceback__)
        raise
    else:
        try:
            manager.__exit__(None, None, None)
        except Exception as exc:
            logger.warning("Could not finish MLflow span %s: %s", name, exc)


def log_benchmark_metrics(
    *,
    run_name: str,
    metrics: dict[str, Any],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """Log flattened scalar evaluation metrics to a Databricks MLflow run."""
    mlflow = _mlflow()
    if mlflow is None:
        return {"enabled": bool(settings.DATABRICKS_MLFLOW_TRACING_ENABLED), "logged": False}

    flattened: dict[str, float] = {}

    def visit(prefix: str, value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                visit(f"{prefix}.{key}" if prefix else str(key), item)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            flattened[prefix] = float(value)

    visit("", metrics)
    try:
        with mlflow.start_run(run_name=run_name) as run:
            mlflow.log_params({key: str(value)[:500] for key, value in parameters.items()})
            if flattened:
                mlflow.log_metrics(flattened)
            return {
                "enabled": True,
                "logged": True,
                "run_id": run.info.run_id,
                "metrics_logged": len(flattened),
            }
    except Exception as exc:
        logger.warning("Could not log benchmark metrics to MLflow: %s", exc)
        return {"enabled": True, "logged": False, "error": str(exc)}
