"""Shared test isolation."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))


@pytest.fixture(autouse=True)
def _reset_llm_breaker():
    """The model circuit breaker is process-wide; failures in one test must not leak into another."""
    from app.services.llm_client import LLM_BREAKER

    LLM_BREAKER.reset()
    yield
    LLM_BREAKER.reset()
