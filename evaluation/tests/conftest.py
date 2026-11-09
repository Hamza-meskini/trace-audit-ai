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


@pytest.fixture(autouse=True)
def _no_live_readiness_probe(monkeypatch):
    """Tests never probe a live model before starting an audit (REL-03); its own tests opt in."""
    from app.config import settings
    from app.services import model_readiness

    monkeypatch.setattr(settings, "AUDIT_READINESS_CHECK", False)
    model_readiness.reset()
    yield
    model_readiness.reset()


@pytest.fixture(autouse=True)
def _no_llm_response_cache(monkeypatch):
    """Model answers are not cached across tests (ARC-01); its own tests opt in."""
    from app.config import settings

    monkeypatch.setattr(settings, "LLM_RESPONSE_CACHE_ENABLED", False)


@pytest.fixture(autouse=True)
def _isolated_provider_limits(monkeypatch):
    """REL-06 cooldowns are process-wide; EVA-03 recording must stay off in tests."""
    from app.config import settings
    from app.services import request_limits

    monkeypatch.setattr(settings, "LLM_RECORD_DIR", "")
    request_limits.reset_provider_limits()
    yield
    request_limits.reset_provider_limits()
