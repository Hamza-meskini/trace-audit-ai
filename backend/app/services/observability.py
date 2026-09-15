"""Lazy, failure-isolated MLflow tracing and benchmark metric logging."""

from __future__ import annotations

from contextlib import contextmanager
import logging
from typing import Any, Iterator

from app.config import settings


logger = logging.getLogger("traceaudit.observability")


def _mlflow() -> Any | None:
    if not settings.DATABRICKS_MLFLOW_TRACING_ENABLED:
        return None
    try:
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
        manager = mlflow.start_span(name=name, span_type=span_type, inputs=inputs)
        span = manager.__enter__()
        for key, value in (attributes or {}).items():
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
